# ui/panels/fill_history_panel.py
# 체결 히스토리 패널 — 당일/과거 체결 목록 + P&L
#
# DataBus 구독: order.filled → 실시간 추가 (당일 보기 중일 때만)
# 시작 시: StockDBManager.query_fills() 로 당일 내역 로드
# 날짜 드롭다운: 과거 날짜 DB 조회 가능

from __future__ import annotations
import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QHBoxLayout, QVBoxLayout, QFrame,
    QComboBox, QPushButton, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSlot, QDate
from PyQt6.QtGui import QColor

from shared.core.bus import DataBus
from shared.ui.panels.base_panel import BasePanel

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# ── 색상 ────────────────────────────────────────────────────
_BG       = "#F5F5F5"
_BG_ROW   = "#FFFFFF"
_BG_ALT   = "#F0F4F8"
_BG_HDR   = "#E8ECF0"
_BUY_CLR  = "#1565C0"   # 매수 (진파랑)
_SELL_CLR = "#C0392B"   # 매도 (진빨강)
_PNL_POS  = "#2E7D32"   # 익절 (진초록)
_PNL_NEG  = "#C0392B"   # 손절 (진빨강)
_TEXT     = "#111827"
_DIM      = "#555E6B"

_BTN_QSS = (
    "QPushButton { background:#E8ECF0; color:#111827; border:1px solid #D0D7DE;"
    " border-radius:3px; padding:2px 8px; font-size:11px; }"
    "QPushButton:hover { background:#D0D7DE; }"
    "QPushButton:pressed { background:#C4C9D0; }"
)
_COMBO_QSS = (
    "QComboBox { background:#FFFFFF; border:1px solid #D0D7DE; border-radius:3px;"
    " padding:2px 6px; font-size:11px; min-width:100px; }"
    "QComboBox::drop-down { border:none; }"
    "QComboBox QAbstractItemView { background:#FFFFFF; selection-background-color:#DBEAFE; }"
)

_TABLE_STYLE = f"""
QTableWidget {{
    background-color: {_BG_ROW};
    alternate-background-color: {_BG_ALT};
    color: {_TEXT};
    gridline-color: #D0D7DE;
    border: none;
    font-size: 12px;
}}
QHeaderView::section {{
    background-color: {_BG_HDR};
    color: #333;
    border: none;
    border-bottom: 2px solid #64B5F6;
    padding: 3px;
    font-weight: bold;
    font-size: 11px;
}}
QScrollBar:vertical {{
    background: #E8ECF0; width: 6px;
}}
QScrollBar::handle:vertical {{
    background: #9CA3AF; border-radius: 3px;
}}
"""

_COLS = ["시간", "종목", "구분", "수량", "체결가", "수수료", "실현손익"]

# 날짜 드롭다운: 최근 N 영업일 생성
_HISTORY_DAYS = 30


def _recent_trading_days(n: int) -> list[date]:
    """ET 기준 오늘 포함 최근 n 영업일(월~금) 날짜 리스트 (최신 → 과거 순)"""
    from datetime import datetime
    today = datetime.now(ET).date()
    result = []
    d = today
    while len(result) < n:
        if d.weekday() < 5:   # 월(0)~금(4)
            result.append(d)
        d -= timedelta(days=1)
    return result


class FillHistoryPanel(BasePanel):
    PANEL_ID     = "fill_history"
    TITLE        = "체결 내역"
    DEFAULT_SIZE = (600, 340)

    def __init__(self, bus: DataBus, db_manager=None, parent=None) -> None:
        super().__init__(bus, parent)
        self._db         = db_manager
        self._total_pnl  = 0.0
        self._viewing_today = True          # 날짜 선택이 오늘인지 여부
        self.setStyleSheet(f"background:{_BG};")
        self._build_ui()

        # 실시간 체결 구독 (오늘 보기 중일 때만 _on_fill 실제 삽입)
        bus.subscribe("order.filled", self,
                      lambda d: self._invoke_main("_on_fill", d))

        # 시작 시 당일 내역 로드
        if db_manager:
            self._load_date(None)   # None = 오늘

    # ── UI 구성 ──────────────────────────────────────────────
    def _build_ui(self) -> None:
        # ── 상단 컨트롤바 ────────────────────────────────────
        top = QHBoxLayout()
        top.setContentsMargins(4, 4, 4, 4)
        top.setSpacing(6)

        self._title_lbl = QLabel("📋 체결 내역")
        self._title_lbl.setStyleSheet(
            "color:#111827; font-weight:bold; font-size:13px;")
        top.addWidget(self._title_lbl)

        # 날짜 드롭다운
        self._date_combo = QComboBox()
        self._date_combo.setStyleSheet(_COMBO_QSS)
        self._date_combo.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        days = _recent_trading_days(_HISTORY_DAYS)
        for d in days:
            label = "오늘" if d == days[0] else d.strftime("%m/%d (%a)")
            self._date_combo.addItem(label, d)
        self._date_combo.currentIndexChanged.connect(self._on_date_changed)
        top.addWidget(self._date_combo)

        # 새로고침 버튼
        refresh_btn = QPushButton("🔄")
        refresh_btn.setToolTip("현재 날짜 다시 불러오기")
        refresh_btn.setStyleSheet(_BTN_QSS)
        refresh_btn.setFixedWidth(28)
        refresh_btn.clicked.connect(self._refresh)
        top.addWidget(refresh_btn)

        top.addStretch()

        # 실현손익 합계
        pnl_lbl = QLabel("실현손익:")
        pnl_lbl.setStyleSheet(f"color:{_DIM}; font-size:11px;")
        top.addWidget(pnl_lbl)

        self._pnl_total = QLabel("$0.00")
        self._pnl_total.setStyleSheet(
            "color:#111827; font-weight:bold; font-size:13px; min-width:80px;")
        self._pnl_total.setAlignment(Qt.AlignmentFlag.AlignRight)
        top.addWidget(self._pnl_total)

        self._layout.addLayout(top)

        # 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#D0D7DE; background:#D0D7DE; max-height:1px;")
        self._layout.addWidget(sep)

        # ── 테이블 ───────────────────────────────────────────
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # 시간·구분 컬럼은 고정폭
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(_TABLE_STYLE)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._layout.addWidget(self._table)

    # ── 날짜 변경 ────────────────────────────────────────────
    def _on_date_changed(self, idx: int) -> None:
        d: date = self._date_combo.itemData(idx)
        from datetime import datetime
        today = datetime.now(ET).date()
        self._viewing_today = (d == today)
        self._load_date(d)

    def _refresh(self) -> None:
        """현재 선택된 날짜 다시 로드"""
        idx = self._date_combo.currentIndex()
        d: date = self._date_combo.itemData(idx)
        self._load_date(d)

    # ── 데이터 로드 ──────────────────────────────────────────
    def _load_date(self, d: date | None) -> None:
        """DB에서 지정 날짜 체결 내역 로드 (None=오늘)"""
        self._table.setRowCount(0)
        self._total_pnl = 0.0
        self._pnl_total.setText("$0.00")
        self._pnl_total.setStyleSheet(
            "color:#111827; font-weight:bold; font-size:13px; min-width:80px;")

        if not self._db:
            return
        try:
            rows = self._db.query_fills(d)
            for row in reversed(rows):   # 오래된 것부터 삽입 → 최신이 상단
                self._insert_row(row)
        except Exception as e:
            log.error("_load_date(%s) error: %s", d, e)

    @pyqtSlot(object)
    def _on_fill(self, data: dict) -> None:
        """실시간 체결 이벤트 → 오늘 보기 중일 때만 최상단에 추가"""
        if not self._viewing_today:
            return
        from datetime import datetime
        now = datetime.now(ET)
        row = {
            "time_et":      now.strftime("%H:%M:%S"),
            "symbol":       data.get("symbol", ""),
            "side":         data.get("side", ""),
            "qty":          data.get("qty", 0),
            "fill_price":   data.get("fill_price", data.get("price", 0.0)),
            "commission":   data.get("commission", 0.0),
            "realized_pnl": data.get("realized_pnl", 0.0),
        }
        self._insert_row(row, prepend=True)

    def _insert_row(self, row: dict, prepend: bool = False) -> None:
        side     = str(row.get("side", ""))
        pnl      = float(row.get("realized_pnl", 0.0))
        side_clr = _BUY_CLR if side == "BUY" else _SELL_CLR
        pnl_clr  = _PNL_POS if pnl >= 0 else _PNL_NEG

        pos = 0 if prepend else self._table.rowCount()
        self._table.insertRow(pos)

        def _item(txt, color=_TEXT, align=Qt.AlignmentFlag.AlignCenter):
            it = QTableWidgetItem(str(txt))
            it.setForeground(QColor(color))
            it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            return it

        right = Qt.AlignmentFlag.AlignRight

        self._table.setItem(pos, 0, _item(row.get("time_et", "--")))
        self._table.setItem(pos, 1, _item(row.get("symbol", ""), align=right))
        self._table.setItem(pos, 2, _item(
            "매수" if side == "BUY" else "매도", side_clr))
        self._table.setItem(pos, 3, _item(
            f"{int(row.get('qty', 0)):,}", align=right))
        self._table.setItem(pos, 4, _item(
            f"${float(row.get('fill_price', 0)):,.2f}", align=right))
        self._table.setItem(pos, 5, _item(
            f"${float(row.get('commission', 0)):,.4f}", align=right))
        self._table.setItem(pos, 6, _item(
            f"${pnl:+,.2f}", pnl_clr, right))

        # P&L 합계 갱신
        self._total_pnl += pnl
        clr = _PNL_POS if self._total_pnl >= 0 else _PNL_NEG
        self._pnl_total.setText(f"${self._total_pnl:+,.2f}")
        self._pnl_total.setStyleSheet(
            f"color:{clr}; font-weight:bold; font-size:13px; min-width:80px;")

    # ── 단축키 ───────────────────────────────────────────────
    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [
            ("Ctrl+Shift+C", self._clear_table),
            ("Ctrl+R",       self._refresh),
        ]

    def _clear_table(self) -> None:
        self._table.setRowCount(0)
        self._total_pnl = 0.0
        self._pnl_total.setText("$0.00")
        self._pnl_total.setStyleSheet(
            "color:#111827; font-weight:bold; font-size:13px; min-width:80px;")
