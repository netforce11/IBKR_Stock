# ui/panels/fill_history_panel.py
# 체결 히스토리 패널 — 당일 체결 목록 + P&L
#
# DataBus 구독: order.filled → 실시간 추가
# 시작 시: StockDBManager.query_fills() 로 당일 내역 로드

from __future__ import annotations
import logging

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QHBoxLayout, QVBoxLayout, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QColor, QFont

from shared.core.bus import DataBus
from shared.ui.panels.base_panel import BasePanel

log = logging.getLogger(__name__)

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


class FillHistoryPanel(BasePanel):
    PANEL_ID     = "fill_history"
    TITLE        = "체결 내역"
    DEFAULT_SIZE = (560, 320)

    def __init__(self, bus: DataBus, db_manager=None, parent=None) -> None:
        super().__init__(bus, parent)
        self._db = db_manager
        self._total_pnl = 0.0
        self.setStyleSheet(f"background:{_BG};")
        self._build_ui()

        # 실시간 체결 구독
        bus.subscribe("order.filled", self,
                      lambda d: self._invoke_main("_on_fill", d))

        # 시작 시 당일 내역 로드
        if db_manager:
            self._load_today()

    # ── UI 구성 ──────────────────────────────────────────────
    def _build_ui(self) -> None:
        # 상단 요약바
        top = QHBoxLayout()
        top.setContentsMargins(4, 4, 4, 4)

        title = QLabel("📋 당일 체결 내역")
        title.setStyleSheet("color:#111827; font-weight:bold; font-size:13px;")
        top.addWidget(title)
        top.addStretch()

        pnl_lbl = QLabel("실현손익 합계:")
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

        # 테이블
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(_TABLE_STYLE)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._layout.addWidget(self._table)

    # ── 데이터 로드 ──────────────────────────────────────────
    def _load_today(self) -> None:
        """앱 시작 시 DB에서 당일 체결 로드"""
        try:
            rows = self._db.query_fills()
            for row in reversed(rows):  # 오래된 것부터 삽입
                self._insert_row(row)
        except Exception as e:
            log.error("_load_today error: %s", e)

    @pyqtSlot(object)
    def _on_fill(self, data: dict) -> None:
        """실시간 체결 이벤트 → 테이블 최상단에 추가"""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
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
            f"${float(row.get('commission', 0)):,.2f}", align=right))
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
        ]

    def _clear_table(self) -> None:
        self._table.setRowCount(0)
        self._total_pnl = 0.0
        self._pnl_total.setText("$0.00")
        self._pnl_total.setStyleSheet(
            "color:#111827; font-weight:bold; font-size:13px; min-width:80px;")
