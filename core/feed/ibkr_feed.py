"""
core/feed/ibkr_feed.py
───────────────────────
IBKR 주식 실시간 피드 — IBKRBroker 래퍼.

IBKRBroker는 연결·주문·포지션을 담당.
IBKRFeed는 피드 구독/해제 + 분봉 히스토리 로드 담당.

사용법:
    feed = IBKRFeed(broker, bus)
    feed.subscribe("AAPL")
    feed.subscribe("TSLA")
    feed.load_history("AAPL", "1 D", "1 min", callback=my_cb)
    feed.unsubscribe("AAPL")

DataBus 발행 (IBKRBroker 내부에서 발행):
  stock.price.<SYM>      — 실시간 현재가
  stock.orderbook.<SYM>  — 호가 (bid/ask 1단계)
"""

from __future__ import annotations
import logging
import threading
from typing import Callable, Optional, Set

log = logging.getLogger(__name__)


class IBKRFeed:
    """
    IBKRBroker를 통해 실시간 시세를 구독/해제.
    MockBroker 사용 시에도 동일 인터페이스 동작
    (MockBroker는 자체 피드 스레드로 stock.price.* 발행).
    """

    def __init__(self, broker, bus) -> None:
        self._broker = broker
        self._bus    = bus
        self._subs: Set[str] = set()
        self._lock = threading.Lock()

    def subscribe(self, symbol: str) -> None:
        """실시간 시세 구독"""
        sym = symbol.upper()
        with self._lock:
            if sym in self._subs:
                return
            self._subs.add(sym)
        try:
            self._broker.subscribe_quote(sym)
            log.info("Feed 구독: %s", sym)
        except AttributeError:
            # MockBroker는 subscribe_quote 없음 → MockBroker.subscribe() 직접 호출
            try:
                self._broker.subscribe(sym)
                log.debug("MockBroker.subscribe() 호출: %s", sym)
            except Exception as e:
                log.debug("MockBroker fallback 실패 (%s): %s", sym, e)

    def unsubscribe(self, symbol: str) -> None:
        """실시간 시세 구독 해제"""
        sym = symbol.upper()
        with self._lock:
            self._subs.discard(sym)
        try:
            self._broker.unsubscribe_quote(sym)
            log.info("Feed 구독 해제: %s", sym)
        except AttributeError:
            pass

    def load_history(self, symbol: str,
                     duration: str = "1 D",
                     bar_size: str = "1 min",
                     callback: Optional[Callable] = None) -> None:
        """
        분봉 히스토리 요청.
        callback(bars: list[dict]) — 완료 시 호출 (IBKRBroker 내부 스레드에서).

        bars 형식:
          [{"ts": "20261017 09:30:00 US/Eastern",
            "open": 150.0, "high": 151.2,
            "low": 149.8, "close": 150.9, "volume": 120000}, ...]
        """
        sym = symbol.upper()
        try:
            self._broker.request_historical(sym, duration, bar_size, callback)
            log.info("분봉 요청: %s (%s, %s)", sym, duration, bar_size)
        except AttributeError:
            log.debug("MockBroker — request_historical 미지원")

    @property
    def subscribed(self) -> set:
        with self._lock:
            return set(self._subs)
