# ui/panels/chart_panel.py  (IBKR_Stock 전용)
# MinuteChartPanel 상속 → IBKR 로더 주입

from __future__ import annotations
import threading
import logging
from datetime import date, datetime

from shared.ui.chart.minute_chart_panel import MinuteChartPanel
from shared.ui.chart.data_source import BaseMinuteLoader, Candle
from shared.core.bus import DataBus
from shared.core.topics import Topics

log = logging.getLogger(__name__)


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

    # ── IBKR 콜백 (IBKR 소켓 스레드에서 호출됨) ──────────────
    def _on_bars_received(self, bars: list) -> None:
        """request_historical 콜백 — bars: list[dict]"""
        self._bars = bars
        self._done_event.set()

    # ── QThread.run() ────────────────────────────────────────
    def run(self) -> None:
        broker = self._broker
        connected = getattr(broker, "_connected", False)

        if broker is not None and connected:
            self._run_real(broker)
        else:
            self._run_mock()

    def _run_real(self, broker) -> None:
        """실제 IBKR reqHistoricalData 호출"""
        try:
            from datetime import date as _date
            today = _date.today()
            # 오늘이면 "1 D" (장 중 포함), 과거면 해당 날짜까지 조회
            if self.target_date == today:
                duration = "1 D"
                end_dt   = ""          # 현재 시각까지
            else:
                duration = "1 D"
                # endDateTime: "YYYYMMDD HH:MM:SS" 형식 (ET 기준 장 마감)
                end_dt = self.target_date.strftime("%Y%m%d") + " 16:00:00"

            # endDateTime 주입을 위해 request_historical 호출
            # IBKRBroker.request_historical은 endDateTime="" (현재) 고정이므로
            # 과거 날짜는 _ib.reqHistoricalData 직접 호출
            if end_dt:
                self._request_past(broker, end_dt)
            else:
                broker.request_historical(
                    self.symbol, "1 D", "1 min",
                    callback=self._on_bars_received
                )

            # 최대 30초 대기
            if not self._done_event.wait(timeout=30):
                self.error.emit(f"{self.symbol} 분봉 수신 타임아웃 (30s)")
                return

            if self._fetch_error:
                self.error.emit(self._fetch_error)
                return

            candles = self._bars_to_candles(self._bars)
            if candles:
                self.loaded.emit(candles)
            else:
                # 데이터 없음 → Mock으로 폴백
                log.warning("[ChartPanel] %s 실데이터 없음 → Mock 폴백", self.symbol)
                self._run_mock()

        except Exception as e:
            log.exception("[ChartPanel] IBKR 분봉 오류")
            self.error.emit(str(e))

    def _request_past(self, broker, end_dt: str) -> None:
        """과거 날짜 조회 — reqHistoricalData 직접 호출"""
        try:
            from broker.ibkr_broker import _make_stk_contract
            req_id = broker._next_req_id
            broker._next_req_id += 1
            broker._hist_callbacks[req_id] = [self.symbol, [], self._on_bars_received]
            c = _make_stk_contract(self.symbol)
            broker._ib.reqHistoricalData(
                req_id, c,
                end_dt,   # endDateTime
                "1 D",    # duration
                "1 min",  # barSizeSetting
                "TRADES",
                1,        # useRTH
                1,        # formatDate
                False, []
            )
        except Exception as e:
            self._fetch_error = str(e)
            self._done_event.set()

    @staticmethod
    def _bars_to_candles(bars: list) -> list:
        """IBKR bar dict 리스트 → Candle 객체 리스트

        IBKR 이상값 처리:
          - formatDate=1 → "YYYYMMDD  HH:MM:SS" (공백 2개) 또는 "YYYYMMDD HH:MM:SS"
          - volume=-1 → IBKR extended-hours 빈봉, 0으로 처리
          - open/high/low/close ≤ 0 → 파싱 오류 또는 결측봉, 스킵
          - high < low / high < open / high < close → 비정상봉, 스킵
        """
        candles = []
        for b in bars:
            try:
                ts_str = b.get("ts", "").strip()
                # 공백 개수 무관하게 파싱
                import re as _re
                ts_clean = _re.sub(r"\s+", " ", ts_str)
                dt = datetime.strptime(ts_clean, "%Y%m%d %H:%M:%S")

                o = float(b.get("open",   0))
                h = float(b.get("high",   0))
                l = float(b.get("low",    0))
                c = float(b.get("close",  0))
                v = max(int(b.get("volume", 0)), 0)  # -1 → 0

                # 비정상 OHLC 필터
                if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                    continue
                if h < l or h < o or h < c or l > o or l > c:
                    continue

                candles.append(Candle(int(dt.timestamp()), o, h, l, c, v))
            except Exception:
                continue
        return candles

    def _run_mock(self) -> None:
        """개발/테스트용 Mock 랜덤 분봉 데이터"""
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


class StockChartPanel(MinuteChartPanel):
    """IBKR_Stock 전용 차트 패널"""
    PANEL_ID = "stock_chart"
    TITLE = "주식 차트"
    DEFAULT_SIZE = (800, 500)

    def __init__(self, bus: DataBus, broker=None, parent=None) -> None:
        super().__init__(bus, parent)
        self._broker = broker          # IBKRBroker 또는 None
        self._indicators: dict[str, float] = {}

        # 실시간 candle 구독
        bus.subscribe("candle.tick",   self,
                      lambda d: self._invoke_main("_on_tick_inv", d))
        bus.subscribe("candle.closed", self,
                      lambda d: self._invoke_main("_on_closed_inv", d))

        # 지표 구독 — CalcProcess가 publish하는 토픽
        self._indicator_names = ("sma20", "ema9", "rsi14")
        self._update_indicator_subs(self._symbol)

        # 관심종목 선택 → 차트 종목 전환
        bus.subscribe(Topics.WATCHLIST_SELECT, self,
                      lambda d: self._invoke_main("_on_watchlist_select", d))

        # 브로커 연결 완료 → 차트 자동 갱신
        bus.subscribe(Topics.BROKER_CONNECTED, self,
                      lambda d: self._invoke_main("_on_broker_connected", d))

    def _make_minute_loader(self, symbol: str, target_date: date):
        return IBKRMinuteLoader(symbol, target_date, broker=self._broker)

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

    @_pyqtSlot2(object)
    def _on_broker_connected(self, data: dict) -> None:
        """브로커 연결 완료 → 오늘 날짜로 실데이터 차트 갱신"""
        from datetime import date as _date
        from PyQt6.QtCore import QDate
        d = _date.today()
        self._calendar.setSelectedDate(QDate(d.year, d.month, d.day))
        self._refresh()

    def register_shortcuts(self) -> list:
        base = super().register_shortcuts()
        return base + [
            ("Ctrl+R", self._refresh),   # 새로고침 추가 단축키
        ]
