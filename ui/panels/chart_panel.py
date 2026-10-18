# ui/panels/chart_panel.py  (IBKR_Stock 전용)
# MinuteChartPanel 상속 → IBKR 로더 주입

from __future__ import annotations
import threading
import logging
from datetime import date, datetime

from shared.ui.chart.minute_chart_panel import MinuteChartPanel
from shared.ui.chart.data_source import BaseMinuteLoader, BaseDailyLoader, Candle
from shared.core.bus import DataBus
from shared.core.topics import Topics

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 분봉 로더
# ══════════════════════════════════════════════════════════════
class IBKRMinuteLoader(BaseMinuteLoader):
    """
    IBKR 분봉 로더.
    - broker가 주입된 경우 → 실제 reqHistoricalData 호출
    - broker=None 또는 미연결 → Mock 랜덤 데이터 (개발/테스트용)
    """

    def __init__(self, symbol: str, target_date: date, broker=None) -> None:
        super().__init__(symbol, target_date)
        self._broker = broker
        self._done_event = threading.Event()
        self._bars: list = []
        self._fetch_error: str = ""

    # ── IBKR 콜백 (IBKR 소켓 스레드에서 호출됨) ──────────
    def _on_bars_received(self, bars: list) -> None:
        self._bars = bars
        self._done_event.set()

    # ── QThread.run() ────────────────────────────────────────
    def run(self) -> None:
        broker    = self._broker
        connected = getattr(broker, "_connected", False)
        if broker is not None and connected:
            self._run_real(broker)
        else:
            self._run_mock()

    def _run_real(self, broker) -> None:
        try:
            from datetime import date as _date
            today = _date.today()
            if self.target_date == today:
                duration = "1 D"
                end_dt   = ""
            else:
                duration = "1 D"
                end_dt   = self.target_date.strftime("%Y%m%d") + " 16:00:00"

            if end_dt:
                self._request_past(broker, end_dt)
            else:
                broker.request_historical(
                    self.symbol, "1 D", "1 min",
                    callback=self._on_bars_received
                )

            if not self._done_event.wait(timeout=30):
                self.error.emit(f"{self.symbol} 분봉 수신 타임아웃 (30s)")
                return

            if self._fetch_error:
                self.error.emit(self._fetch_error)
                return

            candles = _bars_to_candles(self._bars)
            if candles:
                self.loaded.emit(candles)
            else:
                log.warning("[ChartPanel] %s 분봉 실데이터 없음 → Mock 폴백", self.symbol)
                self._run_mock()

        except Exception as e:
            log.exception("[ChartPanel] IBKR 분봉 오류")
            self.error.emit(str(e))

    def _request_past(self, broker, end_dt: str) -> None:
        try:
            from broker.ibkr_broker import _make_stk_contract
            req_id = broker._next_req_id
            broker._next_req_id += 1
            broker._hist_callbacks[req_id] = [self.symbol, [], self._on_bars_received]
            c = _make_stk_contract(self.symbol)
            broker._ib.reqHistoricalData(
                req_id, c, end_dt, "1 D", "1 min",
                "TRADES", 1, 1, False, []
            )
        except Exception as e:
            self._fetch_error = str(e)
            self._done_event.set()

    def _run_mock(self) -> None:
        import random
        from datetime import timedelta
        try:
            candles = []
            start = datetime.combine(
                self.target_date,
                datetime.strptime("09:30", "%H:%M").time()
            )
            price = random.uniform(150, 200)
            for i in range(390):
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


# ══════════════════════════════════════════════════════════════
# 일봉 로더
# ══════════════════════════════════════════════════════════════
class IBKRDailyLoader(BaseDailyLoader):
    """
    IBKR 일봉 로더.
    - broker 연결 시 → reqHistoricalData("1 day" barSize)
    - broker=None 또는 미연결 → Mock 랜덤 일봉
    """

    def __init__(self, symbol: str, days: int = 60, broker=None) -> None:
        super().__init__(symbol, days)
        self._broker      = broker
        self._done_event  = threading.Event()
        self._bars: list  = []
        self._fetch_error = ""

    def _on_bars_received(self, bars: list) -> None:
        self._bars = bars
        self._done_event.set()

    def run(self) -> None:
        broker    = self._broker
        connected = getattr(broker, "_connected", False)
        if broker is not None and connected:
            self._run_real(broker)
        else:
            self._run_mock()

    def _run_real(self, broker) -> None:
        try:
            from broker.ibkr_broker import _make_stk_contract
            req_id = broker._next_req_id
            broker._next_req_id += 1
            broker._hist_callbacks[req_id] = [self.symbol, [], self._on_bars_received]
            c = _make_stk_contract(self.symbol)
            # days → IBKR duration string (예: 60일 → "3 M")
            duration = self._days_to_duration(self.days)
            broker._ib.reqHistoricalData(
                req_id, c,
                "",          # endDateTime: 현재까지
                duration,
                "1 day",     # barSizeSetting
                "TRADES",
                1,           # useRTH
                1,           # formatDate
                False, []
            )

            if not self._done_event.wait(timeout=30):
                self.error.emit(f"{self.symbol} 일봉 수신 타임아웃 (30s)")
                return

            if self._fetch_error:
                self.error.emit(self._fetch_error)
                return

            candles = _bars_to_candles(self._bars, daily=True)
            if candles:
                self.loaded.emit(candles)
            else:
                log.warning("[ChartPanel] %s 일봉 실데이터 없음 → Mock 폴백", self.symbol)
                self._run_mock()

        except Exception as e:
            log.exception("[ChartPanel] IBKR 일봉 오류")
            self.error.emit(str(e))

    @staticmethod
    def _days_to_duration(days: int) -> str:
        """days → IBKR durationStr (예: 60→"3 M", 252→"1 Y")"""
        if days <= 30:
            return f"{days} D"
        elif days <= 90:
            months = max(1, round(days / 30))
            return f"{months} M"
        else:
            years = max(1, round(days / 252))
            return f"{years} Y"

    def _run_mock(self) -> None:
        import random
        from datetime import timedelta
        try:
            candles = []
            d = date.today()
            price = random.uniform(150, 200)
            for _ in range(self.days):
                while d.weekday() >= 5:  # 주말 스킵
                    d -= timedelta(days=1)
                o  = price
                h  = o + random.uniform(0, 5)
                l  = o - random.uniform(0, 5)
                c  = random.uniform(l, h)
                v  = random.randint(5_000_000, 50_000_000)
                candles.append(Candle(
                    int(datetime.combine(d, datetime.min.time()).timestamp()),
                    o, h, l, c, v
                ))
                price = c
                d -= timedelta(days=1)
            candles.reverse()   # 오래된 날짜 → 최신 순서
            self.loaded.emit(candles)
        except Exception as e:
            self.error.emit(str(e))


# ══════════════════════════════════════════════════════════════
# 공통 파서 (분봉/일봉 공유)
# ══════════════════════════════════════════════════════════════
def _bars_to_candles(bars: list, daily: bool = False) -> list:
    """
    IBKR bar dict 리스트 → Candle 객체 리스트

    formatDate=1 시:
      - 분봉 → "YYYYMMDD  HH:MM:SS" (공백 2개 가능)
      - 일봉 → "YYYYMMDD"
    이상값:
      - volume=-1 → 0 처리
      - OHLC ≤ 0 → 스킵
      - h < l/o/c → 스킵
    """
    import re as _re
    candles = []
    for b in bars:
        try:
            ts_str  = b.get("ts", "").strip()
            ts_clean = _re.sub(r"\s+", " ", ts_str)

            if daily:
                # "YYYYMMDD" 형식
                dt = datetime.strptime(ts_clean[:8], "%Y%m%d")
            else:
                dt = datetime.strptime(ts_clean, "%Y%m%d %H:%M:%S")

            o = float(b.get("open",   0))
            h = float(b.get("high",   0))
            l = float(b.get("low",    0))
            c = float(b.get("close",  0))
            v = max(int(b.get("volume", 0)), 0)

            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                continue
            if h < l or h < o or h < c or l > o or l > c:
                continue

            candles.append(Candle(int(dt.timestamp()), o, h, l, c, v))
        except Exception:
            continue
    return candles


# ══════════════════════════════════════════════════════════════
# 패널
# ══════════════════════════════════════════════════════════════
class StockChartPanel(MinuteChartPanel):
    """IBKR_Stock 전용 차트 패널.
    심볼·날짜는 DataBus(WATCHLIST_SELECT / CHART_DATE_SELECT)로만 제어.
    사이드바 완전 제거 → 차트가 패널 전체 차지.
    """
    PANEL_ID      = "stock_chart"
    TITLE         = "주식 차트"
    DEFAULT_SIZE  = (800, 500)
    _SHOW_SIDEBAR = False   # 사이드바 빌드 자체를 건너뜀

    def __init__(self, bus: DataBus, broker=None, parent=None) -> None:
        super().__init__(bus, parent)
        self._broker = broker
        self._indicators: dict[str, float] = {}

        # 실시간 candle 구독
        bus.subscribe("candle.tick",   self,
                      lambda d: self._invoke_main("_on_tick_inv", d))
        bus.subscribe("candle.closed", self,
                      lambda d: self._invoke_main("_on_closed_inv", d))

        # 지표 구독
        self._indicator_names = ("sma20", "ema9", "rsi14")
        self._update_indicator_subs(self._symbol)

        # 관심종목 선택 → 차트 종목 전환
        bus.subscribe(Topics.WATCHLIST_SELECT, self,
                      lambda d: self._invoke_main("_on_watchlist_select", d))

        # 브로커 연결 완료 → 차트 자동 갱신
        bus.subscribe(Topics.BROKER_CONNECTED, self,
                      lambda d: self._invoke_main("_on_broker_connected", d))

    # ── 로더 주입 ─────────────────────────────────────────────
    def _make_minute_loader(self, symbol: str, target_date: date):
        return IBKRMinuteLoader(symbol, target_date, broker=self._broker)

    def _make_daily_loader(self, symbol: str, days: int = 60):
        return IBKRDailyLoader(symbol, days, broker=self._broker)

    # ── 지표 구독 ─────────────────────────────────────────────
    def _update_indicator_subs(self, symbol: str, old_symbol: str | None = None) -> None:
        if old_symbol:
            for ind in self._indicator_names:
                self._bus.unsubscribe(Topics.indicator(old_symbol, ind), self)
        for ind in self._indicator_names:
            self._bus.subscribe(
                Topics.indicator(symbol, ind), self,
                lambda d, i=ind: self._invoke_main("_on_indicator", d))

    def _set_symbol(self, symbol: str) -> None:
        old = getattr(self, "_symbol", None)
        super()._set_symbol(symbol)
        self._update_indicator_subs(symbol, old)

    # ── _invoke_main 슬롯 ─────────────────────────────────────
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
        ind = data.get("indicator", "")
        val = data.get("value", 0.0)
        self._indicators[ind] = val
        self._update_indicator_labels()

    def _update_indicator_labels(self) -> None:
        parts = []
        if "sma20" in self._indicators:
            parts.append(f"SMA20: {self._indicators['sma20']:.2f}")
        if "ema9" in self._indicators:
            parts.append(f"EMA9: {self._indicators['ema9']:.2f}")
        if "rsi14" in self._indicators:
            rsi  = self._indicators["rsi14"]
            flag = " ▲과매수" if rsi > 70 else (" ▼과매도" if rsi < 30 else "")
            parts.append(f"RSI14: {rsi:.1f}{flag}")
        if parts:
            self._set_status("  |  ".join(parts))

    # ── 관심종목 선택 ─────────────────────────────────────────
    from PyQt6.QtCore import pyqtSlot as _pyqtSlot2

    @_pyqtSlot2(object)
    def _on_watchlist_select(self, data: dict) -> None:
        sym = data.get("symbol", "").upper()
        if sym and sym != getattr(self, "_symbol", ""):
            self._set_symbol(sym)

    @_pyqtSlot2(object)
    def _on_broker_connected(self, data: dict) -> None:
        """브로커 연결 완료 → 오늘 날짜로 실데이터 분봉 차트 갱신"""
        from datetime import date as _date
        from PyQt6.QtCore import QDate
        d = _date.today()
        self._calendar.setSelectedDate(QDate(d.year, d.month, d.day))
        self._refresh()

    def register_shortcuts(self) -> list:
        base = super().register_shortcuts()
        return base + [
            ("Ctrl+R", self._refresh),
        ]
