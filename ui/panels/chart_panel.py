# ui/panels/chart_panel.py  (IBKR_Stock 전용)
# MinuteChartPanel 상속 → IBKR 로더 주입

from __future__ import annotations
from datetime import date

from shared.ui.chart.minute_chart_panel import MinuteChartPanel
from shared.ui.chart.data_source import BaseMinuteLoader, Candle
from shared.core.bus import DataBus
from shared.core.topics import Topics


class IBKRMinuteLoader(BaseMinuteLoader):
    """
    IBKR 분봉 로더.
    실제 연결 전까지는 MockBroker의 가짜 데이터로 동작.
    """
    def run(self) -> None:
        import time, random
        from datetime import datetime, timedelta
        try:
            # Mock: 오전 9:30 ~ 16:00 분봉 생성
            candles = []
            start = datetime.combine(
                self.target_date,
                datetime.strptime("09:30", "%H:%M").time()
            )
            price = random.uniform(150, 200)
            for i in range(390):   # 6.5시간 × 60분
                dt = start + timedelta(minutes=i)
                o  = price
                h  = o + random.uniform(0, 1.5)
                l  = o - random.uniform(0, 1.5)
                c  = random.uniform(l, h)
                v  = random.randint(10_000, 500_000)
                candles.append(Candle(int(dt.timestamp()), o, h, l, c, v))
                price = c
            self.loaded.emit(candles)
        except Exception as e:
            self.error.emit(str(e))


class StockChartPanel(MinuteChartPanel):
    """IBKR_Stock 전용 차트 패널"""
    PANEL_ID = "stock_chart"

    def __init__(self, bus: DataBus, parent=None) -> None:
        super().__init__(bus, parent)
        self.setWindowTitle("주식 차트")
        # 실시간 candle 구독
        bus.subscribe("candle.tick",   self,
                      lambda d: self._invoke_main("_on_tick_inv", d))
        bus.subscribe("candle.closed", self,
                      lambda d: self._invoke_main("_on_closed_inv", d))

    def _make_minute_loader(self, symbol: str, target_date: date):
        return IBKRMinuteLoader(symbol, target_date)

    # _invoke_main 슬롯
    from PyQt6.QtCore import pyqtSlot

    @pyqtSlot(object)
    def _on_tick_inv(self, data: dict) -> None:
        self.on_candle_tick(data)

    @pyqtSlot(object)
    def _on_closed_inv(self, data: dict) -> None:
        self.on_candle_closed(data)

    def register_shortcuts(self) -> list:
        base = super().register_shortcuts()
        return base + [
            ("Ctrl+R", self._refresh),   # 새로고침 추가 단축키
        ]
