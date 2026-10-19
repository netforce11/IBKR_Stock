# ui/panels/orderbook_panel.py
# 호가창 패널 — 실제 HTS 스타일 레이아웃
#
#  ┌──────────────────────────┬───────────┐
#  │  Ask (매도) — 역순       │  정보창   │
#  │  [높은가격]              │           │
#  │  ...                     │           │
#  │  [최우선매도]            │           │
#  ├── 현재가 바 ─────────────┤           │
#  │  [최우선매수]            │           │
#  │  ...                     ├───────────┤
#  │  [낮은가격]              │  체결창   │
#  │  Bid (매수) — 정순       │           │
#  └──────────────────────────┴───────────┘

from __future__ import annotations
from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem,
    QLabel, QWidget, QHBoxLayout, QVBoxLayout,
    QHeaderView, QFrame, QSplitter, QSizePolicy,
    QGridLayout,
)
from PyQt6.QtCore import pyqtSlot, Qt
from PyQt6.QtGui import QColor, QFont

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel


# ── 색상 ─────────────────────────────────────────────────
BG_PANEL   = "#F5F5F5"
BG_HEADER  = "#E8ECF0"

# Ask (매도) — 빨강 계열
ASK_TEXT   = "#B91C1C"       # 진한 빨강
ASK_BG     = "#FFF5F5"       # 연한 빨강 배경
ASK_BG_ALT = "#FFE9E9"       # 교차 행
BORDER_ASK = "#E57373"

# Bid (매수) — 파랑 계열
BID_TEXT   = "#1565C0"       # 진한 파랑
BID_BG     = "#F0F6FF"       # 연한 파랑 배경
BID_BG_ALT = "#E2EEFF"       # 교차 행
BORDER_BID = "#64B5F6"

# 현재가 바
PRICE_BAR_BG  = "#1E293B"    # 어두운 네이비
PRICE_BAR_TXT = "#F1F5F9"    # 밝은 텍스트
PRICE_HL      = "#FBBF24"    # 현재가 강조 (금색)

# 체결/정보
FILL_TEXT   = "#7B4F00"
BORDER_FILL = "#FFB300"
INFO_TEXT   = "#2E7D32"
BORDER_INFO = "#81C784"
LABEL_DIM   = "#555E6B"

_ROWS = 8   # Ask/Bid 각 표시 행수


def _ask_table_style() -> str:
    return f"""
        QTableWidget {{
            background-color: {ASK_BG};
            alternate-background-color: {ASK_BG_ALT};
            color: {ASK_TEXT};
            gridline-color: #F8CACA;
            border: none;
            font-size: 15px;
            font-weight: bold;
        }}
        QTableWidget::item:selected {{
            background-color: #FDD;
            color: {ASK_TEXT};
        }}
        QHeaderView::section {{
            background-color: {BG_HEADER};
            color: {ASK_TEXT};
            border: none;
            border-bottom: 2px solid {BORDER_ASK};
            padding: 4px;
            font-weight: bold;
            font-size: 13px;
        }}
        QScrollBar:vertical {{ width: 5px; background: #EEE; }}
        QScrollBar::handle:vertical {{ background: #CCC; border-radius: 2px; }}
    """


def _bid_table_style() -> str:
    return f"""
        QTableWidget {{
            background-color: {BID_BG};
            alternate-background-color: {BID_BG_ALT};
            color: {BID_TEXT};
            gridline-color: #C8DFF8;
            border: none;
            font-size: 15px;
            font-weight: bold;
        }}
        QTableWidget::item:selected {{
            background-color: #DBEAFE;
            color: {BID_TEXT};
        }}
        QHeaderView::section {{
            background-color: {BG_HEADER};
            color: {BID_TEXT};
            border: none;
            border-bottom: 2px solid {BORDER_BID};
            padding: 4px;
            font-weight: bold;
            font-size: 13px;
        }}
        QScrollBar:vertical {{ width: 5px; background: #EEE; }}
        QScrollBar::handle:vertical {{ background: #CCC; border-radius: 2px; }}
    """


def _fill_table_style() -> str:
    return f"""
        QTableWidget {{
            background-color: #FFFDF0;
            alternate-background-color: #FFF8DC;
            color: {FILL_TEXT};
            gridline-color: #F0E0A0;
            border: none;
            font-size: 14px;
        }}
        QHeaderView::section {{
            background-color: {BG_HEADER};
            color: {FILL_TEXT};
            border: none;
            border-bottom: 2px solid {BORDER_FILL};
            padding: 3px;
            font-size: 13px;
        }}
        QScrollBar:vertical {{ width: 5px; background: #EEE; }}
        QScrollBar::handle:vertical {{ background: #CCC; border-radius: 2px; }}
    """


def _frame_style(border_color: str) -> str:
    return (
        f"QFrame#sectionFrame {{"
        f"background-color: {BG_PANEL};"
        f"border: 1px solid {border_color};"
        f"border-radius: 4px; }}"
    )


class OrderbookPanel(BasePanel):
    PANEL_ID     = "orderbook"
    TITLE        = "호가창"
    DEFAULT_SIZE = (480, 660)

    def __init__(self, symbol: str, bus: DataBus, parent=None) -> None:
        super().__init__(bus, parent)
        self._symbol = symbol.upper()
        self.setStyleSheet(f"background-color: {BG_PANEL};")
        self._build_ui()

        bus.subscribe(Topics.stock_orderbook(self._symbol), self,
                      lambda d: self._invoke_main("_on_book", d))
        bus.subscribe(Topics.stock_price(self._symbol), self,
                      lambda d: self._invoke_main("_on_price", d))
        bus.subscribe(Topics.WATCHLIST_SELECT, self,
                      lambda d: self._invoke_main("_on_watchlist_select", d))

    # ── UI 구성 ──────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QHBoxLayout()
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self._layout.addLayout(root)

        # ── 좌: 통합 호가창 ───────────────────────────────
        book_frame = QFrame()
        book_frame.setObjectName("sectionFrame")
        book_frame.setStyleSheet(_frame_style(BORDER_ASK))

        book_lay = QVBoxLayout(book_frame)
        book_lay.setContentsMargins(0, 0, 0, 0)
        book_lay.setSpacing(0)

        # 1) 헤더
        hdr = self._book_header()
        book_lay.addWidget(hdr)

        # 2) Ask 테이블 (매도 — 위쪽)
        self._ask_table = self._make_ask_table()
        book_lay.addWidget(self._ask_table)

        # 3) 현재가 바
        self._price_bar = self._make_price_bar()
        book_lay.addWidget(self._price_bar)

        # 4) Bid 테이블 (매수 — 아래쪽)
        self._bid_table = self._make_bid_table()
        book_lay.addWidget(self._bid_table)

        # ── 우: 정보(위) / 체결(아래) ─────────────────────
        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.setHandleWidth(3)
        right_split.setStyleSheet("QSplitter::handle { background: #CED4DA; }")

        info_w = self._build_info()
        right_split.addWidget(self._section("정  보", info_w, BORDER_INFO, INFO_TEXT))

        self._fill_table = self._make_fill_table()
        right_split.addWidget(self._section("체  결", self._fill_table, BORDER_FILL, FILL_TEXT))
        right_split.setSizes([2, 3])

        # ── 수평 분할 ─────────────────────────────────────
        h_split = QSplitter(Qt.Orientation.Horizontal)
        h_split.setHandleWidth(3)
        h_split.setStyleSheet("QSplitter::handle { background: #CED4DA; }")
        h_split.addWidget(book_frame)
        h_split.addWidget(right_split)
        h_split.setSizes([1, 1])

        root.addWidget(h_split)

    def _book_header(self) -> QLabel:
        """호가창 상단 컬럼 헤더 (가격 / 수량)"""
        hdr = QLabel("  가  격                     수  량  ")
        hdr.setFixedHeight(24)
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setStyleSheet(
            f"background: {BG_HEADER}; color: #333;"
            f"font-size: 13px; font-weight: bold;"
            f"border-bottom: 1px solid #CED4DA;"
        )
        return hdr

    def _make_ask_table(self) -> QTableWidget:
        t = QTableWidget(_ROWS, 2)
        t.setHorizontalHeaderLabels(["매도가격  ▲", "수  량"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setFixedHeight(22)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.setStyleSheet(_ask_table_style())
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.verticalHeader().setDefaultSectionSize(26)
        # Ask 테이블은 스크롤바 숨김 (고정 행수)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setFixedHeight(22 + _ROWS * 26 + 2)
        return t

    def _make_bid_table(self) -> QTableWidget:
        t = QTableWidget(_ROWS, 2)
        t.setHorizontalHeaderLabels(["매수가격  ▼", "수  량"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setFixedHeight(22)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.setStyleSheet(_bid_table_style())
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.verticalHeader().setDefaultSectionSize(26)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setFixedHeight(22 + _ROWS * 26 + 2)
        return t

    def _make_price_bar(self) -> QWidget:
        """Ask / Bid 사이 현재가 구분 바"""
        w = QWidget()
        w.setFixedHeight(36)
        w.setStyleSheet(f"background: {PRICE_BAR_BG};")

        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(6)

        lbl = QLabel("현재가")
        lbl.setStyleSheet(
            f"color: {PRICE_BAR_TXT}; font-size: 11px; background: transparent;")

        self._bar_price = QLabel("--")
        self._bar_price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._bar_price.setStyleSheet(
            f"color: {PRICE_HL}; font-size: 18px; font-weight: bold; background: transparent;")

        self._bar_spread = QLabel("")
        self._bar_spread.setStyleSheet(
            f"color: #94A3B8; font-size: 11px; background: transparent;")

        lay.addWidget(lbl)
        lay.addWidget(self._bar_price, 1)
        lay.addWidget(self._bar_spread)
        return w

    def _make_fill_table(self) -> QTableWidget:
        t = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels(["시  간", "가  격", "수  량"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.setStyleSheet(_fill_table_style())
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.verticalHeader().setDefaultSectionSize(26)
        return t

    def _build_info(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")

        g = QGridLayout(w)
        g.setContentsMargins(10, 8, 10, 8)
        g.setSpacing(6)
        g.setColumnStretch(1, 1)

        def _label(txt: str, color: str = LABEL_DIM, bold: bool = False) -> QLabel:
            lb = QLabel(txt)
            lb.setStyleSheet(
                f"color: {color}; font-size: 13px;"
                + (" font-weight: bold;" if bold else "")
            )
            return lb

        def _value(txt: str = "--", color: str = "#1A1A1A") -> QLabel:
            lb = QLabel(txt)
            lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lb.setStyleSheet(f"color: {color}; font-size: 14px;")
            return lb

        self._lbl_sym = QLabel(self._symbol)
        self._lbl_sym.setStyleSheet(
            "color:#111827; font-size:17px; font-weight:bold;")
        g.addWidget(self._lbl_sym, 0, 0, 1, 2)
        g.addWidget(self._hline(BORDER_INFO), 1, 0, 1, 2)

        g.addWidget(_label("현재가"), 2, 0)
        self._lbl_price = QLabel("--")
        self._lbl_price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_price.setStyleSheet(
            f"color: {PRICE_HL}; font-size: 22px; font-weight: bold;")
        g.addWidget(self._lbl_price, 2, 1)

        # 등락률
        g.addWidget(_label("등락률"), 3, 0)
        self._lbl_chg = _value(color=LABEL_DIM)
        g.addWidget(self._lbl_chg, 3, 1)

        g.addWidget(self._hline("#CED4DA"), 4, 0, 1, 2)

        # 매수1호가
        g.addWidget(_label("매수1호가"), 5, 0)
        self._lbl_bid = _value(color=BID_TEXT)
        g.addWidget(self._lbl_bid, 5, 1)

        # 매도1호가
        g.addWidget(_label("매도1호가"), 6, 0)
        self._lbl_ask = _value(color=ASK_TEXT)
        g.addWidget(self._lbl_ask, 6, 1)

        # 스프레드
        g.addWidget(_label("스프레드"), 7, 0)
        self._lbl_spread = _value(color=LABEL_DIM)
        g.addWidget(self._lbl_spread, 7, 1)

        g.setRowStretch(8, 1)  # 하단 여백
        return w

    @staticmethod
    def _hline(color: str = "#CED4DA") -> QFrame:
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

        hdr = QLabel(title)
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setFixedHeight(24)
        hdr.setStyleSheet(
            f"color: {title_color}; background: {BG_HEADER};"
            f"border-bottom: 2px solid {border};"
            f"font-size: 13px; font-weight: bold; letter-spacing: 1px;"
        )
        lay.addWidget(hdr)
        lay.addWidget(widget)
        return frame

    # ── DataBus 슬롯 ─────────────────────────────────────
    @pyqtSlot(object)
    def _on_price(self, data: dict) -> None:
        price      = data.get("price",      0.0)
        bid        = data.get("bid",        0.0)
        ask        = data.get("ask",        0.0)
        prev_close = data.get("prev_close", 0.0)
        # 정보창
        self._lbl_price.setText(f"${price:,.2f}")
        self._lbl_bid.setText(f"${bid:,.2f}")
        self._lbl_ask.setText(f"${ask:,.2f}")
        spread = round(ask - bid, 4)
        self._lbl_spread.setText(f"${spread:.4f}")
        # 현재가 바
        self._bar_price.setText(f"${price:,.2f}")
        self._bar_spread.setText(f"스프레드 ${spread:.4f}")

        # 등락률
        if prev_close > 0 and price > 0:
            chg_pct = (price - prev_close) / prev_close * 100
            sign    = "+" if chg_pct >= 0 else ""
            color   = "#26A69A" if chg_pct > 0 else ("#EF5350" if chg_pct < 0 else "#888")
            self._lbl_chg.setText(f"{sign}{chg_pct:.2f}%")
            self._lbl_chg.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: bold;")
        else:
            self._lbl_chg.setText("—")

    @pyqtSlot(object)
    def _on_book(self, data: dict) -> None:
        # Ask: 낮은 가격이 아래(최우선매도), 높은 가격이 위
        asks = sorted(data.get("asks", []), key=lambda x: x[0])[:_ROWS]
        # Bid: 높은 가격이 위(최우선매수), 낮은 가격이 아래
        bids = sorted(data.get("bids", []), key=lambda x: x[0], reverse=True)[:_ROWS]

        # Ask 테이블: 역순 채우기 (높은 가격 → 행0, 최우선매도 → 마지막 행)
        self._fill_ask(list(reversed(asks)))
        # Bid 테이블: 정순 채우기 (최우선매수 → 행0)
        self._fill_bid(bids)
        self._add_fill_row(data)

    def _fill_ask(self, rows: list) -> None:
        """Ask 테이블 채우기 — 빨강 배경 행별 적용"""
        self._ask_table.setRowCount(_ROWS)
        n = len(rows)
        for i in range(_ROWS):
            if i < n:
                price, qty = rows[i]
                p_item = QTableWidgetItem(f"{price:,.2f}")
                q_item = QTableWidgetItem(f"{qty:,}")
                p_item.setForeground(QColor(ASK_TEXT))
                q_item.setForeground(QColor(ASK_TEXT))
                p_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                q_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                # 최우선매도(마지막 행)은 배경 강조
                if i == n - 1:
                    for item in (p_item, q_item):
                        item.setBackground(QColor("#FFCDD2"))
                self._ask_table.setItem(i, 0, p_item)
                self._ask_table.setItem(i, 1, q_item)
            else:
                self._ask_table.setItem(i, 0, QTableWidgetItem(""))
                self._ask_table.setItem(i, 1, QTableWidgetItem(""))

    def _fill_bid(self, rows: list) -> None:
        """Bid 테이블 채우기 — 파랑 배경 행별 적용"""
        self._bid_table.setRowCount(_ROWS)
        for i in range(_ROWS):
            if i < len(rows):
                price, qty = rows[i]
                p_item = QTableWidgetItem(f"{price:,.2f}")
                q_item = QTableWidgetItem(f"{qty:,}")
                p_item.setForeground(QColor(BID_TEXT))
                q_item.setForeground(QColor(BID_TEXT))
                p_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                q_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                # 최우선매수(첫 행)은 배경 강조
                if i == 0:
                    for item in (p_item, q_item):
                        item.setBackground(QColor("#BBDEFB"))
                self._bid_table.setItem(i, 0, p_item)
                self._bid_table.setItem(i, 1, q_item)
            else:
                self._bid_table.setItem(i, 0, QTableWidgetItem(""))
                self._bid_table.setItem(i, 1, QTableWidgetItem(""))

    def _add_fill_row(self, data: dict) -> None:
        import time, random
        bids = data.get("bids", [])
        if not bids:
            return
        price = sorted(bids, key=lambda x: x[0], reverse=True)[0][0]
        qty   = random.randint(10, 500)
        t_str = time.strftime("%H:%M:%S")

        self._fill_table.insertRow(0)
        color = QColor(FILL_TEXT)
        for col, val in enumerate([t_str, f"{price:,.2f}", f"{qty:,}"]):
            item = QTableWidgetItem(val)
            item.setForeground(color)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
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
        new_sym = symbol.upper()
        if new_sym == self._symbol:
            return
        self._bus.unsubscribe(Topics.stock_orderbook(self._symbol), self)
        self._bus.unsubscribe(Topics.stock_price(self._symbol), self)
        self._symbol = new_sym
        self._clear_book()
        self._fill_table.setRowCount(0)
        self._lbl_sym.setText(new_sym)
        self.setWindowTitle(f"호가창 — {new_sym}")
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
        self._bar_price.setText("--")
        self._bar_spread.setText("")

    # ── 단축키 ───────────────────────────────────────────
    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [
            ("Ctrl+R", self._clear_fills),
        ]

    def _clear_fills(self) -> None:
        self._fill_table.setRowCount(0)
