# ui/panels/orderbook_panel.py
# 호가창 패널 — 키움 스타일 레이아웃
#
#  ┌───────────┬───────────┐
#  │ 1. 매도   │ 3. 정보창 │
#  │  (Ask)    │           │
#  ├───────────┼───────────┤
#  │ 2. 매수   │ 4. 체결창 │
#  │  (Bid)    │           │
#  └───────────┴───────────┘

from __future__ import annotations
from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem,
    QLabel, QWidget, QGridLayout,
    QHeaderView, QFrame,
)
from PyQt6.QtCore import pyqtSlot, Qt
from PyQt6.QtGui import QColor, QFont

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel


# ── 색상 상수 ───────────────────────────────────────────
ASK_COLOR  = QColor("#FF6B6B")   # 매도 (빨강 계열)
BID_COLOR  = QColor("#4DABF7")   # 매수 (파랑 계열)
FILL_COLOR = QColor("#FFD43B")   # 체결 (노랑 계열)
INFO_COLOR = QColor("#A9E34B")   # 정보 (초록 계열)

_ROWS = 10   # 매도/매수 각 10호가


class OrderbookPanel(BasePanel):
    PANEL_ID    = "orderbook"
    TITLE       = "호가창"
    DEFAULT_SIZE = (480, 600)

    def __init__(self, symbol: str, bus: DataBus, parent=None) -> None:
        super().__init__(bus, parent)
        self._symbol = symbol.upper()
        self._build_ui()

        # DataBus 구독
        bus.subscribe(Topics.stock_orderbook(self._symbol), self,
                      lambda d: self._invoke_main("_on_book", d))
        bus.subscribe(Topics.stock_price(self._symbol), self,
                      lambda d: self._invoke_main("_on_price", d))

    # ── UI 구성 ───────────────────────────────────────────
    def _build_ui(self) -> None:
        # 최상위: 좌(호가) | 우(정보+체결)
        from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QSplitter
        root = QHBoxLayout()
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self._layout.addLayout(root)

        # ── 좌측: 매도(위) + 매수(아래) ─────────────────
        left_splitter = QSplitter(Qt.Orientation.Vertical)

        self._ask_table = self._make_book_table("매도 (Ask)", ASK_COLOR)
        ask_wrap = self._titled("매 도", self._ask_table, ASK_COLOR)
        left_splitter.addWidget(ask_wrap)

        self._bid_table = self._make_book_table("매수 (Bid)", BID_COLOR)
        bid_wrap = self._titled("매 수", self._bid_table, BID_COLOR)
        left_splitter.addWidget(bid_wrap)

        left_splitter.setSizes([1, 1])   # 50:50 초기 비율
        left_splitter.setHandleWidth(4)

        # ── 우측: 정보창(위) + 체결창(아래) ─────────────
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        info_widget = self._build_info_widget()
        info_wrap = self._titled("정 보", info_widget, INFO_COLOR)
        right_splitter.addWidget(info_wrap)

        self._fill_table = self._make_fill_table()
        fill_wrap = self._titled("체 결", self._fill_table, FILL_COLOR)
        right_splitter.addWidget(fill_wrap)

        right_splitter.setSizes([1, 2])   # 정보 1 : 체결 2
        right_splitter.setHandleWidth(4)

        # 좌:우 = 1:1 (QSplitter로 조절 가능)
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.addWidget(left_splitter)
        h_splitter.addWidget(right_splitter)
        h_splitter.setSizes([1, 1])
        h_splitter.setHandleWidth(4)

        root.addWidget(h_splitter)

    # ── 위젯 팩토리 ───────────────────────────────────────
    def _make_book_table(self, _label: str, color: QColor) -> QTableWidget:
        t = QTableWidget(_ROWS, 2)
        t.setHorizontalHeaderLabels(["가격", "수량"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setShowGrid(True)
        # 헤더 색
        t.horizontalHeader().setStyleSheet(
            f"QHeaderView::section {{ color: {color.name()}; }}"
        )
        return t

    def _make_fill_table(self) -> QTableWidget:
        t = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels(["시간", "가격", "수량"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        return t

    def _build_info_widget(self) -> QWidget:
        w = QWidget()
        from PyQt6.QtWidgets import QFormLayout
        lay = QFormLayout(w)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(4)

        def _lbl(txt="--"):
            l = QLabel(txt)
            l.setAlignment(Qt.AlignmentFlag.AlignRight)
            return l

        self._lbl_price  = _lbl(); self._lbl_price.setStyleSheet(
            "font-size:16px; font-weight:bold; color:#FFD43B;")
        self._lbl_bid    = _lbl()
        self._lbl_ask    = _lbl()
        self._lbl_spread = _lbl()
        self._lbl_sym    = QLabel(self._symbol)
        self._lbl_sym.setStyleSheet("font-weight:bold;")

        lay.addRow("종목",   self._lbl_sym)
        lay.addRow("현재가", self._lbl_price)
        lay.addRow("매수1호가", self._lbl_bid)
        lay.addRow("매도1호가", self._lbl_ask)
        lay.addRow("스프레드",  self._lbl_spread)
        return w

    @staticmethod
    def _titled(title: str, widget: QWidget, color: QColor) -> QFrame:
        """테두리 + 타이틀 프레임"""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            f"QFrame {{ border: 1px solid {color.name()}; border-radius: 3px; }}"
        )
        from PyQt6.QtWidgets import QVBoxLayout
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)

        lbl = QLabel(title)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color: {color.name()}; font-weight: bold; font-size: 11px;"
            "border: none;"
        )
        lay.addWidget(lbl)
        lay.addWidget(widget)
        return frame

    # ── DataBus 슬롯 ──────────────────────────────────────
    @pyqtSlot(object)
    def _on_price(self, data: dict) -> None:
        price = data.get("price", 0.0)
        bid   = data.get("bid",   0.0)
        ask   = data.get("ask",   0.0)
        self._lbl_price.setText(f"${price:,.2f}")
        self._lbl_bid.setText(f"${bid:,.2f}")
        self._lbl_ask.setText(f"${ask:,.2f}")
        spread = round(ask - bid, 4)
        self._lbl_spread.setText(f"${spread:.4f}")
        # 상승/하락 색
        # (이전가 비교는 추후 추가)

    @pyqtSlot(object)
    def _on_book(self, data: dict) -> None:
        asks = sorted(data.get("asks", []), key=lambda x: x[0])[:_ROWS]
        bids = sorted(data.get("bids", []), key=lambda x: x[0], reverse=True)[:_ROWS]

        # 매도: 낮은 호가가 하단(행 9→0)에 가까이 오도록 역순 표시
        asks_disp = list(reversed(asks))  # 높은 가격 위, 낮은 가격 아래
        self._fill_book(self._ask_table, asks_disp, ASK_COLOR)
        self._fill_book(self._bid_table, bids, BID_COLOR)

        # 체결창에 현재 스프레드 대 추가 (mock: 최우선 호가 이용)
        self._add_fill_row(data)

    def _fill_book(self, table: QTableWidget, rows: list, color: QColor) -> None:
        table.setRowCount(_ROWS)
        for i in range(_ROWS):
            if i < len(rows):
                price, qty = rows[i]
                p_item = QTableWidgetItem(f"{price:.2f}")
                q_item = QTableWidgetItem(f"{qty:,}")
                p_item.setForeground(color)
                q_item.setForeground(color)
                p_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                q_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(i, 0, p_item)
                table.setItem(i, 1, q_item)
            else:
                table.setItem(i, 0, QTableWidgetItem(""))
                table.setItem(i, 1, QTableWidgetItem(""))

    def _add_fill_row(self, data: dict) -> None:
        """체결창에 mock 체결 1건 추가 (실제 브로커 연결 시 대체)"""
        import time, random
        bids = data.get("bids", [])
        if not bids:
            return
        price = bids[0][0]
        qty   = random.randint(10, 500)
        t_str = time.strftime("%H:%M:%S")

        row = self._fill_table.rowCount()
        self._fill_table.insertRow(0)   # 최신 체결이 위로
        for col, val in enumerate([t_str, f"{price:.2f}", str(qty)]):
            item = QTableWidgetItem(val)
            item.setForeground(FILL_COLOR)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._fill_table.setItem(0, col, item)

        # 최대 200행 유지
        if self._fill_table.rowCount() > 200:
            self._fill_table.removeRow(200)

    # ── 단축키 추가 ───────────────────────────────────────
    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [
            ("Ctrl+R", self._clear_fills),  # 체결창 초기화
        ]

    def _clear_fills(self) -> None:
        self._fill_table.setRowCount(0)
