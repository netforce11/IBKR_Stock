# ui/panels/orderbook_panel.py
# 호가창 패널 (옵션 HTS의 옵션체인 대체)

from __future__ import annotations
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QLabel, QHBoxLayout, QWidget
from PyQt6.QtCore import pyqtSlot, Qt
from PyQt6.QtGui import QColor
from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel


class OrderbookPanel(BasePanel):
    PANEL_ID = "orderbook"

    def __init__(self, symbol: str, bus: DataBus, parent=None) -> None:
        super().__init__(f"호가창 — {symbol}", bus, parent)
        self._symbol = symbol
        self._build_ui()

        # DataBus 구독 (UI → Core 방향 금지)
        bus.subscribe(Topics.stock_orderbook(symbol), self,
                      lambda d: self._invoke_main("_refresh_book", d))
        bus.subscribe(Topics.stock_price(symbol), self,
                      lambda d: self._invoke_main("_refresh_price", d))

    def _build_ui(self) -> None:
        # 현재가 레이블
        self._price_lbl = QLabel("--")
        self._price_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._price_lbl)

        # 호가 테이블: 매도5 + 매수5 = 10행, 가격/수량 2열
        self._table = QTableWidget(10, 2)
        self._table.setHorizontalHeaderLabels(["가격", "수량"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._layout.addWidget(self._table)

    # ── Qt 슬롯 (메인 스레드에서만 실행) ─────────────────
    @pyqtSlot(object)
    def _refresh_price(self, data: dict) -> None:
        price = data.get("price", 0)
        self._price_lbl.setText(f"${price:,.2f}")

    @pyqtSlot(object)
    def _refresh_book(self, data: dict) -> None:
        asks = sorted(data.get("asks", []), key=lambda x: x[0], reverse=True)[:5]
        bids = sorted(data.get("bids", []), key=lambda x: x[0], reverse=True)[:5]
        rows = asks + bids
        for i, (price, qty) in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(f"{price:.2f}"))
            self._table.setItem(i, 1, QTableWidgetItem(str(qty)))
            # 매도 = 빨간색, 매수 = 파란색
            color = QColor("#ff6b6b") if i < 5 else QColor("#4dabf7")
            self._table.item(i, 0).setForeground(color)
            self._table.item(i, 1).setForeground(color)
