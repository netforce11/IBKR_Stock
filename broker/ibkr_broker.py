"""
broker/ibkr_broker.py
─────────────────────
IBKR TWS API 주식 브로커 구현체 (옵션 코드 완전 제거)

BaseBroker 인터페이스 준수 → MockBroker와 교체 가능.

연결:
  paper trade: host=127.0.0.1, port=7497
  live trade:  host=127.0.0.1, port=7496

필요 패키지:
  pip install ibapi   (Interactive Brokers 공식 Python API)
  TWS 또는 IB Gateway 실행 필요

DataBus 발행 토픽:
  broker.connected        — 연결 성공
  broker.disconnected     — 연결 해제
  stock.price.<SYM>       — 실시간 시세
  stock.orderbook.<SYM>   — 호가 (bid/ask)
  order.placed            — 주문 접수
  order.filled            — 체결
  order.cancelled         — 취소
  portfolio.position.<SYM>— 포지션 업데이트
  account.balance         — 잔고 업데이트
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from queue import Empty, Queue as SyncQueue
from typing import Any, Dict, Optional

from shared.broker.base_broker import BaseBroker
from shared.core.topics import Topics

log = logging.getLogger(__name__)

# ibapi lazy import — 미설치 환경 보호
try:
    from ibapi.client  import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order   import Order as IBOrder
    IBAPI_OK = True
except ImportError:
    IBAPI_OK = False
    log.warning("ibapi not installed — IBKRBroker 사용 불가 (MockBroker 사용 권장)")


# ── 주식 계약 생성 헬퍼 ─────────────────────────────────────────
def _make_stk_contract(symbol: str, exchange: str = "SMART",
                       currency: str = "USD") -> Any:
    """주식(STK) 계약 객체 생성"""
    if not IBAPI_OK:
        return None
    c = Contract()
    c.symbol   = symbol.upper()
    c.secType  = "STK"
    c.exchange = exchange
    c.currency = currency
    return c


def _make_ibkr_order(action: str, qty: int,
                     order_type: str, price: float) -> Any:
    """IBKR 주문 객체 생성"""
    if not IBAPI_OK:
        return None
    o = IBOrder()
    o.eTradeOnly    = False
    o.firmQuoteOnly = False
    o.action        = action.upper()         # "BUY" | "SELL"
    o.totalQuantity = qty
    o.orderType     = order_type.upper()     # "LMT" | "MKT"
    o.tif           = "DAY"
    o.transmit      = True
    if order_type.upper() in ("LMT", "LIMIT"):
        o.lmtPrice = round(price, 2)
    return o


# ── EWrapper + EClient 통합 ─────────────────────────────────────
if IBAPI_OK:
    class _IBWrapper(EWrapper, EClient):
        """IBKR API 이벤트 수신 + 송신 통합 클래스"""

        def __init__(self, broker: "IBKRBroker") -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self._b = broker

        # ── 연결 이벤트 ───────────────────────────────────────
        def connectAck(self) -> None:
            log.info("[IBKRBroker] 연결 ACK")

        def nextValidId(self, order_id: int) -> None:
            self._b._next_order_id = order_id
            self._b._connected     = True
            self._b._connect_event.set()
            log.info("[IBKRBroker] nextValidId=%d — 연결 완료", order_id)

        def connectionClosed(self) -> None:
            self._b._connected = False
            log.warning("[IBKRBroker] 연결 해제")
            self._b._bus.publish(Topics.BROKER_DISCONNECTED,
                                 {"broker": "ibkr", "ts": time.time()})

        def error(self, req_id: int, error_code: int,
                  error_string: str, advanced_order_reject: str = "") -> None:
            # 무시해도 되는 정보성 코드
            _INFO = {2100, 2103, 2104, 2105, 2106, 2107,
                     2108, 2109, 2119, 2158, 2168, 300, 10167, 101}
            if error_code in _INFO:
                return
            log.error("[IBKR] [%d] req=%d: %s", error_code, req_id, error_string)
            # 326: client ID 중복 → connect() 재시도 신호
            if error_code == 326:
                self._b._connect_error_code = 326
                self._b._connect_event.set()

        # ── 시세 콜백 ─────────────────────────────────────────
        def tickPrice(self, req_id: int, tick_type: int,
                      price: float, attrib: Any) -> None:
            self._b._on_tick_price(req_id, tick_type, price)

        def tickSize(self, req_id: int, tick_type: int, size: int) -> None:
            self._b._on_tick_size(req_id, tick_type, size)

        # ── 주문/체결 콜백 ────────────────────────────────────
        def orderStatus(self, order_id: int, status: str, filled: float,
                        remaining: float, avg_fill_price: float,
                        perm_id: int, parent_id: int, last_fill_price: float,
                        client_id: int, why_held: str, mkt_cap_price: float) -> None:
            self._b._on_order_status(
                order_id, status, filled, avg_fill_price)

        def execDetails(self, req_id: int, contract: Any, execution: Any) -> None:
            self._b._on_exec(contract, execution)

        # ── 포지션/잔고 콜백 ─────────────────────────────────
        def position(self, account: str, contract: Any,
                     position: float, avg_cost: float) -> None:
            if contract.secType != "STK":
                return
            self._b._bus.publish(
                Topics.portfolio_position(contract.symbol),
                {
                    "symbol":   contract.symbol,
                    "qty":      int(position),
                    "avg_cost": round(avg_cost, 4),
                })

        def positionEnd(self) -> None:
            pass

        def accountSummary(self, req_id: int, account: str,
                           tag: str, value: str, currency: str) -> None:
            self._b._on_account_summary(tag, value)

        def accountSummaryEnd(self, req_id: int) -> None:
            pass

        # ── 분봉 히스토리 콜백 ────────────────────────────────
        def historicalData(self, req_id: int, bar: Any) -> None:
            self._b._on_historical_bar(req_id, bar)

        def historicalDataEnd(self, req_id: int,
                              start: str, end: str) -> None:
            self._b._on_historical_end(req_id)


# ── IBKRBroker ─────────────────────────────────────────────────
class IBKRBroker(BaseBroker):
    """
    IBKR TWS API 주식 브로커.
    BaseBroker 인터페이스 구현 → MockBroker와 교체 가능.
    """

    def __init__(self, bus, host: str = "127.0.0.1",
                 port: int = 7497, client_id: int = 1) -> None:
        self._bus       = bus
        self._host      = host
        self._port      = port
        self._client_id = client_id

        self._connected          = False
        self._connect_event      = threading.Event()
        self._connect_error_code = 0   # Error 326 등 연결 실패 코드 저장
        self._next_order_id = 1
        self._ib: Optional[_IBWrapper] = None
        self._ib_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # reqId 관리 (시세 구독별 ID)
        self._next_req_id  = 100
        self._sym_req: Dict[str, int] = {}   # symbol → req_id
        self._req_sym: Dict[int, str] = {}   # req_id → symbol

        # 시세 캐시 (tickPrice 여러 타입 조합)
        self._price_cache: Dict[str, Dict] = {}

        # 주문 추적: order_id → internal_id
        self._order_map: Dict[int, str] = {}

        # 분봉 요청 콜백
        self._hist_callbacks: Dict[int, list] = {}

    # ── BaseBroker 인터페이스 ────────────────────────────────
    def connect(self) -> None:
        if not IBAPI_OK:
            raise RuntimeError(
                "ibapi 미설치 — 'pip install ibapi' 후 TWS를 실행하세요.")

        _MAX_RETRY = 5
        for attempt in range(_MAX_RETRY):
            # 매 시도마다 이벤트/오류 코드 초기화
            self._connect_event.clear()
            self._connect_error_code = 0

            self._ib = _IBWrapper(self)
            self._ib.connect(self._host, self._port, self._client_id)

            self._ib_thread = threading.Thread(
                target=self._ib.run, daemon=True,
                name=f"IBKR-Socket-{self._client_id}")
            self._ib_thread.start()

            # nextValidId 수신 대기 (최대 10초)
            if not self._connect_event.wait(timeout=10):
                raise RuntimeError(
                    f"IBKR 연결 실패 — TWS/Gateway가 {self._host}:{self._port}에서 실행 중인지 확인")

            # Error 326: client ID 중복 → client_id 증가 후 재시도
            if self._connect_error_code == 326:
                log.warning(
                    "[IBKRBroker] client_id=%d 중복 (Error 326) — "
                    "client_id=%d 로 재시도 (%d/%d)",
                    self._client_id, self._client_id + 1, attempt + 1, _MAX_RETRY)
                try:
                    self._ib.disconnect()
                except Exception:
                    pass
                self._client_id += 1
                continue

            # 정상 연결
            break
        else:
            raise RuntimeError(
                f"IBKR client ID 중복으로 {_MAX_RETRY}회 재시도 실패 "
                f"(마지막 client_id={self._client_id})")

        self._bus.publish(Topics.BROKER_CONNECTED,
                          {"broker": "ibkr",
                           "host": self._host, "port": self._port})

        # 포지션 + 잔고 구독 시작
        self._ib.reqPositions()
        self._ib.reqAccountSummary(
            self._next_req_id, "All",
            "NetLiquidation,TotalCashValue,BuyingPower,UnrealizedPnL")
        self._next_req_id += 1
        log.info("IBKRBroker 연결 성공 — %s:%d", self._host, self._port)

    def disconnect(self) -> None:
        if self._ib:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        log.info("IBKRBroker 연결 해제")

    # ── BaseBroker 필수 메서드: subscribe / unsubscribe ────────
    def subscribe(self, symbol: str) -> None:
        """BaseBroker 인터페이스 — subscribe_quote로 위임"""
        self.subscribe_quote(symbol)

    def unsubscribe(self, symbol: str) -> None:
        """BaseBroker 인터페이스 — unsubscribe_quote로 위임"""
        self.unsubscribe_quote(symbol)

    def get_positions(self) -> dict:
        """보유 포지션 반환 (symbol → qty). 미연결 시 빈 dict."""
        return dict(self._price_cache)   # 임시: 구독 중인 종목 목록 반환

    def get_account(self) -> dict:
        """계좌 정보 반환. 미연결 시 빈 dict."""
        return {}   # TODO: reqAccountSummary 구현 시 채우기

    def subscribe_quote(self, symbol: str) -> None:
        """실시간 시세 구독 (reqMktData)"""
        if not self._connected or not self._ib:
            return
        sym = symbol.upper()
        with self._lock:
            if sym in self._sym_req:
                return
            req_id = self._next_req_id
            self._next_req_id += 1
            self._sym_req[sym] = req_id
            self._req_sym[req_id] = sym
            self._price_cache[sym] = {"bid": 0.0, "ask": 0.0, "last": 0.0}

        c = _make_stk_contract(sym)
        # tick_type 233 = RT Volume (last/size/time), 165 = misc stats
        self._ib.reqMktData(req_id, c, "233,165", False, False, [])
        log.info("구독: %s reqId=%d", sym, req_id)

    def unsubscribe_quote(self, symbol: str) -> None:
        sym = symbol.upper()
        with self._lock:
            req_id = self._sym_req.pop(sym, None)
            if req_id:
                self._req_sym.pop(req_id, None)
        if req_id and self._ib:
            try:
                self._ib.cancelMktData(req_id)
            except Exception:
                pass

    def place_order(self, req) -> str:
        """
        BaseBroker 인터페이스 — OrderRequest 또는 키워드 dict 모두 허용.
        → order_id(internal) 반환
        """
        # OrderRequest dataclass 또는 dict 양쪽 지원
        if hasattr(req, "symbol"):
            symbol     = req.symbol
            side       = req.side
            qty        = req.quantity
            price      = req.price or 0.0
            order_type = req.order_type
        else:
            symbol     = req["symbol"]
            side       = req["side"]
            qty        = req["quantity"]
            price      = req.get("price", 0.0) or 0.0
            order_type = req.get("order_type", "LMT")

        if not self._connected or not self._ib:
            raise RuntimeError("IBKR 미연결")

        internal_id = str(uuid.uuid4())[:8]
        order_id    = self._next_order_id
        self._next_order_id += 1
        self._order_map[order_id] = internal_id

        c = _make_stk_contract(symbol)
        o = _make_ibkr_order(side, qty, order_type, price)

        self._ib.placeOrder(order_id, c, o)

        self._bus.publish("order.placed", {
            "order_id":   internal_id,
            "ibkr_id":    order_id,
            "symbol":     symbol.upper(),
            "side":       side.upper(),
            "qty":        qty,
            "price":      price,
            "order_type": order_type.upper(),
        })
        log.info("주문 발행: %s %s %d @ %s [%s]",
                 side, symbol, qty, price, order_type)
        return internal_id

    def cancel_order(self, order_id: str) -> None:
        # internal_id → ibkr order_id 역방향 탐색
        for ibkr_id, iid in self._order_map.items():
            if iid == order_id:
                if self._ib:
                    self._ib.cancelOrder(ibkr_id, "")
                break

    def cancel_all(self) -> None:
        if self._ib:
            self._ib.reqGlobalCancel()

    def request_historical(self, symbol: str, duration: str = "1 D",
                           bar_size: str = "1 min",
                           callback=None) -> None:
        """
        분봉 히스토리 요청.
        callback(bars: list[dict]) 으로 결과 반환.
        bars = [{"ts":..., "open":..., "high":..., "low":...,
                 "close":..., "volume":...}, ...]
        """
        if not self._connected or not self._ib:
            return
        req_id = self._next_req_id
        self._next_req_id += 1
        self._hist_callbacks[req_id] = [symbol, [], callback]

        c = _make_stk_contract(symbol)
        self._ib.reqHistoricalData(
            req_id, c,
            "",            # endDateTime = 현재
            duration,      # "1 D", "5 D", ...
            bar_size,      # "1 min", "5 mins", ...
            "TRADES",
            1,             # useRTH=1 정규장 시간
            1,             # formatDate=1 (문자열)
            False, []
        )

    # ── IBKR 콜백 핸들러 ────────────────────────────────────
    def _on_tick_price(self, req_id: int,
                       tick_type: int, price: float) -> None:
        """
        tick_type:
          1 = Bid, 2 = Ask, 4 = Last, 6 = High, 7 = Low, 9 = Close
        """
        sym = self._req_sym.get(req_id)
        if not sym or price <= 0:
            return
        cache = self._price_cache.setdefault(sym, {})

        if tick_type == 1:
            cache["bid"] = price
        elif tick_type == 2:
            cache["ask"] = price
        elif tick_type == 4:
            cache["last"] = price
        elif tick_type == 9:
            # Close = 전일 종가 (장 중/장 후 모두 수신됨)
            cache["prev_close"] = price

        # last/bid/ask 중 하나라도 갱신되면 publish
        last       = cache.get("last") or cache.get("bid", 0)
        bid        = cache.get("bid", 0)
        ask        = cache.get("ask", 0)
        prev_close = cache.get("prev_close", 0.0)
        if last > 0:
            self._bus.publish(Topics.stock_price(sym), {
                "symbol":     sym,
                "price":      last,
                "bid":        bid,
                "ask":        ask,
                "prev_close": prev_close,
                "ts":         time.time(),
            })

    def _on_tick_size(self, req_id: int,
                      tick_type: int, size: int) -> None:
        """틱 수량 — 현재는 호가 잔량에만 사용"""
        sym = self._req_sym.get(req_id)
        if not sym:
            return
        # tick_type 0=Bid Size, 3=Ask Size → 호가 잔량 (간략 발행)
        if tick_type in (0, 3):
            cache = self._price_cache.get(sym, {})
            bid   = cache.get("bid", 0)
            ask   = cache.get("ask", 0)
            if bid > 0 or ask > 0:
                side = "bid" if tick_type == 0 else "ask"
                self._bus.publish(Topics.stock_orderbook(sym), {
                    "symbol": sym,
                    "bids": [[bid, size]] if side == "bid" else [],
                    "asks": [[ask, size]] if side == "ask" else [],
                    "ts":   time.time(),
                })

    def _on_order_status(self, order_id: int, status: str,
                         filled: float, avg_price: float) -> None:
        internal_id = self._order_map.get(order_id, str(order_id))
        log.debug("orderStatus: ibkr=%d status=%s filled=%s avg=%.2f",
                  order_id, status, filled, avg_price)

        if status in ("Filled", "Submitted"):
            self._bus.publish(
                Topics.order_status(internal_id),
                {"order_id": internal_id, "status": status,
                 "filled": filled, "avg_price": avg_price})

    def _on_exec(self, contract: Any, execution: Any) -> None:
        """체결 상세 — fills 테이블 저장용"""
        if contract.secType != "STK":
            return
        ibkr_id     = execution.orderId
        internal_id = self._order_map.get(ibkr_id, str(ibkr_id))
        self._bus.publish("order.filled", {
            "order_id":   internal_id,
            "fill_id":    execution.execId,
            "symbol":     contract.symbol,
            "side":       execution.side,     # "BOT" | "SLD"
            "qty":        int(execution.shares),
            "fill_price": round(execution.price, 4),
            "commission": 0.0,  # commissionReport 콜백으로 별도 수신
            "ts":         time.time(),
        })
        log.info("체결: %s %s %d @ %.2f",
                 execution.side, contract.symbol,
                 int(execution.shares), execution.price)

    def _on_account_summary(self, tag: str, value: str) -> None:
        try:
            self._bus.publish(Topics.ACCOUNT_BALANCE, {
                "tag": tag, "value": float(value)})
        except (ValueError, TypeError):
            pass

    def _on_historical_bar(self, req_id: int, bar: Any) -> None:
        entry = self._hist_callbacks.get(req_id)
        if not entry:
            return
        try:
            entry[1].append({
                "ts":     bar.date,
                "open":   bar.open,
                "high":   bar.high,
                "low":    bar.low,
                "close":  bar.close,
                "volume": bar.volume,
            })
        except Exception as e:
            log.error("_on_historical_bar error: %s", e)

    def _on_historical_end(self, req_id: int) -> None:
        entry = self._hist_callbacks.pop(req_id, None)
        if entry:
            symbol, bars, callback = entry
            log.info("분봉 수신 완료: %s — %d bars", symbol, len(bars))
            if callback:
                try:
                    callback(bars)
                except Exception as e:
                    log.error("historical callback error: %s", e)
