# ui/panels/portfolio_panel.py
# 포트폴리오/잔고 패널

from __future__ import annotations
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QLabel
from PyQt6.QtCore import pyqtSlot
from core.bus import DataBus
from core.topics import Topics
from ui.panels.base_panel import BasePanel


class PortfolioPanel(BasePanel):
    PANEL_ID = "portfolio"

    def __init__(self, bus: DataBus, parent=None) -> None:
        super().__init__("포트폴리오", bus, parent)
        self._build_ui()
        bus.subscribe(Topics.PORTFOLIO_SUMMARY, self,
                      lambda d: self._invoke_main("_refresh", d))
        bus.subscribe(Topics.ACCOUNT_BALANCE, self,
                      lambda d: self._invoke_main("_refresh_balance", d))

    def _build_ui(self) -> None:
        self._balance_lbl = QLabel("잔고: --")
        self._layout.addWidget(self._balance_lbl)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["종목", "수량", "평균단가", "평가손익"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._layout.addWidget(self._table)

    @pyqtSlot(object)
    def _refresh(self, data: dict) -> None:
        positions = data.get("positions", [])
        self._table.setRowCount(len(positions))
        for i, pos in enumerate(positions):
            self._table.setItem(i, 0, QTableWidgetItem(pos["symbol"]))
            self._table.setItem(i, 1, QTableWidgetItem(str(pos["qty"])))
            self._table.setItem(i, 2, QTableWidgetItem(f"{pos['avg_price']:.2f}"))
            self._table.setItem(i, 3, QTableWidgetItem(f"{pos['pnl']:.2f}"))

    @pyqtSlot(object)
    def _refresh_balance(self, data: dict) -> None:
        balance = data.get("balance", 0)
        self._balance_lbl.setText(f"잔고: ${balance:,.2f}")
