# calc/calc_process.py
# 지표 계산 전용 프로세스 — calc_queue 소비 → DataBus 발행

from __future__ import annotations
import queue
import threading
import logging
from core.bus import DataBus
from core.topics import Topics
from calc.indicators import SMA, EMA, RSI

log = logging.getLogger(__name__)


class CalcProcess:
    """
    calc_queue에서 price 이벤트를 받아 지표를 계산하고
    DataBus로 indicator 토픽 발행.
    """

    def __init__(self, bus: DataBus, calc_queue: queue.Queue) -> None:
        self._bus = bus
        self._q = calc_queue
        self._running = False
        self._thread: threading.Thread | None = None

        # symbol → 지표 인스턴스
        self._sma20: dict[str, SMA] = {}
        self._ema9:  dict[str, EMA] = {}
        self._rsi14: dict[str, RSI] = {}

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="CalcProcess", daemon=True
        )
        self._thread.start()
        log.info("CalcProcess started")

    def stop(self) -> None:
        self._running = False
        self._q.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while self._running:
            try:
                msg = self._q.get(timeout=1)
                if msg is None:
                    break
                self._calc(msg)
            except queue.Empty:
                continue
            except Exception:
                log.exception("CalcProcess error")

    def _calc(self, msg: dict) -> None:
        sym = msg.get("symbol")
        price = msg.get("price")
        if not sym or price is None:
            return

        # 지표 인스턴스 초기화 (첫 심볼)
        if sym not in self._sma20:
            self._sma20[sym] = SMA(20)
            self._ema9[sym]  = EMA(9)
            self._rsi14[sym] = RSI(14)

        sma = self._sma20[sym].update(price)
        ema = self._ema9[sym].update(price)
        rsi = self._rsi14[sym].update(price)

        if sma is not None:
            self._bus.publish(Topics.indicator(sym, "sma20"), {"symbol": sym, "value": sma})
        if ema is not None:
            self._bus.publish(Topics.indicator(sym, "ema9"),  {"symbol": sym, "value": ema})
        if rsi is not None:
            self._bus.publish(Topics.indicator(sym, "rsi14"), {"symbol": sym, "value": rsi})
