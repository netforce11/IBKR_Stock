# core/engine.py  (IBKR_Stock 전용)
# Trading Engine — cmd_queue 수신 → 주문 처리 → DataBus 발행
# Core는 PyQt를 절대 import하지 않음

from __future__ import annotations
import queue
import threading
import logging
from typing import Any

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.core.order_validator import OrderValidator, OrderRequest
from shared.core.risk_manager import RiskManager

log = logging.getLogger(__name__)


class TradingEngine:
    def __init__(
        self,
        bus: DataBus,
        cmd_queue: queue.Queue,
        risk: RiskManager,
        broker,
    ) -> None:
        self._bus = bus
        self._cmd_queue = cmd_queue
        self._risk = risk
        self._broker = broker
        self._validator = OrderValidator()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="TradingEngine", daemon=True
        )
        self._thread.start()
        log.info("TradingEngine started")

    def stop(self) -> None:
        self._running = False
        self._cmd_queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while self._running:
            try:
                cmd: dict[str, Any] | None = self._cmd_queue.get(timeout=1)
                if cmd is None:
                    break
                self._dispatch(cmd)
            except queue.Empty:
                continue
            except Exception:
                log.exception("Engine loop error")

    def _dispatch(self, cmd: dict) -> None:
        action = cmd.get("action")
        if action == "place_order":
            self._place_order(cmd)
        elif action == "cancel_order":
            self._cancel_order(cmd)
        elif action == "subscribe_feed":
            self._broker.subscribe(cmd["symbol"])
        elif action == "unsubscribe_feed":
            self._broker.unsubscribe(cmd["symbol"])
        else:
            log.warning("Unknown command: %s", action)

    def _place_order(self, cmd: dict) -> None:
        req = OrderRequest(
            symbol=cmd["symbol"],
            side=cmd["side"],
            quantity=cmd.get("quantity") or cmd.get("qty"),
            price=cmd.get("price"),
            order_type=cmd.get("order_type", "MKT"),
            fractional=cmd.get("fractional", False),
        )
        v = self._validator.validate(req)
        if not v.ok:
            self._bus.publish(Topics.SYSTEM_ERROR, {"msg": f"주문 검증 실패: {v.reason}"})
            return

        r = self._risk.check_order(req)
        if not r.ok:
            self._bus.publish(Topics.RISK_WARNING, {"msg": r.reason})
            return

        try:
            order_id = self._broker.place_order(req)
            # ── DB 저장 + UI 갱신용 토픽 발행 ─────────────
            self._bus.publish(Topics.ORDER_PLACED, {
                "order_id":   order_id,
                "symbol":     req.symbol,
                "side":       req.side,
                "qty":        req.quantity,
                "price":      req.price,
                "order_type": req.order_type,
            })
            self._bus.publish(Topics.order_status(order_id), {"status": "submitted"})
        except Exception as e:
            log.exception("Broker place_order error")
            self._bus.publish(Topics.SYSTEM_ERROR, {"msg": str(e)})

    def _cancel_order(self, cmd: dict) -> None:
        order_id = cmd.get("order_id", "")
        try:
            self._broker.cancel_order(order_id)
            # ── DB 저장 + UI 갱신용 토픽 발행 ─────────────
            self._bus.publish(Topics.ORDER_CANCELLED, {
                "order_id": order_id,
                "symbol":   cmd.get("symbol", ""),
            })
        except Exception as e:
            self._bus.publish(Topics.SYSTEM_ERROR, {"msg": str(e)})
