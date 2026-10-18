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
    TITLE = "주식 차트"
    DEFAULT_SIZE = (800, 500)

    def __init__(self, bus: DataBus, parent=None) -> None:
        super().__init__(bus, parent)
        self._indicators: dict[str, float] = {}

        # 실시간 candle 구독
        bus.subscribe("candle.tick",   self,
                      lambda d: self._invoke_main("_on_tick_inv", d))
        bus.subscribe("candle.closed", self,
                      lambda d: self._invoke_main("_on_closed_inv", d))

        # 지표 구독 — CalcProcess가 publish하는 토픽
        # DataBus는 exact-match only → 종목별 exact 토픽으로 구독
        # 심볼이 변경될 때는 _update_indicator_subs() 호출
        self._indicator_names = ("sma20", "ema9", "rsi14")
        self._update_indicator_subs(self._symbol)

        # 관심종목 선택 → 차트 종목 전환
        bus.subscribe(Topics.WATCHLIST_SELECT, self,
                      lambda d: self._invoke_main("_on_watchlist_select", d))

    def _make_minute_loader(self, symbol: str, target_date: date):
        return IBKRMinuteLoader(symbol, target_date)

    def _update_indicator_subs(self, symbol: str, old_symbol: str | None = None) -> None:
        """종목 변경 시 지표 구독을 old → new 로 교체"""
        if old_symbol:
            for ind in self._indicator_names:
                self._bus.unsubscribe(Topics.indicator(old_symbol, ind), self)
        for ind in self._indicator_names:
            self._bus.subscribe(
                Topics.indicator(symbol, ind), self,
                lambda d, i=ind: self._invoke_main("_on_indicator", d))

    def _set_symbol(self, symbol: str) -> None:
        """MinuteChartPanel._set_symbol 오버라이드 — 지표 구독도 교체"""
        old = getattr(self, "_symbol", None)
        super()._set_symbol(symbol)
        self._update_indicator_subs(symbol, old)

    # _invoke_main 슬롯
    from PyQt6.QtCore import pyqtSlot

    @pyqtSlot(object)
    def _on_tick_inv(self, data: dict) -> None:
        self.on_candle_tick(data)

    @pyqtSlot(object)
    def _on_closed_inv(self, data: dict) -> None:
        self.on_candle_closed(data)

    from PyQt6.QtCore import pyqtSlot as _slot

    @_slot(object)
    def _on_indicator(self, data: dict) -> None:
        """CalcProcess → DataBus → 차트 사이드바 지표 갱신"""
        ind  = data.get("indicator", "")
        val  = data.get("value", 0.0)
        sym  = data.get("symbol", "").upper()
        self._indicators[ind] = val
        self._update_indicator_labels()

    def _update_indicator_labels(self) -> None:
        """MinuteChartPanel status label에 지표값 표시"""
        parts = []
        if "sma20" in self._indicators:
            parts.append(f"SMA20: {self._indicators['sma20']:.2f}")
        if "ema9" in self._indicators:
            parts.append(f"EMA9: {self._indicators['ema9']:.2f}")
        if "rsi14" in self._indicators:
            rsi = self._indicators["rsi14"]
            flag = " ▲과매수" if rsi > 70 else (" ▼과매도" if rsi < 30 else "")
            parts.append(f"RSI14: {rsi:.1f}{flag}")
        if parts:
            self._set_status("  |  ".join(parts))

    # ── 관심종목 선택 → 차트 종목 전환 ──────────────────
    from PyQt6.QtCore import pyqtSlot as _pyqtSlot2

    @_pyqtSlot2(object)
    def _on_watchlist_select(self, data: dict) -> None:
        sym = data.get("symbol", "").upper()
        if sym and sym != getattr(self, "_symbol", ""):
            self._set_symbol(sym)   # MinuteChartPanel의 종목 전환 메서드

    def register_shortcuts(self) -> list:
        base = super().register_shortcuts()
        return base + [
            ("Ctrl+R", self._refresh),   # 새로고침 추가 단축키
        ]
