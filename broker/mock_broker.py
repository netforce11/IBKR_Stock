# broker/mock_broker.py
# 개발/테스트용 — 실제 브로커 없이 동작 보장

from __future__ import annotations
import uuid
import random
import threading
import logging
from core.order_validator import OrderRequest
from broker.base_broker import BaseBroker
from core.bus import DataBus
from core.topics import Topics

log = logging.getLogger(__name__)


class MockBroker(BaseBroker):
    def __init__(self, bus: DataBus) -> None:
        self._bus = bus
        self._positions: dict[str, int] = {}
        self._balance = 100_000.0
        self._feed_threads: dict[str, threading.Thread] = {}

    def connect(self) -> None:
        log.info("MockBroker connected")
        self._bus.publish(Topics.BROKER_CONNECTED, {"broker": "mock"})

    def disconnect(self) -> None:
        for sym in list(self._feed_threads):
            self.unsubscribe(sym)
        log.info("MockBroker disconnected")
        self._bus.publish(Topics.BROKER_DISCONNECTED, {"broker": "mock"})

    def subscribe(self, symbol: str) -> None:
        if symbol in self._feed_threads:
            return
        stop_evt = threading.Event()
        t = threading.Thread(
            target=self._fake_feed, args=(symbol, stop_evt),
            name=f"MockFeed-{symbol}", daemon=True
        )
        t.start()
        self._feed_threads[symbol] = (t, stop_evt)  # type: ignore
        log.debug("MockBroker: subscribed %s", symbol)

    def unsubscribe(self, symbol: str) -> None:
        if symbol not in self._feed_threads:
            return
        _t, stop_evt = self._feed_threads.pop(symbol)
        stop_evt.set()

    def place_order(self, req: OrderRequest) -> str:
        order_id = str(uuid.uuid4())[:8]
        log.info("MockBroker: order %s %s %s x%d", order_id, req.side, req.symbol, req.quantity)
        # 즉시 체결 시뮬레이션
        fill_price = round(random.uniform(100, 500), 2)
        self._bus.publish(Topics.order_fill(order_id), {
            "order_id": order_id, "symbol": req.symbol,
            "side": req.side, "qty": req.quantity, "price": fill_price
        })
        return order_id

    def cancel_order(self, order_id: str) -> None:
        log.info("MockBroker: cancel %s", order_id)

    def get_positions(self) -> dict:
        return dict(self._positions)

    def get_account(self) -> dict:
        return {"balance": self._balance, "currency": "USD"}

    # ── 가짜 시세 피드 ─────────────────────────────────────
    def _fake_feed(self, symbol: str, stop: threading.Event) -> None:
        import time
        price = random.uniform(100, 500)
        while not stop.is_set():
            price += random.gauss(0, 0.5)
            price = max(1.0, round(price, 2))
            bid = round(price - 0.01, 2)
            ask = round(price + 0.01, 2)
            self._bus.publish(Topics.stock_price(symbol), {
                "symbol": symbol, "price": price, "bid": bid, "ask": ask
            })
            self._bus.publish(Topics.stock_orderbook(symbol), {
                "symbol": symbol,
                "bids": [(bid, random.randint(100, 1000)) for _ in range(5)],
                "asks": [(ask, random.randint(100, 1000)) for _ in range(5)],
            })
            time.sleep(0.5)
