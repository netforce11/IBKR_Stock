# ui/panels/scanner_panel.py
# 마켓 스캐너 패널
#
# [기능]
#   - IBKR TWS 스캐너 코드 선택 + 필터(가격/거래량/시가총액/거래소)
#   - 조회 → 결과 테이블 (순위/종목/현재가/등락률/거래량/거래대금/시가총액)
#   - 결과 자동 저장 (data/scanner/YYYYMMDD/HHMM_CODE.json)
#   - 과거 파일 불러오기 (날짜 선택 → 파일 선택 → 로드)
#   - 종목 더블클릭 → WATCHLIST_SELECT 발행 (호가창/차트 연동)
#   - 종목 우클릭 → 관심종목 추가

from __future__ import annotations

import queue
import logging
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QLabel, QComboBox, QPushButton,
    QHBoxLayout, QVBoxLayout, QFormLayout,
    QFrame, QGroupBox, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDoubleSpinBox, QSpinBox, QCheckBox,
    QListWidget, QListWidgetItem,
    QAbstractItemView, QMenu, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot, QPoint
from PyQt6.QtGui import QColor, QFont

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel

log = logging.getLogger(__name__)

# ── 스타일 상수 ───────────────────────────────────────────
_BG       = "#F5F5F5"
_BG_TBL   = "#FFFFFF"
_BORDER   = "#CDD0D5"
_TEXT     = "#111827"
_HEAD_BG  = "#E8ECF0"

_BTN_SCAN = ("background:#1565C0; color:#fff; font-weight:bold;"
             "padding:6px 16px; border:none; border-radius:4px;")
_BTN_SAVE = ("background:#2E7D32; color:#fff; font-weight:bold;"
             "padding:6px 12px; border:none; border-radius:4px;")
_BTN_LOAD = ("background:#5C6BC0; color:#fff; font-weight:bold;"
             "padding:6px 12px; border:none; border-radius:4px;")
_BTN_SM   = ("background:#E8ECF0; color:#374151; font-size:11px;"
             "padding:4px 8px; border:1px solid #CDD0D5; border-radius:3px;")

_INPUT_STYLE = (
    "QComboBox, QDoubleSpinBox, QSpinBox {"
    "background:#fff; color:#111827; border:1px solid #CDD0D5;"
    "border-radius:3px; padding:6px 4px; font-size:13px; min-height:28px;}"
)
_GROUP_STYLE = (
    "QGroupBox { font-size:11px; font-weight:bold; color:#1565C0;"
    " border:1px solid #BFDBFE; border-radius:4px;"
    " margin-top:8px; padding-top:6px; }"
    "QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px; }"
)

# 스캔 코드 정의 (label, code)
SCAN_CODES = [
    ("등락률 상위 (상승)",        "TOP_PERC_GAIN"),
    ("등락률 상위 (하락)",        "TOP_PERC_LOSE"),
    ("거래량 상위",               "MOST_ACTIVE"),
    ("거래대금 상위 (USD)",        "MOST_ACTIVE_USD"),
    ("갭 상승 (시가 기준)",        "TOP_OPEN_PERC_GAIN"),
    ("갭 하락 (시가 기준)",        "TOP_OPEN_PERC_LOSE"),
    ("13주 신고가 돌파",           "HIGH_VS_13W_HL"),
    ("13주 신저가 하향",           "LOW_VS_13W_HL"),
    ("52주 신고가",                "HIGH_OPT_VOLUME"),
    ("거래량 급증 (전일 대비)",    "HOT_BY_VOLUME"),
]

# 테이블 컬럼 정의
_COLS = [
    ("순위",      50,  Qt.AlignmentFlag.AlignCenter),
    ("종목",      70,  Qt.AlignmentFlag.AlignLeft),
    ("현재가",    85,  Qt.AlignmentFlag.AlignRight),
    ("등락",      75,  Qt.AlignmentFlag.AlignRight),
    ("등락률",    75,  Qt.AlignmentFlag.AlignRight),
    ("거래량",    95,  Qt.AlignmentFlag.AlignRight),
    ("거래량비",  80,  Qt.AlignmentFlag.AlignRight),
    ("시가총액",  100, Qt.AlignmentFlag.AlignRight),
    ("거래소",    70,  Qt.AlignmentFlag.AlignCenter),
]


def _fmt_vol(v: float) -> str:
    if v >= 1e9:   return f"{v/1e9:.1f}B"
    if v >= 1e6:   return f"{v/1e6:.1f}M"
    if v >= 1e3:   return f"{v/1e3:.0f}K"
    return str(int(v))

def _fmt_cap(v: float) -> str:
    if v >= 1e12:  return f"{v/1e12:.2f}T"
    if v >= 1e9:   return f"{v/1e9:.1f}B"
    if v >= 1e6:   return f"{v/1e6:.0f}M"
    return f"{v:.0f}"


class ScannerPanel(BasePanel):
    PANEL_ID     = "scanner"
    TITLE        = "마켓 스캐너"
    DEFAULT_SIZE = (820, 580)

    def __init__(self, cmd_queue: queue.Queue, bus: DataBus,
                 broker, storage, parent=None) -> None:
        super().__init__(bus, parent)
        self._cmd_queue = cmd_queue
        self._broker    = broker
        self._storage   = storage   # ScannerStorage 인스턴스
        self._req_id    = 9000      # 스캐너 전용 req_id 시작값
        self._last_result: dict = {}

        # 자동 조회 타이머
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._do_scan)

        # 조회 버튼 타임아웃 복구 (응답 없을 경우 15초 후 버튼 재활성)
        self._scan_timeout = QTimer(self)
        self._scan_timeout.setSingleShot(True)
        self._scan_timeout.setInterval(15_000)
        self._scan_timeout.timeout.connect(self._on_scan_timeout)

        self.setStyleSheet(
            f"background:{_BG}; color:{_TEXT};" + _INPUT_STYLE)
        self._build_ui()
        self._setup_subscriptions()

    # ════════════════════════════════════════════════════════
    # UI 구성
    # ════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 왼쪽: 조회 설정 ──────────────────────────────────
        splitter.addWidget(self._build_left_panel())
        # ── 오른쪽: 결과 테이블 ──────────────────────────────
        splitter.addWidget(self._build_right_panel())

        splitter.setSizes([240, 580])
        splitter.setHandleWidth(4)
        self._layout.addWidget(splitter)

        # 하단 상태 표시
        self._status_lbl = QLabel("조회 대기 중")
        self._status_lbl.setStyleSheet(
            "color:#555E6B; font-size:10px; padding:2px;")
        self._layout.addWidget(self._status_lbl)

    def _build_left_panel(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background:{_BG}; border-right:1px solid {_BORDER}; }}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # ── 조회 설정 그룹 ────────────────────────────────────
        grp_scan = QGroupBox("조회 설정")
        grp_scan.setStyleSheet(_GROUP_STYLE)
        form = QFormLayout(grp_scan)
        form.setSpacing(5)
        form.setContentsMargins(8, 14, 8, 8)

        self._scan_combo = QComboBox()
        for label, code in SCAN_CODES:
            self._scan_combo.addItem(label, code)
        form.addRow("스캔 종류", self._scan_combo)

        self._market_combo = QComboBox()
        for lbl, val in [("미국 주식 (STK)", "STK.US.MAJOR"),
                          ("미국 ETF",        "ETF.US"),
                          ("나스닥",           "STK.NASDAQ"),
                          ("NYSE",             "STK.NYSE")]:
            self._market_combo.addItem(lbl, val)
        form.addRow("시장", self._market_combo)

        lay.addWidget(grp_scan)

        # ── 필터 그룹 ──────────────────────────────────────────
        grp_flt = QGroupBox("필터")
        grp_flt.setStyleSheet(_GROUP_STYLE)
        flt_form = QFormLayout(grp_flt)
        flt_form.setSpacing(4)
        flt_form.setContentsMargins(8, 14, 8, 8)

        self._min_price = QDoubleSpinBox()
        self._min_price.setRange(0, 99999)
        self._min_price.setValue(100.0)   # 기본 $100
        self._min_price.setPrefix("$")
        self._min_price.setMinimumHeight(32)
        flt_form.addRow("최소 가격", self._min_price)

        self._max_price = QDoubleSpinBox()
        self._max_price.setRange(0, 99999)
        self._max_price.setValue(1000.0)  # 기본 $1000
        self._max_price.setPrefix("$")
        self._max_price.setToolTip("0 = 제한 없음")
        self._max_price.setMinimumHeight(32)
        flt_form.addRow("최대 가격", self._max_price)

        self._min_vol = QSpinBox()
        self._min_vol.setRange(0, 999_999_999)
        self._min_vol.setValue(500_000)
        self._min_vol.setSingleStep(100_000)
        self._min_vol.setMinimumHeight(32)
        flt_form.addRow("최소 거래량", self._min_vol)

        lay.addWidget(grp_flt)

        # ── 자동 저장 옵션 ────────────────────────────────────
        self._auto_save_chk = QCheckBox("조회 후 자동 저장")
        self._auto_save_chk.setChecked(True)
        self._auto_save_chk.setStyleSheet("font-size:11px;")
        lay.addWidget(self._auto_save_chk)

        # ── 자동 조회 옵션 ────────────────────────────────────
        auto_row = QHBoxLayout()
        self._auto_refresh_chk = QCheckBox("자동 조회")
        self._auto_refresh_chk.setStyleSheet("font-size:11px;")
        self._auto_refresh_chk.toggled.connect(self._on_auto_refresh_toggle)
        auto_row.addWidget(self._auto_refresh_chk)

        self._interval_combo = QComboBox()
        for lbl, ms in [("10초", 10_000), ("30초", 30_000),
                         ("1분",  60_000), ("5분",  300_000), ("10분", 600_000)]:
            self._interval_combo.addItem(lbl, ms)
        self._interval_combo.setCurrentIndex(1)   # 기본: 30초
        self._interval_combo.setStyleSheet(
            "QComboBox { font-size:11px; padding:1px 4px; }")
        self._interval_combo.currentIndexChanged.connect(
            self._on_interval_changed)
        auto_row.addWidget(self._interval_combo)

        # 다음 조회까지 카운트다운
        self._countdown_lbl = QLabel("")
        self._countdown_lbl.setStyleSheet("color:#888; font-size:10px;")
        auto_row.addWidget(self._countdown_lbl)
        auto_row.addStretch()
        lay.addLayout(auto_row)

        # 카운트다운 표시용 1초 타이머
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._countdown_remaining = 0

        # ── 조회 버튼 ──────────────────────────────────────────
        self._btn_scan = QPushButton("조회")
        self._btn_scan.setStyleSheet(_BTN_SCAN)
        self._btn_scan.setMinimumHeight(36)
        self._btn_scan.clicked.connect(self._do_scan)
        lay.addWidget(self._btn_scan)

        lay.addWidget(self._hline())

        # ── 수동 저장 ──────────────────────────────────────────
        self._btn_save = QPushButton("💾 현재 결과 저장")
        self._btn_save.setStyleSheet(_BTN_SAVE)
        self._btn_save.clicked.connect(self._do_save)
        self._btn_save.setEnabled(False)
        lay.addWidget(self._btn_save)

        lay.addWidget(self._hline())

        # ── 과거 파일 불러오기 ────────────────────────────────
        lay.addWidget(QLabel("📂 과거 데이터 불러오기"))

        self._date_combo = QComboBox()
        self._date_combo.addItem("날짜 선택")
        self._date_combo.currentTextChanged.connect(self._on_date_changed)
        lay.addWidget(self._date_combo)

        self._file_list = QListWidget()
        self._file_list.setMaximumHeight(120)
        self._file_list.setStyleSheet(
            f"background:#fff; border:1px solid {_BORDER}; font-size:10px;")
        lay.addWidget(self._file_list)

        self._btn_load = QPushButton("📂 불러오기")
        self._btn_load.setStyleSheet(_BTN_LOAD)
        self._btn_load.clicked.connect(self._do_load)
        lay.addWidget(self._btn_load)

        self._btn_refresh_dates = QPushButton("날짜 목록 갱신")
        self._btn_refresh_dates.setStyleSheet(_BTN_SM)
        self._btn_refresh_dates.clicked.connect(self._refresh_date_list)
        lay.addWidget(self._btn_refresh_dates)

        lay.addStretch()

        # 초기 날짜 목록 로드
        self._refresh_date_list()
        return frame

    def _build_right_panel(self) -> QFrame:
        frame = QFrame()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # 헤더 행 (제목 + 저장 시각 표시)
        hdr = QHBoxLayout()
        self._result_title = QLabel("결과")
        self._result_title.setStyleSheet(
            "font-weight:bold; font-size:13px; color:#1565C0;")
        hdr.addWidget(self._result_title)
        hdr.addStretch()
        self._saved_lbl = QLabel("")
        self._saved_lbl.setStyleSheet("color:#888; font-size:10px;")
        hdr.addWidget(self._saved_lbl)
        lay.addLayout(hdr)

        # 결과 테이블
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setStyleSheet(
            f"QTableWidget {{ background:{_BG_TBL}; font-size:11px;"
            f" gridline-color:{_BORDER}; }}"
            "QTableWidget::item:selected { background:#DBEAFE; color:#1E3A5F; }"
            f"QHeaderView::section {{ background:{_HEAD_BG};"
            f" color:#374151; border:1px solid {_BORDER};"
            f" font-size:11px; padding:3px; }}"
            "QTableWidget::item:alternate { background:#F9FAFB; }"
        )
        for i, (_, width, _align) in enumerate(_COLS):
            self._table.setColumnWidth(i, width)

        # 단일 클릭 → 호가창/차트 연동 (더블클릭 불필요)
        self._table.clicked.connect(self._on_row_click)
        self._table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(
            self._on_context_menu)

        lay.addWidget(self._table)
        return frame

    @staticmethod
    def _hline() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet(f"color:#CDD0D5; background:#CDD0D5; max-height:1px;")
        return f

    # ════════════════════════════════════════════════════════
    # DataBus 구독
    # ════════════════════════════════════════════════════════
    def _setup_subscriptions(self) -> None:
        self._bus.subscribe(
            Topics.SCANNER_RESULT, self,
            lambda d: self._invoke_main("_on_scanner_result", d))
        self._bus.subscribe(
            Topics.SCANNER_ERROR, self,
            lambda d: self._invoke_main("_on_scanner_error", d))

    # ════════════════════════════════════════════════════════
    # 조회
    # ════════════════════════════════════════════════════════
    def _do_scan(self) -> None:
        if self._broker is None:
            self._set_status("❌ 브로커 미연결 — 앱을 재시작하거나 IBKR을 연결하세요")
            return
        scan_code = self._scan_combo.currentData()
        location  = self._market_combo.currentData()
        min_p = self._min_price.value()
        max_p = self._max_price.value()
        filters = {
            "market":       "STK",
            "location":     location,
            "min_price":    min_p,
            "max_price":    max_p if max_p > 0 else 0,
            "above_volume": self._min_vol.value(),
        }
        self._req_id += 1
        self._btn_scan.setEnabled(False)
        self._btn_scan.setText("조회 중...")
        self._set_status(f"⏳ {scan_code} 조회 중... (req_id={self._req_id})")
        self._scan_timeout.start()   # 15초 타임아웃 시작
        log.info("Scanner scan: req_id=%d code=%s filters=%s",
                 self._req_id, scan_code, filters)
        try:
            self._broker.request_scanner(self._req_id, scan_code, filters)
        except Exception as e:
            log.exception("request_scanner 실패")
            self._scan_timeout.stop()
            self._btn_scan.setEnabled(True)
            self._btn_scan.setText("조회")
            self._set_status(f"❌ 스캐너 요청 실패: {e}")

    def _on_scan_timeout(self) -> None:
        """15초 이내 응답 없을 경우 버튼 복구"""
        log.warning("Scanner timeout — 응답 없음 (req_id=%d)", self._req_id)
        self._btn_scan.setEnabled(True)
        self._btn_scan.setText("조회")
        self._set_status("⏰ 조회 타임아웃 — IBKR 연결 상태를 확인하세요")

    # ════════════════════════════════════════════════════════
    # 결과 수신 / 표시
    # ════════════════════════════════════════════════════════
    @pyqtSlot(object)
    def _on_scanner_result(self, data: dict) -> None:
        scan_code = data.get("scan_code", "")
        results   = data.get("results", [])
        ts        = data.get("ts", time.time())
        dt_str    = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

        # 현재 요청 코드와 다를 경우 무시 (다른 패널 요청)
        # req_id 기반 필터링 — 매칭되지 않아도 표시 (단일 패널)

        self._scan_timeout.stop()   # 타임아웃 취소
        self._last_result = data
        self._render_table(results)

        # 타이틀 업데이트
        label = next((lbl for lbl, c in SCAN_CODES if c == scan_code), scan_code)
        self._result_title.setText(f"{label}  ({len(results)}개)")
        self._saved_lbl.setText(f"조회: {dt_str}")

        self._btn_scan.setEnabled(True)
        self._btn_scan.setText("조회")
        self._btn_save.setEnabled(True)
        self._set_status(f"✅ {len(results)}개 종목 조회 완료 — {dt_str}")

        # 자동 저장
        if self._auto_save_chk.isChecked() and results:
            path = self._storage.save(data)
            self._set_status(
                f"✅ {len(results)}개 종목 저장 완료 → {path.name}")
            self._refresh_date_list()

    @pyqtSlot(object)
    def _on_scanner_error(self, data: dict) -> None:
        msg = data.get("msg", "알 수 없는 오류")
        self._btn_scan.setEnabled(True)
        self._btn_scan.setText("조회")
        self._set_status(f"❌ 스캐너 오류: {msg}")

    def _render_table(self, results: list) -> None:
        self._table.setRowCount(0)
        self._table.setRowCount(len(results))

        font_bold = QFont()
        font_bold.setBold(True)

        for row, item in enumerate(results):
            pct = item.get("change_pct", 0.0)
            chg = item.get("change",     0.0)
            color_str = "#C62828" if pct < 0 else "#1565C0" if pct > 0 else _TEXT

            def cell(text: str, align=Qt.AlignmentFlag.AlignRight,
                     color: str | None = None, bold=False) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setTextAlignment(
                    align | Qt.AlignmentFlag.AlignVCenter)
                if color:
                    it.setForeground(QColor(color))
                if bold:
                    it.setFont(font_bold)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            vol     = item.get("volume",    0)
            avg_vol = item.get("avg_volume", 0)
            mktcap  = item.get("market_cap", 0)
            price   = item.get("price",      0.0)

            vol_ratio = f"{vol/avg_vol:.1f}x" if avg_vol > 0 else "-"

            self._table.setItem(row, 0, cell(str(item.get("rank", row+1)),
                                             Qt.AlignmentFlag.AlignCenter))
            sym_item = cell(item.get("symbol", ""),
                            Qt.AlignmentFlag.AlignLeft, bold=True)
            sym_item.setData(Qt.ItemDataRole.UserRole,
                             item.get("symbol", ""))
            self._table.setItem(row, 1, sym_item)
            self._table.setItem(row, 2, cell(f"${price:,.2f}"))
            self._table.setItem(row, 3, cell(
                f"{'+' if chg>=0 else ''}{chg:.2f}", color=color_str))
            self._table.setItem(row, 4, cell(
                f"{'+' if pct>=0 else ''}{pct:.2f}%",
                color=color_str, bold=True))
            self._table.setItem(row, 5, cell(_fmt_vol(vol)))
            self._table.setItem(row, 6, cell(vol_ratio))
            self._table.setItem(row, 7, cell(
                _fmt_cap(mktcap) if mktcap else "-"))
            self._table.setItem(row, 8, cell(
                item.get("exchange", ""), Qt.AlignmentFlag.AlignCenter))

            self._table.setRowHeight(row, 24)

    # ════════════════════════════════════════════════════════
    # 저장 / 불러오기
    # ════════════════════════════════════════════════════════
    def _do_save(self) -> None:
        if not self._last_result:
            return
        path = self._storage.save(self._last_result)
        self._set_status(f"💾 저장 완료 → {path.name}")
        self._refresh_date_list()

    def _refresh_date_list(self) -> None:
        dates = self._storage.list_dates()
        self._date_combo.blockSignals(True)
        self._date_combo.clear()
        self._date_combo.addItem("날짜 선택")
        for d in dates:
            # YYYYMMDD → YYYY-MM-DD 표시
            disp = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            self._date_combo.addItem(disp, d)
        self._date_combo.blockSignals(False)

    def _on_date_changed(self, text: str) -> None:
        self._file_list.clear()
        date_str = self._date_combo.currentData()
        if not date_str:
            return
        files = self._storage.list_files(date_str)
        for hhmm, scan_code, path in files:
            label = next((lbl for lbl, c in SCAN_CODES
                          if c == scan_code), scan_code)
            item = QListWidgetItem(f"{hhmm[:2]}:{hhmm[2:]}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self._file_list.addItem(item)

    def _do_load(self) -> None:
        sel = self._file_list.selectedItems()
        if not sel:
            self._set_status("⚠ 불러올 파일을 선택하세요")
            return
        path = sel[0].data(Qt.ItemDataRole.UserRole)
        data = self._storage.load(path)
        if not data:
            self._set_status(f"❌ 파일 로드 실패: {path}")
            return

        self._last_result = data
        results  = data.get("results", [])
        saved_at = data.get("saved_at", "")
        scan_code = data.get("scan_code", "")
        label = next((lbl for lbl, c in SCAN_CODES
                      if c == scan_code), scan_code)

        self._render_table(results)
        self._result_title.setText(
            f"[저장됨] {label}  ({len(results)}개)")
        self._saved_lbl.setText(f"저장: {saved_at}")
        self._btn_save.setEnabled(False)   # 불러온 데이터는 재저장 불필요
        self._set_status(
            f"📂 불러오기 완료 — {saved_at}  {label}  {len(results)}개")
        log.info("Scanner load: %s (%d rows)", path, len(results))

    # ════════════════════════════════════════════════════════
    # 테이블 인터랙션
    # ════════════════════════════════════════════════════════
    def _get_symbol_at(self, row: int) -> str | None:
        sym_item = self._table.item(row, 1)
        if not sym_item:
            return None
        return sym_item.data(Qt.ItemDataRole.UserRole) or sym_item.text()

    def _on_row_click(self, index) -> None:
        """단일 클릭 → WATCHLIST_SELECT 발행 (호가창/차트/기타 패널 전체 연동)"""
        sym = self._get_symbol_at(index.row())
        if sym:
            self._bus.publish(Topics.WATCHLIST_SELECT, {"symbol": sym})
            self._set_status(f"📌 {sym} → 전 패널 전환")

    def _on_context_menu(self, pos: QPoint) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        sym = self._get_symbol_at(row)
        if not sym:
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#fff; border:1px solid #CDD0D5; font-size:11px; }"
            "QMenu::item:selected { background:#DBEAFE; color:#1E3A5F; }")

        act_chart = menu.addAction(f"📈 {sym} 차트 보기")
        act_order = menu.addAction(f"💹 {sym} 주문창 전환")
        menu.addSeparator()
        act_watch = menu.addAction(f"★ {sym} 관심종목 추가")

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action in (act_chart, act_order):
            self._bus.publish(Topics.WATCHLIST_SELECT, {"symbol": sym})
        elif action == act_watch:
            self._cmd_queue.put({"action": "add_watchlist", "symbol": sym})
            self._set_status(f"★ {sym} 관심종목 추가 요청")

    # ════════════════════════════════════════════════════════
    # 자동 조회
    # ════════════════════════════════════════════════════════
    def _on_auto_refresh_toggle(self, checked: bool) -> None:
        if checked:
            ms = self._interval_combo.currentData()
            self._auto_timer.start(ms)
            self._countdown_remaining = ms // 1000
            self._countdown_timer.start()
            self._update_countdown()
            self._set_status(
                f"🔄 자동 조회 시작 ({self._interval_combo.currentText()})")
        else:
            self._auto_timer.stop()
            self._countdown_timer.stop()
            self._countdown_lbl.setText("")
            self._set_status("⏹ 자동 조회 중지")

    def _on_interval_changed(self) -> None:
        """간격 변경 시 타이머 재시작"""
        if self._auto_refresh_chk.isChecked():
            ms = self._interval_combo.currentData()
            self._auto_timer.start(ms)
            self._countdown_remaining = ms // 1000
            self._update_countdown()

    def _update_countdown(self) -> None:
        """1초마다 카운트다운 갱신"""
        if not self._auto_refresh_chk.isChecked():
            return
        if self._countdown_remaining > 0:
            self._countdown_lbl.setText(f"{self._countdown_remaining}s")
            self._countdown_remaining -= 1
        else:
            # 조회 발동 시 카운트 리셋
            ms = self._interval_combo.currentData()
            self._countdown_remaining = ms // 1000

    # ════════════════════════════════════════════════════════
    # 단축키
    # ════════════════════════════════════════════════════════
    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [
            ("F5",    self._do_scan),    # 재조회
            ("F6",    self._do_save),    # 저장
        ]

    # ════════════════════════════════════════════════════════
    # 내부 유틸
    # ════════════════════════════════════════════════════════
    def _set_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)

    def closeEvent(self, event) -> None:
        self._auto_timer.stop()
        self._countdown_timer.stop()
        super().closeEvent(event)
