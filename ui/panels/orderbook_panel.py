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
    QLabel, QWidget, QHBoxLayout, QVBoxLayout,
    QHeaderView, QFrame, QSplitter, QSizePolicy,
)
from PyQt6.QtCore import pyqtSlot, Qt
from PyQt6.QtGui import QColor, QFont

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel


# ── 라이트 테마 색상 (밝은 배경 기반, 가독성 강화) ──────────
BG_PANEL   = "#F5F5F5"   # 패널 배경 (연회색)
BG_ROW     = "#FFFFFF"   # 테이블 기본 행
BG_ROW_ALT = "#F0F4F8"   # 교차 행 (살짝 파란빛 회색)
BG_HEADER  = "#E8ECF0"   # 헤더 배경

# 밝은 배경에서 선명하게 보이는 진한 색상
ASK_TEXT   = "#C0392B"   # 매도 (진한 빨강)
BID_TEXT   = "#1565C0"   # 매수 (진한 파랑)
FILL_TEXT  = "#7B4F00"   # 체결 (진한 amber — 노랑 대신)
INFO_TEXT  = "#2E7D32"   # 정보 (진한 초록)
LABEL_DIM  = "#555E6B"   # 폼 레이블 (중간 회색)
PRICE_HL   = "#B45309"   # 현재가 강조 (진한 오렌지/골드)

BORDER_ASK  = "#E57373"   # 매도 테두리 (연한 빨강)
BORDER_BID  = "#64B5F6"   # 매수 테두리 (연한 파랑)
BORDER_FILL = "#FFB300"   # 체결 테두리 (amber)
BORDER_INFO = "#81C784"   # 정보 테두리 (연한 초록)

_ROWS = 10


def _table_style(text_color: str, border_color: str) -> str:
    return f"""
        QTableWidget {{
            background-color: {BG_ROW};
            alternate-background-color: {BG_ROW_ALT};
            color: {text_color};
            gridline-color: #D0D7DE;
            border: none;
            font-size: 15px;
            font-weight: bold;
        }}
        QTableWidget::item:selected {{
            background-color: #DBEAFE;
            color: {text_color};
        }}
        QHeaderView::section {{
            background-color: {BG_HEADER};
            color: #333;
            border: none;
            border-bottom: 2px solid {border_color};
            padding: 4px;
            font-weight: bold;
            font-size: 14px;
        }}
        QScrollBar:vertical {{
            background: #E8ECF0;
            width: 6px;
        }}
        QScrollBar::handle:vertical {{
            background: #9CA3AF;
            border-radius: 3px;
        }}
    """


def _frame_style(border_color: str) -> str:
    return f"""
        QFrame#sectionFrame {{
            background-color: {BG_PANEL};
            border: 1px solid {border_color};
            border-radius: 4px;
        }}
    """


class OrderbookPanel(BasePanel):
    PANEL_ID     = "orderbook"
    TITLE        = "호가창"
    DEFAULT_SIZE = (480, 600)

    def __init__(self, symbol: str, bus: DataBus, parent=None) -> None:
        super().__init__(bus, parent)
        self._symbol = symbol.upper()
        self.setStyleSheet(f"background-color: {BG_PANEL};")
        self._build_ui()

        bus.subscribe(Topics.stock_orderbook(self._symbol), self,
                      lambda d: self._invoke_main("_on_book", d))
        bus.subscribe(Topics.stock_price(self._symbol), self,
                      lambda d: self._invoke_main("_on_price", d))

        # 관심종목 선택 → 종목 자동 전환
        bus.subscribe(Topics.WATCHLIST_SELECT, self,
                      lambda d: self._invoke_main("_on_watchlist_select", d))

    # ── UI 구성 ──────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QHBoxLayout()
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self._layout.addLayout(root)

        # 좌: 매도(위) / 매수(아래)
        left_split = QSplitter(Qt.Orientation.Vertical)
        left_split.setHandleWidth(3)
        left_split.setStyleSheet("QSplitter::handle { background: #2D333B; }")

        self._ask_table = self._make_table(ASK_TEXT, BORDER_ASK)
        left_split.addWidget(self._section("매  도  Ask", self._ask_table, BORDER_ASK, ASK_TEXT))

        self._bid_table = self._make_table(BID_TEXT, BORDER_BID)
        left_split.addWidget(self._section("매  수  Bid", self._bid_table, BORDER_BID, BID_TEXT))
        left_split.setSizes([1, 1])

        # 우: 정보(위) / 체결(아래)
        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.setHandleWidth(3)
        right_split.setStyleSheet("QSplitter::handle { background: #2D333B; }")

        info_w = self._build_info()
        right_split.addWidget(self._section("정  보", info_w, BORDER_INFO, INFO_TEXT))

        self._fill_table = self._make_fill_table()
        right_split.addWidget(self._section("체  결", self._fill_table, BORDER_FILL, FILL_TEXT))
        right_split.setSizes([2, 3])

        # 좌/우 수평 분할
        h_split = QSplitter(Qt.Orientation.Horizontal)
        h_split.setHandleWidth(3)
        h_split.setStyleSheet("QSplitter::handle { background: #2D333B; }")
        h_split.addWidget(left_split)
        h_split.addWidget(right_split)
        h_split.setSizes([1, 1])

        root.addWidget(h_split)

    # ── 위젯 팩토리 ──────────────────────────────────────
    def _make_table(self, text_color: str, border_color: str) -> QTableWidget:
        t = QTableWidget(_ROWS, 2)
        t.setHorizontalHeaderLabels(["가  격", "수  량"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.setStyleSheet(_table_style(text_color, border_color))
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # 행 높이 타이트하게
        t.verticalHeader().setDefaultSectionSize(28)
        return t

    def _make_fill_table(self) -> QTableWidget:
        t = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels(["시  간", "가  격", "수  량"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.setStyleSheet(_table_style(FILL_TEXT, BORDER_FILL))
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.verticalHeader().setDefaultSectionSize(28)
        return t

    def _build_info(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: transparent;")

        from PyQt6.QtWidgets import QGridLayout
        g = QGridLayout(w)
        g.setContentsMargins(10, 8, 10, 8)
        g.setSpacing(6)
        g.setColumnStretch(1, 1)

        def _label(txt: str, color: str = LABEL_DIM, bold: bool = False) -> QLabel:
            lb = QLabel(txt)
            lb.setStyleSheet(
                f"color: {color}; font-size: 14px;"
                + (" font-weight: bold;" if bold else "")
            )
            return lb

        def _value(txt: str = "--", color: str = "#1A1A1A") -> QLabel:
            lb = QLabel(txt)
            lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lb.setStyleSheet(f"color: {color}; font-size: 15px;")
            return lb

        # 종목
        self._lbl_sym = _label(self._symbol, "#111827", bold=True)
        self._lbl_sym.setStyleSheet("color:#111827; font-size:17px; font-weight:bold;")
        g.addWidget(self._lbl_sym, 0, 0, 1, 2)

        # 구분선
        g.addWidget(self._hline(BORDER_INFO), 1, 0, 1, 2)

        # 현재가 (크게)
        g.addWidget(_label("현재가"), 2, 0)
        self._lbl_price = QLabel("--")
        self._lbl_price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_price.setStyleSheet(
            f"color: {PRICE_HL}; font-size: 23px; font-weight: bold; letter-spacing: 0.5px;")
        g.addWidget(self._lbl_price, 2, 1)

        g.addWidget(self._hline("#2D333B"), 3, 0, 1, 2)

        # 매수1호가
        g.addWidget(_label("매수1호가"), 4, 0)
        self._lbl_bid = _value(color=BID_TEXT)
        g.addWidget(self._lbl_bid, 4, 1)

        # 매도1호가
        g.addWidget(_label("매도1호가"), 5, 0)
        self._lbl_ask = _value(color=ASK_TEXT)
        g.addWidget(self._lbl_ask, 5, 1)

        # 스프레드
        g.addWidget(_label("스프레드"), 6, 0)
        self._lbl_spread = _value(color=LABEL_DIM)
        g.addWidget(self._lbl_spread, 6, 1)

        g.setRowStretch(7, 1)  # 하단 여백
        return w

    @staticmethod
    def _hline(color: str = "#2D333B") -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet(f"color: {color}; background: {color}; max-height: 1px;")
        return f

    def _section(self, title: str, widget: QWidget,
                 border: str, title_color: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionFrame")
        frame.setStyleSheet(_frame_style(border))

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 타이틀 바
        hdr = QLabel(title)
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setFixedHeight(24)
        hdr.setStyleSheet(
            f"color: {title_color}; background: {BG_HEADER};"
            f"border-bottom: 2px solid {border};"
            f"font-size: 14px; font-weight: bold; letter-spacing: 1px;"
        )
        lay.addWidget(hdr)
        lay.addWidget(widget)
        return frame

    # ── DataBus 슬롯 ─────────────────────────────────────
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

    @pyqtSlot(object)
    def _on_book(self, data: dict) -> None:
        asks = sorted(data.get("asks", []), key=lambda x: x[0])[:_ROWS]
        bids = sorted(data.get("bids", []), key=lambda x: x[0], reverse=True)[:_ROWS]
        # 매도: 높은 호가가 위 (역순)
        self._fill_book(self._ask_table, list(reversed(asks)), ASK_TEXT)
        self._fill_book(self._bid_table, bids, BID_TEXT)
        self._add_fill_row(data)

    def _fill_book(self, table: QTableWidget, rows: list, text_color: str) -> None:
        table.setRowCount(_ROWS)
        color = QColor(text_color)
        for i in range(_ROWS):
            if i < len(rows):
                price, qty = rows[i]
                p_item = QTableWidgetItem(f"{price:,.2f}")
                q_item = QTableWidgetItem(f"{qty:,}")
                for item in (p_item, q_item):
                    item.setForeground(color)
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
        import time, random
        bids = data.get("bids", [])
        if not bids:
            return
        price = bids[0][0]
        qty   = random.randint(10, 500)
        t_str = time.strftime("%H:%M:%S")

        self._fill_table.insertRow(0)
        color = QColor(FILL_TEXT)
        for col, val in enumerate([t_str, f"{price:,.2f}", f"{qty:,}"]):
            item = QTableWidgetItem(val)
            item.setForeground(color)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._fill_table.setItem(0, col, item)

        if self._fill_table.rowCount() > 200:
            self._fill_table.removeRow(200)

    # ── 관심종목 선택 → 종목 전환 ────────────────────────
    @pyqtSlot(object)
    def _on_watchlist_select(self, data: dict) -> None:
        sym = data.get("symbol", "").upper()
        if sym and sym != self._symbol:
            self.set_symbol(sym)

    def set_symbol(self, symbol: str) -> None:
        """호가창 종목 전환 — 기존 구독 해제 후 새 구독"""
        new_sym = symbol.upper()
        if new_sym == self._symbol:
            return
        # 기존 구독 해제
        self._bus.unsubscribe(Topics.stock_orderbook(self._symbol), self)
        self._bus.unsubscribe(Topics.stock_price(self._symbol), self)
        # 종목 전환
        self._symbol = new_sym
        # 화면 초기화
        self._clear_book()
        self._fill_table.setRowCount(0)
        if hasattr(self, "_sym_lbl"):
            self._sym_lbl.setText(new_sym)
        self.setWindowTitle(f"호가창 — {new_sym}")
        # 새 구독
        self._bus.subscribe(Topics.stock_orderbook(new_sym), self,
                            lambda d: self._invoke_main("_on_book", d))
        self._bus.subscribe(Topics.stock_price(new_sym), self,
                            lambda d: self._invoke_main("_on_price", d))

    def _clear_book(self) -> None:
        for r in range(_ROWS):
            for c in range(2):
                for tbl in (self._ask_table, self._bid_table):
                    item = tbl.item(r, c)
                    if item:
                        item.setText("")

    # ── 단축키 ───────────────────────────────────────────
    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [
            ("Ctrl+R", self._clear_fills),
        ]

    def _clear_fills(self) -> None:
        self._fill_table.setRowCount(0)
