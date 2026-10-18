# ui/panels/order_panel.py
# 주문 패널 — 기본 주문 + 분(分)단위 분할매수(DCA) + 손익 기반 추가매수 일시정지
#
# [기능]
#   - 기본 주문: 지정가/시장가, 매수/매도, F1/F2/F3 단축키
#   - DCA: 분 단위 간격으로 자동 분할 매수 (F4 시작 / F5 중지)
#   - 손익 기반 일시정지: 현재 포지션 손익 < 기준% 시 지정 시간(분)만큼 추가매수 중지

from __future__ import annotations
import queue
import logging
import time

from PyQt6.QtWidgets import (
    QLabel, QComboBox, QPushButton,
    QHBoxLayout, QVBoxLayout, QFormLayout,
    QFrame, QSizePolicy, QDoubleSpinBox, QSpinBox,
    QCheckBox, QGroupBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel

log = logging.getLogger(__name__)

# ── 색상 상수 ────────────────────────────────────────────────
_BG         = "#F5F5F5"
_BG_INPUT   = "#FFFFFF"
_BORDER     = "#CDD0D5"
_TEXT       = "#111827"
_LABEL      = "#555E6B"

_BTN_BUY  = ("background:#1565C0; color:#fff; font-weight:bold; font-size:14px;"
             "padding:8px; border:none; border-radius:4px;")
_BTN_SELL = ("background:#C62828; color:#fff; font-weight:bold; font-size:14px;"
             "padding:8px; border:none; border-radius:4px;")
_BTN_CANCEL = ("background:#E8ECF0; color:#555E6B; font-size:11px;"
               "padding:4px; border:1px solid #CDD0D5; border-radius:3px;")
_BTN_DCA_START = ("background:#2E7D32; color:#fff; font-weight:bold; font-size:12px;"
                  "padding:6px; border:none; border-radius:4px;")
_BTN_DCA_STOP  = ("background:#B71C1C; color:#fff; font-weight:bold; font-size:12px;"
                  "padding:6px; border:none; border-radius:4px;")

_INPUT_STYLE = (
    f"QComboBox, QDoubleSpinBox, QSpinBox {{"
    f"background:{_BG_INPUT}; color:{_TEXT}; border:1px solid {_BORDER};"
    f"border-radius:3px; padding:2px 4px; font-size:12px;}}"
)
_GROUP_STYLE = (
    "QGroupBox { font-size:11px; font-weight:bold; color:#1565C0;"
    " border:1px solid #BFDBFE; border-radius:4px;"
    " margin-top:8px; padding-top:6px; }"
    "QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px; }"
)
_PAUSE_GROUP_STYLE = (
    "QGroupBox { font-size:10px; color:#6B7280;"
    " border:1px solid #E5E7EB; border-radius:3px;"
    " margin-top:6px; padding-top:4px; }"
    "QGroupBox::title { subcontrol-origin:margin; left:6px; }"
)

# DCA 상태 라벨 스타일
_STS_WAIT   = "color:#6B7280; font-size:10px; background:#F3F4F6; border-radius:3px; padding:3px;"
_STS_RUN    = "color:#1565C0; font-size:10px; background:#DBEAFE; border-radius:3px; padding:3px;"
_STS_OK     = "color:#2E7D32; font-size:10px; background:#DCFCE7; border-radius:3px; padding:3px;"
_STS_PAUSE  = "color:#B45309; font-size:10px; background:#FEF3C7; border-radius:3px; padding:3px;"
_STS_ERR    = "color:#C62828; font-size:10px; background:#FEE2E2; border-radius:3px; padding:3px;"


class OrderPanel(BasePanel):
    PANEL_ID     = "order"
    TITLE        = "주문"
    DEFAULT_SIZE = (300, 660)

    def __init__(self, cmd_queue: queue.Queue, bus: DataBus, parent=None) -> None:
        super().__init__(bus, parent)
        self._cmd_queue  = cmd_queue
        self._last_price = 0.0
        self._symbol     = "AAPL"
        self._position: dict = {}   # {qty, avg_cost, pnl, pnl_pct}

        # DCA 런타임 상태
        self._dca_running     = False
        self._dca_count       = 0
        self._dca_last_ts     = 0.0   # 마지막 매수 timestamp (0 = 즉시 실행)
        self._dca_pause_until = 0.0   # 일시정지 해제 timestamp

        self.setStyleSheet(
            f"background:{_BG}; color:{_TEXT};"
            f"QLabel {{ color:{_LABEL}; font-size:11px; }}"
            + _INPUT_STYLE
        )
        self._build_ui()
        self._setup_subscriptions()

        # DCA 체크 타이머 (1초)
        self._dca_timer = QTimer(self)
        self._dca_timer.setInterval(1000)
        self._dca_timer.timeout.connect(self._dca_tick)
        self._dca_timer.start()

    # ════════════════════════════════════════════════════════
    # UI 구성
    # ════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        # ① 심볼 선택
        sym_row = QHBoxLayout()
        sym_row.addWidget(QLabel("종목"))
        self._sym_combo = QComboBox()
        self._sym_combo.setEditable(True)
        self._sym_combo.addItems(["AAPL", "SPY", "QQQ", "TSLA", "NVDA"])
        self._sym_combo.setCurrentText(self._symbol)
        self._sym_combo.currentTextChanged.connect(self._on_symbol_changed)
        sym_row.addWidget(self._sym_combo, 1)
        self._layout.addLayout(sym_row)

        # ② 현재가 표시
        self._price_lbl = QLabel("현재가: --")
        self._price_lbl.setStyleSheet(
            "color:#B45309; font-weight:bold; font-size:14px;")
        self._layout.addWidget(self._price_lbl)

        # ③ 포지션 손익 표시
        self._pnl_lbl = QLabel("포지션: 없음")
        self._pnl_lbl.setStyleSheet("color:#555E6B; font-size:11px;")
        self._pnl_lbl.setWordWrap(True)
        self._layout.addWidget(self._pnl_lbl)

        self._layout.addWidget(self._hline())

        # ④ 기본 주문 폼
        form = QFormLayout()
        form.setSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)

        self._order_type = QComboBox()
        self._order_type.addItems(["지정가 (LMT)", "시장가 (MKT)"])
        self._order_type.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("주문유형", self._order_type)

        self._price_spin = QDoubleSpinBox()
        self._price_spin.setRange(0.0, 999_999.0)
        self._price_spin.setDecimals(2)
        self._price_spin.setSingleStep(0.01)
        self._price_spin.setPrefix("$")
        form.addRow("가격", self._price_spin)

        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(1, 100_000)
        self._qty_spin.setValue(10)
        self._qty_spin.setSingleStep(10)
        form.addRow("수량", self._qty_spin)

        self._est_lbl = QLabel("$0.00")
        self._est_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._est_lbl.setStyleSheet("color:#111827; font-size:12px; font-weight:bold;")
        form.addRow("예상금액", self._est_lbl)

        self._layout.addLayout(form)
        self._price_spin.valueChanged.connect(self._update_est)
        self._qty_spin.valueChanged.connect(self._update_est)

        self._layout.addWidget(self._hline())

        # ⑤ 매수 / 매도 버튼
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._btn_buy = QPushButton("매 수  [F1]")
        self._btn_buy.setStyleSheet(_BTN_BUY)
        self._btn_buy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_buy.setMinimumHeight(44)
        self._btn_buy.clicked.connect(lambda: self._place_order("BUY"))

        self._btn_sell = QPushButton("매 도  [F2]")
        self._btn_sell.setStyleSheet(_BTN_SELL)
        self._btn_sell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_sell.setMinimumHeight(44)
        self._btn_sell.clicked.connect(lambda: self._place_order("SELL"))

        btn_row.addWidget(self._btn_buy)
        btn_row.addWidget(self._btn_sell)
        self._layout.addLayout(btn_row)

        # ⑥ 전체 주문 취소
        self._btn_cancel = QPushButton("전체 주문 취소  [F3]")
        self._btn_cancel.setStyleSheet(_BTN_CANCEL)
        self._btn_cancel.clicked.connect(self._cancel_all)
        self._layout.addWidget(self._btn_cancel)

        # ⑦ 상태 표시
        self._status_lbl = QLabel("")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet("color:#888; font-size:10px;")
        self._layout.addWidget(self._status_lbl)

        self._layout.addWidget(self._hline())

        # ⑧ 분할매수(DCA) 섹션
        self._build_dca_section()

        self._layout.addStretch()

    def _build_dca_section(self) -> None:
        grp = QGroupBox("분할매수 DCA  [F4 시작 / F5 중지]")
        grp.setStyleSheet(_GROUP_STYLE)
        lay = QVBoxLayout(grp)
        lay.setSpacing(6)
        lay.setContentsMargins(8, 14, 8, 8)

        # 기본 설정 폼
        form = QFormLayout()
        form.setSpacing(4)

        self._dca_interval = QSpinBox()
        self._dca_interval.setRange(1, 1440)
        self._dca_interval.setValue(5)
        self._dca_interval.setSuffix(" 분")
        self._dca_interval.setToolTip("매수 간격 (분 단위)")
        form.addRow("매수 간격", self._dca_interval)

        self._dca_qty = QSpinBox()
        self._dca_qty.setRange(1, 100_000)
        self._dca_qty.setValue(10)
        self._dca_qty.setSingleStep(10)
        form.addRow("1회 수량", self._dca_qty)

        self._dca_total = QSpinBox()
        self._dca_total.setRange(1, 999)
        self._dca_total.setValue(5)
        form.addRow("총  횟수", self._dca_total)

        lay.addLayout(form)

        # ── 손익 기반 일시정지 ──────────────────────────────
        pause_grp = QGroupBox("손익 기반 추가매수 일시정지")
        pause_grp.setStyleSheet(_PAUSE_GROUP_STYLE)
        pause_lay = QFormLayout(pause_grp)
        pause_lay.setSpacing(4)
        pause_lay.setContentsMargins(6, 10, 6, 6)

        self._dca_pause_chk = QCheckBox("활성화")
        self._dca_pause_chk.setStyleSheet("color:#374151; font-size:11px;")
        self._dca_pause_chk.setChecked(True)
        pause_lay.addRow("", self._dca_pause_chk)

        self._dca_pnl_threshold = QDoubleSpinBox()
        self._dca_pnl_threshold.setRange(-100.0, 0.0)
        self._dca_pnl_threshold.setValue(-2.0)
        self._dca_pnl_threshold.setSingleStep(0.5)
        self._dca_pnl_threshold.setSuffix(" %")
        self._dca_pnl_threshold.setToolTip(
            "현재 포지션 손익률이 이 값 이하로 떨어지면\n"
            "추가매수를 일시정지합니다.")
        pause_lay.addRow("손익 기준", self._dca_pnl_threshold)

        self._dca_pause_min = QSpinBox()
        self._dca_pause_min.setRange(1, 1440)
        self._dca_pause_min.setValue(30)
        self._dca_pause_min.setSuffix(" 분")
        self._dca_pause_min.setToolTip("손익 기준 초과 시 추가매수를 중지할 시간")
        pause_lay.addRow("중지 시간", self._dca_pause_min)

        lay.addWidget(pause_grp)

        # ── DCA 상태 표시 ───────────────────────────────────
        self._dca_status_lbl = QLabel("대기 중")
        self._dca_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dca_status_lbl.setStyleSheet(_STS_WAIT)
        lay.addWidget(self._dca_status_lbl)

        self._dca_progress_lbl = QLabel("")
        self._dca_progress_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dca_progress_lbl.setStyleSheet(
            "color:#374151; font-size:11px; font-weight:bold;")
        lay.addWidget(self._dca_progress_lbl)

        # ── 시작 / 중지 버튼 ────────────────────────────────
        dca_btn_row = QHBoxLayout()
        self._btn_dca_start = QPushButton("▶ DCA 시작")
        self._btn_dca_start.setStyleSheet(_BTN_DCA_START)
        self._btn_dca_start.clicked.connect(self._dca_start)

        self._btn_dca_stop = QPushButton("■ 중지")
        self._btn_dca_stop.setStyleSheet(_BTN_DCA_STOP)
        self._btn_dca_stop.setEnabled(False)
        self._btn_dca_stop.clicked.connect(lambda: self._dca_stop(finished=False))

        dca_btn_row.addWidget(self._btn_dca_start)
        dca_btn_row.addWidget(self._btn_dca_stop)
        lay.addLayout(dca_btn_row)

        self._layout.addWidget(grp)

    @staticmethod
    def _hline() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet(f"color:{_BORDER}; background:{_BORDER}; max-height:1px;")
        return f

    # ════════════════════════════════════════════════════════
    # DataBus 구독
    # ════════════════════════════════════════════════════════
    def _setup_subscriptions(self) -> None:
        self._bus.subscribe(
            Topics.stock_price(self._symbol), self,
            lambda d: self._invoke_main("_on_price", d))
        self._bus.subscribe(
            Topics.portfolio_position(self._symbol), self,
            lambda d: self._invoke_main("_on_position", d))
        self._bus.subscribe(
            Topics.WATCHLIST_SELECT, self,
            lambda d: self._invoke_main("_on_watchlist_select", d))

    def _update_symbol_subscriptions(self, old_sym: str, new_sym: str) -> None:
        self._bus.unsubscribe(Topics.stock_price(old_sym), self)
        self._bus.unsubscribe(Topics.portfolio_position(old_sym), self)
        self._bus.subscribe(
            Topics.stock_price(new_sym), self,
            lambda d: self._invoke_main("_on_price", d))
        self._bus.subscribe(
            Topics.portfolio_position(new_sym), self,
            lambda d: self._invoke_main("_on_position", d))

    # ════════════════════════════════════════════════════════
    # DCA 엔진
    # ════════════════════════════════════════════════════════
    def _dca_tick(self) -> None:
        """1초마다 호출 — 카운트다운 / 매수 실행"""
        if not self._dca_running:
            return

        now   = time.time()
        total = self._dca_total.value()

        # 완료 체크
        if self._dca_count >= total:
            self._dca_stop(finished=True)
            return

        # ── 일시정지 체크 ──────────────────────────────────
        if self._dca_pause_until > now:
            remain = int(self._dca_pause_until - now)
            self._dca_status_lbl.setStyleSheet(_STS_PAUSE)
            self._dca_status_lbl.setText(
                f"⏸ 일시정지 — {remain // 60:02d}:{remain % 60:02d} 후 재개")
            return

        # ── 다음 매수까지 남은 시간 ────────────────────────
        interval_s  = self._dca_interval.value() * 60
        elapsed     = now - self._dca_last_ts
        remaining_s = max(0.0, interval_s - elapsed)

        if remaining_s > 0:
            mins, secs = divmod(int(remaining_s), 60)
            self._dca_status_lbl.setStyleSheet(_STS_RUN)
            self._dca_status_lbl.setText(f"⏳ 다음 매수: {mins:02d}:{secs:02d}")
            return

        # ── 매수 실행 ──────────────────────────────────────
        self._dca_execute(now)

    def _dca_execute(self, now: float) -> None:
        """DCA 1회 실행 (일시정지 조건 선체크)"""
        # 손익 기반 일시정지 판정
        if self._dca_pause_chk.isChecked() and self._position:
            pnl_pct   = self._position.get("pnl_pct", 0.0)
            threshold = self._dca_pnl_threshold.value()
            if pnl_pct < threshold:
                pause_min            = self._dca_pause_min.value()
                self._dca_pause_until = now + pause_min * 60
                log.warning(
                    "DCA pause triggered: pnl_pct=%.2f%% < threshold=%.2f%% → %dmin",
                    pnl_pct, threshold, pause_min)
                self._dca_status_lbl.setStyleSheet(_STS_PAUSE)
                self._dca_status_lbl.setText(
                    f"⏸ 손익 {pnl_pct:+.1f}% < {threshold:.1f}% → "
                    f"{pause_min}분 중지")
                return

        symbol     = self._sym_combo.currentText().strip().upper()
        qty        = self._dca_qty.value()
        is_mkt     = self._order_type.currentIndex() == 1
        price      = 0.0 if is_mkt else self._price_spin.value()
        order_type = "MKT" if is_mkt else "LMT"

        cmd = {
            "action":     "place_order",
            "symbol":     symbol,
            "side":       "BUY",
            "qty":        qty,
            "price":      price,
            "order_type": order_type,
            "dca":        True,
            "dca_seq":    self._dca_count + 1,
            "dca_total":  self._dca_total.value(),
        }
        self._cmd_queue.put(cmd)

        self._dca_count   += 1
        self._dca_last_ts  = now
        total = self._dca_total.value()

        self._dca_progress_lbl.setText(f"진행: {self._dca_count} / {total}")
        self._dca_status_lbl.setStyleSheet(_STS_OK)
        self._dca_status_lbl.setText(
            f"✅ {self._dca_count}회 매수 — {symbol} {qty}주 "
            f"@ {'시장가' if is_mkt else f'${price:.2f}'}")
        log.info("DCA exec %d/%d: %s BUY %d @ %s",
                 self._dca_count, total, symbol, qty, order_type)

    def _dca_start(self) -> None:
        if self._dca_running:
            return
        self._dca_running     = True
        self._dca_count       = 0
        self._dca_last_ts     = 0.0   # 첫 회: 즉시 실행
        self._dca_pause_until = 0.0

        self._btn_dca_start.setEnabled(False)
        self._btn_dca_stop.setEnabled(True)
        self._dca_progress_lbl.setText(f"진행: 0 / {self._dca_total.value()}")
        self._dca_status_lbl.setStyleSheet(_STS_RUN)
        self._dca_status_lbl.setText("▶ DCA 실행 중 — 첫 매수 대기...")
        log.info("DCA started: interval=%dmin qty=%d total=%d",
                 self._dca_interval.value(), self._dca_qty.value(), self._dca_total.value())

    def _dca_stop(self, *, finished: bool = False) -> None:
        self._dca_running = False
        self._btn_dca_start.setEnabled(True)
        self._btn_dca_stop.setEnabled(False)
        if finished:
            self._dca_status_lbl.setStyleSheet(_STS_OK)
            self._dca_status_lbl.setText(
                f"✅ DCA 완료 — 총 {self._dca_count}회 매수")
        else:
            self._dca_status_lbl.setStyleSheet(_STS_WAIT)
            self._dca_status_lbl.setText(
                f"■ DCA 중지 ({self._dca_count}회 완료)")
        log.info("DCA stopped: finished=%s count=%d", finished, self._dca_count)

    # ════════════════════════════════════════════════════════
    # 이벤트 핸들러
    # ════════════════════════════════════════════════════════
    def _on_symbol_changed(self, sym: str) -> None:
        new_sym = sym.strip().upper()
        if not new_sym or new_sym == self._symbol:
            return
        old_sym      = self._symbol
        self._symbol = new_sym
        self._update_symbol_subscriptions(old_sym, new_sym)
        self._price_lbl.setText("현재가: --")
        self._pnl_lbl.setText("포지션: 없음")
        self._pnl_lbl.setStyleSheet("color:#555E6B; font-size:11px;")
        self._position   = {}
        self._last_price = 0.0

    def _on_type_changed(self, idx: int) -> None:
        self._price_spin.setEnabled(idx == 0)

    def _update_est(self) -> None:
        est = self._price_spin.value() * self._qty_spin.value()
        self._est_lbl.setText(f"${est:,.2f}")

    @pyqtSlot(object)
    def _on_price(self, data: dict) -> None:
        price = data.get("price", 0.0)
        self._last_price = price
        self._price_lbl.setText(f"현재가: ${price:,.2f}")
        if self._price_spin.value() == 0.0:
            self._price_spin.setValue(price)

    @pyqtSlot(object)
    def _on_position(self, data: dict) -> None:
        """
        포지션 업데이트 — 손익 표시 + DCA 일시정지 실시간 체크.
        데이터 형식: {qty, avg_cost, pnl, pnl_pct}
        """
        self._position = data
        qty     = data.get("qty", 0)
        avg_cost = data.get("avg_cost", 0.0)
        pnl      = data.get("pnl", 0.0)
        pnl_pct  = data.get("pnl_pct", 0.0)

        if qty == 0:
            self._pnl_lbl.setText("포지션: 없음")
            self._pnl_lbl.setStyleSheet("color:#555E6B; font-size:11px;")
            return

        color = "#2E7D32" if pnl >= 0 else "#C62828"
        sign  = "+" if pnl >= 0 else ""
        self._pnl_lbl.setText(
            f"보유 {qty}주  |  평균 ${avg_cost:.2f}  |  "
            f"손익 {sign}{pnl:.2f} ({sign}{pnl_pct:.2f}%)"
        )
        self._pnl_lbl.setStyleSheet(
            f"color:{color}; font-size:11px; font-weight:bold;")

        # DCA 실행 중이고 일시정지 조건 체크 (아직 일시정지 중이 아닐 때만)
        if (self._dca_running
                and self._dca_pause_chk.isChecked()
                and self._dca_pause_until <= time.time()
                and pnl_pct < self._dca_pnl_threshold.value()):
            pause_min            = self._dca_pause_min.value()
            self._dca_pause_until = time.time() + pause_min * 60
            log.warning(
                "DCA real-time pause: pnl_pct=%.2f%% → %dmin",
                pnl_pct, pause_min)

    @pyqtSlot(object)
    def _on_watchlist_select(self, data: dict) -> None:
        sym = data.get("symbol", "").upper()
        if not sym:
            return
        idx = self._sym_combo.findText(sym)
        if idx < 0:
            self._sym_combo.addItem(sym)
            idx = self._sym_combo.count() - 1
        self._sym_combo.setCurrentIndex(idx)
        self._price_spin.setValue(0.0)
        self._price_lbl.setText("현재가: --")

    # ════════════════════════════════════════════════════════
    # 주문 발송
    # ════════════════════════════════════════════════════════
    def _place_order(self, side: str) -> None:
        symbol     = self._sym_combo.currentText().strip().upper()
        qty        = self._qty_spin.value()
        is_market  = self._order_type.currentIndex() == 1
        price      = 0.0 if is_market else self._price_spin.value()
        order_type = "MKT" if is_market else "LMT"

        if not symbol:
            self._show_status("❌ 종목을 입력하세요", error=True)
            return
        if not is_market and price <= 0:
            self._show_status("❌ 가격을 입력하세요", error=True)
            return

        cmd = {
            "action":     "place_order",
            "symbol":     symbol,
            "side":       side,
            "qty":        qty,
            "price":      price,
            "order_type": order_type,
        }
        self._cmd_queue.put(cmd)
        log.info("Order sent: %s", cmd)
        side_kr = "매수" if side == "BUY" else "매도"
        self._show_status(
            f"✅ {side_kr} {symbol} {qty}주 @ "
            f"{'시장가' if is_market else f'${price:.2f}'}")

    def _cancel_all(self) -> None:
        self._cmd_queue.put({"action": "cancel_all"})
        self._show_status("전체 취소 요청")

    def _show_status(self, msg: str, *, error: bool = False) -> None:
        color = "#FF6B6B" if error else "#2E7D32"
        self._status_lbl.setStyleSheet(f"color:{color}; font-size:10px;")
        self._status_lbl.setText(msg)

    # ════════════════════════════════════════════════════════
    # 단축키
    # ════════════════════════════════════════════════════════
    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [
            ("F1", lambda: self._place_order("BUY")),   # 매수
            ("F2", lambda: self._place_order("SELL")),  # 매도
            ("F3", self._cancel_all),                    # 전체취소
            ("F4", self._dca_start),                     # DCA 시작
            ("F5", lambda: self._dca_stop(finished=False)),  # DCA 중지
        ]

    # ════════════════════════════════════════════════════════
    # 종료
    # ════════════════════════════════════════════════════════
    def closeEvent(self, event) -> None:
        if self._dca_running:
            self._dca_stop(finished=False)
        self._dca_timer.stop()
        super().closeEvent(event)
