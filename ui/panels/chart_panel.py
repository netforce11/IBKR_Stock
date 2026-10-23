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
def _et_now() -> datetime:
    """현재 미국 동부시간 (서머타임 자동 반영)"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone, timedelta
        return datetime.now(timezone(timedelta(hours=-5)))


def _is_regular_session(et: datetime) -> bool:
    """미국 정규장(평일 09:30~16:00 ET) 여부 — 공휴일은 판단하지 않음"""
    if et.weekday() >= 5:
        return False
    hm = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= hm < 16 * 60


class StockChartPanel(MinuteChartPanel):
    """IBKR_Stock 전용 차트 패널.
    심볼·날짜는 DataBus(WATCHLIST_SELECT / CHART_DATE_SELECT)로만 제어.
    사이드바 완전 제거 → 차트가 패널 전체 차지.

    [실시간 1분봉]
      - stock.price.<SYM> 틱으로 진행 중인 봉(live bar) 고가/저가/종가 갱신
      - 1분 경계마다 진행 봉을 확정하고, 3초 뒤 IBKR 최근 30분 분봉을
        다시 받아 거래량 포함 정확한 값으로 교체
      - 상단 바에 "다음 봉까지 N초" 카운트다운 표시
      - 이미 1분봉 화면에서 [1분봉] 버튼 클릭 → 즉시 다시 불러오기
    """
    _LIVE_REFRESH_DELAY_MS = 3_000      # 1분 경계 후 분봉 재조회까지 대기
    _LIVE_REFRESH_DURATION = "1800 S"   # 재조회 범위 (최근 30분)
    PANEL_ID      = "stock_chart"
    TITLE         = "주식 차트"
    DEFAULT_SIZE  = (800, 500)
    _SHOW_SIDEBAR = False   # 사이드바 빌드 자체를 건너뜀

    def __init__(self, bus: DataBus, broker=None, parent=None) -> None:
        super().__init__(bus, parent)
        self._broker = broker
        self._indicators: dict[str, float] = {}

        # 가격/거래량 Y축을 오른쪽으로 이동
        self._move_y_axis_right()

        # 실시간 candle 구독 (Topics 정적 메서드 사용 — 문자열 하드코딩 금지)
        bus.subscribe(Topics.CANDLE_TICK,   self,
                      lambda d: self._invoke_main("_on_tick_inv", d))
        bus.subscribe(Topics.CANDLE_CLOSED, self,
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

        # ── 실시간 1분봉 ──────────────────────────────────────
        self._price_sub_sym: str | None = None
        self._last_et_minute: int | None = None
        self._live_refresh_busy = False
        self._subscribe_price(self._symbol)
        self._build_countdown_label()

        from PyQt6.QtCore import QTimer
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._on_clock)
        self._clock.start()
        self._on_clock()

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
        if hasattr(self, "_clock"):          # __init__ 완료 후에만
            self._subscribe_price(self._symbol)

    # ── Y축 오른쪽 배치 ──────────────────────────────────────
    _Y_AXIS_WIDTH = 70   # 가격·거래량 축 폭을 같게 맞춰 두 차트 x 정렬 유지

    def _move_y_axis_right(self) -> None:
        for name, label in (("_p1", "가격"), ("_p2", "거래량")):
            pl = getattr(self, name, None)
            if pl is None:
                continue
            pl.hideAxis("left")
            pl.showAxis("right")
            pl.setLabel("right", label)
            pl.getAxis("right").setWidth(self._Y_AXIS_WIDTH)
        self.apply_theme()

    def apply_theme(self, bg: str = "#FFF5F7", fg: str = "#1E293B",
                    border: str = "#E8C8D0") -> None:
        super().apply_theme(bg, fg, border)
        for name in ("_p1", "_p2"):
            pl = getattr(self, name, None)
            if pl is None:
                continue
            try:
                pl.getAxis("right").setTextPen(fg)
                pl.getAxis("right").setPen(border)
            except Exception:
                pass

    # ── 1분봉 버튼: 이미 1분봉이면 다시 불러오기 ────────────────
    def _set_chart_mode(self, mode: str) -> None:
        if mode == self._chart_mode == "minute":
            self._btn_minute.setChecked(True)   # 토글 해제 방지
            self._refresh()
            return
        super()._set_chart_mode(mode)

    # ── 실시간 1분봉 ─────────────────────────────────────────
    def _build_countdown_label(self) -> None:
        from PyQt6.QtWidgets import QLabel
        self._countdown_lbl = QLabel("")
        self._countdown_lbl.setStyleSheet(
            "color:#FBBF24; font-size:12px; font-weight:bold; padding:0 6px;")
        self._countdown_lbl.setToolTip("다음 1분봉 시작까지 남은 시간 (미국 동부시간 기준)")
        try:
            bar_lay = self._btn_minute.parentWidget().layout()
            idx = bar_lay.indexOf(self._ratio_combo)
            bar_lay.insertWidget(idx + 1 if idx >= 0 else 2, self._countdown_lbl)
        except Exception:
            log.debug("카운트다운 라벨 배치 실패", exc_info=True)

    def _subscribe_price(self, symbol: str) -> None:
        sym = (symbol or "").upper()
        if not sym or sym == self._price_sub_sym:
            return
        if self._price_sub_sym:
            self._bus.unsubscribe(Topics.stock_price(self._price_sub_sym), self)
        self._price_sub_sym = sym
        self._bus.subscribe(Topics.stock_price(sym), self,
                            lambda d: self._invoke_main("_on_price_tick", d))
        # 시세 구독 보장 (관심종목에 없는 종목도 실시간 틱 수신)
        sub = getattr(self._broker, "subscribe_quote", None)
        if sub and getattr(self._broker, "_connected", False):
            try:
                sub(sym)
            except Exception:
                log.debug("subscribe_quote 실패: %s", sym, exc_info=True)

    def _is_live_view(self) -> bool:
        """오늘 날짜 1분봉 단일일 보기인지"""
        if self._chart_mode != "minute":
            return False
        if getattr(self, "_chk_multiday", None) and self._chk_multiday.isChecked():
            return False
        try:
            return self._calendar.selectedDate().toPyDate() == date.today() \
                or self._calendar.selectedDate().toPyDate() == _et_now().date()
        except Exception:
            return False

    def _on_clock(self) -> None:
        """1초 타이머: 카운트다운 표시 + 1분 경계 처리"""
        et = _et_now()
        live = self._is_live_view()
        if not live:
            self._countdown_lbl.setText("")
        elif _is_regular_session(et):
            self._countdown_lbl.setText(f"⏱ 다음 봉 {60 - et.second:2d}초")
        else:
            self._countdown_lbl.setText("⏸ 정규장 외")

        minute_key = et.hour * 60 + et.minute
        if self._last_et_minute is None:
            self._last_et_minute = minute_key
            return
        if minute_key != self._last_et_minute:
            self._last_et_minute = minute_key
            if live and self._candles and _is_regular_session(et):
                self._close_live_bar()
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(self._LIVE_REFRESH_DELAY_MS,
                                  self._refresh_recent_bars)

    from PyQt6.QtCore import pyqtSlot as _slot_tick

    @_slot_tick(object)
    def _on_price_tick(self, data: dict) -> None:
        """현재가 틱 → 진행 중인 1분봉 갱신"""
        if data.get("symbol", "").upper() != self._symbol:
            return
        if not self._is_live_view() or not self._candles:
            return
        if not _is_regular_session(_et_now()):
            return
        price = float(data.get("price") or 0)
        if price <= 0:
            return
        lb = self._live_bar
        if not lb:
            lb = {"symbol": self._symbol, "open": price, "high": price,
                  "low": price, "close": price, "volume": 0,
                  "t": self._candles[-1].t + 60}
        else:
            lb = dict(lb)
            lb["high"]  = max(float(lb.get("high", price)), price)
            lb["low"]   = min(float(lb.get("low", price)), price)
            lb["close"] = price
        self._live_bar = lb
        self._render_live_tick()

    def _on_minute_loaded(self, candles: list) -> None:
        """분봉 로드 완료 — 장중이면 마지막(진행 중) 봉을 live bar 로 분리"""
        super()._on_minute_loaded(candles)
        if (len(self._candles) > 1 and self._is_live_view()
                and _is_regular_session(_et_now())
                and getattr(self._broker, "_connected", False)):
            cur = self._candles.pop()
            self._live_bar = {"symbol": self._symbol, "open": cur.o,
                              "high": cur.h, "low": cur.l, "close": cur.c,
                              "volume": cur.v, "t": cur.t}
            self._render_full()
            self._render_live_tick()

    def _close_live_bar(self) -> None:
        """1분 경계: 진행 봉을 확정 봉으로 추가 (거래량은 재조회 시 보정)"""
        lc = self._live_bar_as_candle()
        self._live_bar = None
        if lc is None:
            return
        self._candles.append(lc)
        self._render_full()

    def _refresh_recent_bars(self) -> None:
        """최근 30분 분봉 재조회 → 차트 끝부분을 정확한 OHLCV 로 교체"""
        broker = self._broker
        if (self._live_refresh_busy or not self._is_live_view()
                or not getattr(broker, "_connected", False)
                or not hasattr(broker, "request_historical")):
            return
        self._live_refresh_busy = True
        sym = self._symbol
        try:
            broker.request_historical(
                sym, self._LIVE_REFRESH_DURATION, "1 min",
                callback=lambda bars, s=sym: self._on_recent_bars(s, bars))
        except Exception:
            self._live_refresh_busy = False
            log.exception("최근 분봉 재조회 실패")

    def _on_recent_bars(self, sym: str, bars: list) -> None:
        # 콜백은 drain_tick_queue(Qt 메인 스레드)에서 호출됨
        self._live_refresh_busy = False
        try:
            if sym != self._symbol or not self._is_live_view():
                return
            fresh = _bars_to_candles(bars)
            if len(fresh) < 1 or not self._candles:
                return
            # 마지막 봉 = 진행 중인 봉 → live bar 로, 나머지는 확정 봉
            closed, current = fresh[:-1], fresh[-1]
            first_t = fresh[0].t
            kept = [c for c in self._candles if c.t < first_t]
            self._candles = kept + closed
            prev_live = self._live_bar or {}
            self._live_bar = {
                "symbol": sym,
                "open":   current.o,
                "high":   max(current.h, float(prev_live.get("high", 0) or 0)),
                "low":    min(current.l, float(prev_live.get("low", current.l)
                                               or current.l)),
                "close":  float(prev_live.get("close", 0) or current.c),
                "volume": current.v,
                "t":      current.t,
            }
            self._render_full()
            self._render_live_tick()
        except Exception:
            log.exception("최근 분봉 병합 오류")

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

    def closeEvent(self, e) -> None:
        if hasattr(self, "_clock"):
            self._clock.stop()
        super().closeEvent(e)

    def register_shortcuts(self) -> list:
        base = super().register_shortcuts()
        return base + [
            ("Ctrl+R", self._refresh),
        ]
