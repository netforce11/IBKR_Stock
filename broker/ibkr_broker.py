# broker/ibkr_broker.py
# IBKR 브로커 — ib_insync 기반 (lazy import)

from __future__ import annotations
import logging
from core.order_validator import OrderRequest
from broker.base_broker import BaseBroker
from core.bus import DataBus
from core.topics import Topics

log = logging.getLogger(__name__)


class IBKRBroker(BaseBroker):
    def __init__(self, bus: DataBus, host: str, port: int, client_id: int) -> None:
        self._bus = bus
        self._host = host
        self._port = port
        self._client_id = client_id
        self._ib = None  # lazy import

    def connect(self) -> None:
        try:
            from ib_insync import IB, Stock  # lazy import — 미설치 환경 보호
            self._ib = IB()
            self._ib.connect(self._host, self._port, clientId=self._client_id)
            self._ib.errorEvent += self._on_error
            self._bus.publish(Topics.BROKER_CONNECTED, {"broker": "ibkr"})
            log.info("IBKR connected %s:%d", self._host, self._port)
        except ImportError:
            raise RuntimeError("ib_insync 미설치. pip install ib_insync")
        except Exception as e:
            self._bus.publish(Topics.SYSTEM_ERROR, {"msg": str(e)})
            raise

    def disconnect(self) -> None:
        if self._ib:
            self._ib.disconnect()
        self._bus.publish(Topics.BROKER_DISCONNECTED, {"broker": "ibkr"})

    def subscribe(self, symbol: str) -> None:
        from ib_insync import Stock
        contract = Stock(symbol, "SMART", "USD")
        self._ib.reqMktData(contract, "", False, False)
        self._ib.pendingTickersEvent += lambda tickers: self._on_tick(tickers, symbol)

    def unsubscribe(self, symbol: str) -> None:
        from ib_insync import Stock
        contract = Stock(symbol, "SMART", "USD")
        self._ib.cancelMktData(contract)

    def place_order(self, req: OrderRequest) -> str:
        from ib_insync import Stock, MarketOrder, LimitOrder
        contract = Stock(req.symbol, "SMART", "USD")
        if req.order_type == "MKT":
            order = MarketOrder(req.side, req.quantity)
        else:
            order = LimitOrder(req.side, req.quantity, req.price)
        trade = self._ib.placeOrder(contract, order)
        return str(trade.order.orderId)

    def cancel_order(self, order_id: str) -> None:
        from ib_insync import Order
        self._ib.cancelOrder(Order(orderId=int(order_id)))

    def get_positions(self) -> dict:
        return {p.contract.symbol: p.position for p in self._ib.positions()}

    def get_account(self) -> dict:
        vals = self._ib.accountValues()
        balance = next((float(v.value) for v in vals if v.tag == "TotalCashValue"), 0.0)
        return {"balance": balance, "currency": "USD"}

    def _on_tick(self, tickers, symbol: str) -> None:
        for t in tickers:
            if t.contract.symbol == symbol:
                self._bus.publish(Topics.stock_price(symbol), {
                    "symbol": symbol,
                    "price": t.last or t.close,
                    "bid": t.bid,
                    "ask": t.ask,
                })

    def _on_error(self, req_id, code, msg, _contract) -> None:
        log.error("IBKR error %d %d: %s", req_id, code, msg)
        self._bus.publish(Topics.SYSTEM_ERROR, {"code": code, "msg": msg})
