# ui/panels/order_panel.py
# 주문 패널 — 심볼/수량/가격 입력 + 매수/매도 버튼
# 버튼 클릭 → cmd_queue.put({action, symbol, qty, price, order_type})

from __future__ import annotations
import queue
import logging

from PyQt6.QtWidgets import (
    QLabel, QLineEdit, QComboBox, QPushButton,
    QHBoxLayout, QVBoxLayout, QFormLayout,
    QFrame, QSizePolicy, QDoubleSpinBox, QSpinBox,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel

log = logging.getLogger(__name__)

# ── 색상 상수 (라이트 테마) ──────────────────────────────
_BG         = "#F5F5F5"
_BG_INPUT   = "#FFFFFF"
_BORDER     = "#CDD0D5"
_TEXT       = "#111827"
_LABEL      = "#555E6B"
_BTN_BUY    = ("background:#1565C0; color:#fff; font-weight:bold; font-size:14px;"
               "padding:8px; border:none; border-radius:4px;")
_BTN_SELL   = ("background:#C62828; color:#fff; font-weight:bold; font-size:14px;"
               "padding:8px; border:none; border-radius:4px;")
_BTN_CANCEL = ("background:#E8ECF0; color:#555E6B; font-size:11px;"
               "padding:4px; border:1px solid #CDD0D5; border-radius:3px;")
_INPUT_STYLE = (f"QComboBox, QDoubleSpinBox, QSpinBox {{"
                f"background:{_BG_INPUT}; color:{_TEXT}; border:1px solid {_BORDER};"
                f"border-radius:3px; padding:2px 4px; font-size:12px;}}")


class OrderPanel(BasePanel):
    PANEL_ID     = "order"
    TITLE        = "주문"
    DEFAULT_SIZE = (280, 380)

    def __init__(self, cmd_queue: queue.Queue, bus: DataBus, parent=None) -> None:
        super().__init__(bus, parent)
        self._cmd_queue = cmd_queue
        self._last_price: float = 0.0
        self.setStyleSheet(
            f"background:{_BG}; color:{_TEXT};"
            f"QLabel {{ color:{_LABEL}; font-size:11px; }}"
            + _INPUT_STYLE
        )
        self._build_ui()

        # 현재가 구독 → 가격 필드 자동 채움
        bus.subscribe("stock.price.*", self,
                      lambda d: self._invoke_main("_on_price_any", d))

    # ── UI 구성 ──────────────────────────────────────────
    def _build_ui(self) -> None:
        # ① 심볼 선택
        sym_row = QHBoxLayout()
        sym_row.addWidget(QLabel("종목"))
        self._sym_combo = QComboBox()
        self._sym_combo.setEditable(True)
        self._sym_combo.addItems(["AAPL", "SPY", "QQQ", "TSLA", "NVDA"])
        self._sym_combo.setCurrentText("AAPL")
        sym_row.addWidget(self._sym_combo, 1)
        self._layout.addLayout(sym_row)

        # ② 현재가 표시
        self._price_lbl = QLabel("현재가: --")
        self._price_lbl.setStyleSheet(
            "color:#B45309; font-weight:bold; font-size:14px;")
        self._layout.addWidget(self._price_lbl)

        self._layout.addWidget(self._hline())

        # ③ 주문 입력 폼
        form = QFormLayout()
        form.setSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)

        # 주문 유형
        self._order_type = QComboBox()
        self._order_type.addItems(["지정가 (LMT)", "시장가 (MKT)"])
        self._order_type.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("주문유형", self._order_type)

        # 가격
        self._price_spin = QDoubleSpinBox()
        self._price_spin.setRange(0.0, 999_999.0)
        self._price_spin.setDecimals(2)
        self._price_spin.setSingleStep(0.01)
        self._price_spin.setPrefix("$")
        form.addRow("가격", self._price_spin)

        # 수량
        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(1, 100_000)
        self._qty_spin.setValue(10)
        self._qty_spin.setSingleStep(10)
        form.addRow("수량", self._qty_spin)

        # 예상 금액
        self._est_lbl = QLabel("$0.00")
        self._est_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._est_lbl.setStyleSheet("color:#111827; font-size:12px; font-weight:bold;")
        form.addRow("예상금액", self._est_lbl)

        self._layout.addLayout(form)

        # 수량/가격 변경 시 예상금액 갱신
        self._price_spin.valueChanged.connect(self._update_est)
        self._qty_spin.valueChanged.connect(self._update_est)

        self._layout.addWidget(self._hline())

        # ④ 버튼 행
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._btn_buy = QPushButton("매 수")
        self._btn_buy.setStyleSheet(_BTN_BUY)
        self._btn_buy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_buy.setMinimumHeight(44)
        self._btn_buy.clicked.connect(lambda: self._place_order("BUY"))

        self._btn_sell = QPushButton("매 도")
        self._btn_sell.setStyleSheet(_BTN_SELL)
        self._btn_sell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_sell.setMinimumHeight(44)
        self._btn_sell.clicked.connect(lambda: self._place_order("SELL"))

        btn_row.addWidget(self._btn_buy)
        btn_row.addWidget(self._btn_sell)
        self._layout.addLayout(btn_row)

        # ⑤ 취소 버튼
        self._btn_cancel = QPushButton("전체 주문 취소")
        self._btn_cancel.setStyleSheet(_BTN_CANCEL)
        self._btn_cancel.clicked.connect(self._cancel_all)
        self._layout.addWidget(self._btn_cancel)

        # ⑥ 상태 표시
        self._status_lbl = QLabel("")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet("color:#888; font-size:10px;")
        self._layout.addWidget(self._status_lbl)

        self._layout.addStretch()

    @staticmethod
    def _hline() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet("color:#30363D; background:#30363D; max-height:1px;")
        return f

    # ── 이벤트 핸들러 ────────────────────────────────────
    def _on_type_changed(self, idx: int) -> None:
        is_limit = (idx == 0)
        self._price_spin.setEnabled(is_limit)

    def _update_est(self) -> None:
        est = self._price_spin.value() * self._qty_spin.value()
        self._est_lbl.setText(f"${est:,.2f}")

    @pyqtSlot(object)
    def _on_price_any(self, data: dict) -> None:
        sym = data.get("symbol", "").upper()
        if sym != self._sym_combo.currentText().upper():
            return
        price = data.get("price", 0.0)
        self._last_price = price
        self._price_lbl.setText(f"현재가: ${price:,.2f}")
        # 가격 스핀박스가 0이면 현재가로 자동 채움
        if self._price_spin.value() == 0.0:
            self._price_spin.setValue(price)

    # ── 주문 발송 ────────────────────────────────────────
    def _place_order(self, side: str) -> None:
        symbol    = self._sym_combo.currentText().strip().upper()
        qty       = self._qty_spin.value()
        is_market = self._order_type.currentIndex() == 1
        price     = 0.0 if is_market else self._price_spin.value()
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
            "side":       side,          # "BUY" | "SELL"
            "qty":        qty,
            "price":      price,
            "order_type": order_type,
        }
        self._cmd_queue.put(cmd)
        log.info("Order sent: %s", cmd)
        side_kr = "매수" if side == "BUY" else "매도"
        self._show_status(f"✅ {side_kr} {symbol} {qty}주 @ {'시장가' if is_market else f'${price:.2f}'}")

    def _cancel_all(self) -> None:
        self._cmd_queue.put({"action": "cancel_all"})
        self._show_status("전체 취소 요청")

    def _show_status(self, msg: str, *, error: bool = False) -> None:
        color = "#FF6B6B" if error else "#A9E34B"
        self._status_lbl.setStyleSheet(f"color:{color}; font-size:10px;")
        self._status_lbl.setText(msg)

    # ── 단축키 ───────────────────────────────────────────
    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [
            ("F1", lambda: self._place_order("BUY")),   # F1 = 매수
            ("F2", lambda: self._place_order("SELL")),  # F2 = 매도
            ("F3", self._cancel_all),                   # F3 = 전체취소
        ]
