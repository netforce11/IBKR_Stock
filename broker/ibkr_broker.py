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


def _make_ibkr_order(action: str, qty: float,
                     order_type: str, price: float,
                     fractional: bool = False) -> Any:
    """IBKR 주문 객체 생성 (소수 주문 지원)"""
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
    if fractional:
        # IBKR fractional share: cashQty 또는 totalQuantity 소수 그대로
        o.totalQuantity = qty   # ibapi 2.x 이상에서 float 허용
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
            # 스캐너 보강용 시세 / 계약정보 요청 오류 → 메인 스레드에서 해당 요청 종료 처리
            if (req_id in self._b._enrich_req
                    or req_id in self._b._cd_callbacks):
                log.debug("[IBKR] [%d] req=%d (보조 요청): %s",
                          error_code, req_id, error_string)
                if error_code not in (2104, 2106, 2158, 10167):
                    self._b._tick_queue.put_nowait(
                        ("req_error", req_id, error_code, error_string))
                return
            if error_code in _INFO:
                return
            log.error("[IBKR] [%d] req=%d: %s", error_code, req_id, error_string)
            # 326: client ID 중복 → connect() 재시도 신호
            if error_code == 326:
                self._b._connect_error_code = 326
                self._b._connect_event.set()

        # ── 시세 콜백 (ibapi 스레드 → tick_queue만 사용) ──────
        # PyQt6에서 ibapi C 스레드가 직접 bus.publish → Qt 접근하면
        # malloc 힙 손상 발생 → 큐에만 넣고 QTimer가 메인 스레드에서 소비
        def tickPrice(self, req_id: int, tick_type: int,
                      price: float, attrib: Any) -> None:
            self._b._tick_queue.put_nowait(
                ("tick_price", req_id, tick_type, price))

        def tickSize(self, req_id: int, tick_type: int, size: int) -> None:
            self._b._tick_queue.put_nowait(
                ("tick_size", req_id, tick_type, size))

        def tickString(self, req_id: int, tick_type: int, value: str) -> None:
            # 47 = Fundamental Ratios (generic tick 258) — 스캐너 보강용만 처리
            if tick_type == 47:
                self._b._tick_queue.put_nowait(
                    ("tick_string", req_id, tick_type, value))

        # ── 계약 상세 (기업 정보 창) ─────────────────────────
        def contractDetails(self, req_id: int, contract_details: Any) -> None:
            self._b._tick_queue.put_nowait(
                ("contract_details", req_id, contract_details))

        def contractDetailsEnd(self, req_id: int) -> None:
            self._b._tick_queue.put_nowait(("contract_details_end", req_id))

        # ── 주문/체결 콜백 ────────────────────────────────────
        def orderStatus(self, order_id: int, status: str, filled: float,
                        remaining: float, avg_fill_price: float,
                        perm_id: int, parent_id: int, last_fill_price: float,
                        client_id: int, why_held: str, mkt_cap_price: float) -> None:
            self._b._tick_queue.put_nowait(
                ("order_status", order_id, status, filled, avg_fill_price))

        def execDetails(self, req_id: int, contract: Any, execution: Any) -> None:
            self._b._tick_queue.put_nowait(
                ("exec_details", contract, execution))

        # ── 포지션/잔고 콜백 ─────────────────────────────────
        def position(self, account: str, contract: Any,
                     position: float, avg_cost: float) -> None:
            if contract.secType != "STK":
                return
            self._b._tick_queue.put_nowait(
                ("position", contract.symbol, int(position), round(avg_cost, 4)))

        def positionEnd(self) -> None:
            pass

        def accountSummary(self, req_id: int, account: str,
                           tag: str, value: str, currency: str) -> None:
            self._b._tick_queue.put_nowait(
                ("account_summary", tag, value, account))

        def accountSummaryEnd(self, req_id: int) -> None:
            self._b._tick_queue.put_nowait(("account_summary_end", req_id))

        # ── reqAccountUpdates 실시간 콜백 ─────────────────────
        def updateAccountValue(self, key: str, val: str,
                               currency: str, account_name: str) -> None:
            self._b._tick_queue.put_nowait(
                ("account_value", key, val, account_name))

        def updatePortfolio(self, contract: Any, position: float,
                            market_price: float, market_value: float,
                            average_cost: float, unrealized_pnl: float,
                            realized_pnl: float, account_name: str) -> None:
            if contract.secType != "STK":
                return
            self._b._tick_queue.put_nowait(
                ("portfolio_update", contract.symbol, position,
                 market_price, unrealized_pnl, realized_pnl))

        def accountDownloadEnd(self, account_name: str) -> None:
            self._b._tick_queue.put_nowait(("account_download_end",))

        # ── 분봉 히스토리 콜백 ────────────────────────────────
        def historicalData(self, req_id: int, bar: Any) -> None:
            self._b._tick_queue.put_nowait(("historical_bar", req_id, bar))

        def historicalDataEnd(self, req_id: int,
                              start: str, end: str) -> None:
            self._b._tick_queue.put_nowait(("historical_end", req_id))

        # ── 스캐너 콜백 ───────────────────────────────────────
        def scannerData(self, req_id: int, rank: int,
                        contract_details: Any, distance: str,
                        benchmark: str, projection: str,
                        legs_str: str) -> None:
            self._b._tick_queue.put_nowait(
                ("scanner_row", req_id, rank, contract_details,
                 distance, benchmark, projection))

        def scannerDataEnd(self, req_id: int) -> None:
            self._b._tick_queue.put_nowait(("scanner_end", req_id))


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

        # ibapi 스레드 → Qt 메인 스레드 안전 전달용 큐
        # ibapi 콜백에서 직접 bus.publish 하면 힙 손상 발생 (PyQt6 C 스레드 제약)
        # → 이 큐에만 넣고, Qt 메인 스레드 QTimer가 드레인
        self._tick_queue: SyncQueue = SyncQueue()

        # 주문 추적: order_id → internal_id
        self._order_map: Dict[int, str] = {}

        # 분봉 요청 콜백
        self._hist_callbacks: Dict[int, list] = {}

        # 스캐너 요청 임시 버퍼: req_id → {scan_code, rows[]}
        self._scanner_buf: Dict[int, dict] = {}

        # 스캐너 결과 보강(시세/지표) 작업: scan req_id → job dict
        self._enrich_jobs: Dict[int, dict] = {}
        # 보강용 reqMktData req_id → (scan req_id, row index)
        self._enrich_req: Dict[int, tuple] = {}

        # 계약 상세 요청: req_id → [callback, details[]]
        self._cd_callbacks: Dict[int, list] = {}

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

        # 포지션 구독
        self._ib.reqPositions()
        # 잔고 실시간 구독 (reqAccountUpdates = push 방식, 변경 시마다 수신)
        self._ib.reqAccountUpdates(True, "")
        # snapshot 조회도 병행 (AccountType 등 일부 태그는 reqAccountSummary에만 있음)
        self._acct_summary_req_id = self._next_req_id
        self._ib.reqAccountSummary(
            self._next_req_id, "All",
            "NetLiquidation,TotalCashValue,BuyingPower,AvailableFunds,"
            "UnrealizedPnL,RealizedPnL,MaintMarginReq,InitMarginReq,"
            "ExcessLiquidity,AccountType")
        self._next_req_id += 1
        self._acct_buf: dict = {}
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
            fractional = getattr(req, "fractional", False)
        else:
            symbol     = req["symbol"]
            side       = req["side"]
            qty        = req.get("quantity") or req.get("qty")
            price      = req.get("price", 0.0) or 0.0
            order_type = req.get("order_type", "LMT")
            fractional = req.get("fractional", False)

        if not self._connected or not self._ib:
            raise RuntimeError("IBKR 미연결")

        internal_id = str(uuid.uuid4())[:8]
        order_id    = self._next_order_id
        self._next_order_id += 1
        self._order_map[order_id] = internal_id

        c = _make_stk_contract(symbol)
        o = _make_ibkr_order(side, qty, order_type, price, fractional)

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

    # ── Qt 메인 스레드에서 tick_queue 소비 ─────────────────
    def drain_tick_queue(self, max_items: int = 200) -> None:
        """
        QTimer(50ms)로 Qt 메인 스레드에서 호출.
        ibapi 스레드가 넣은 이벤트를 메인 스레드에서 안전하게 처리.
        """
        count = 0
        while count < max_items:
            try:
                item = self._tick_queue.get_nowait()
            except Exception:
                break
            count += 1
            kind = item[0]
            try:
                if kind == "tick_price":
                    _, req_id, tick_type, price = item
                    if req_id in self._enrich_req:
                        self._on_enrich_price(req_id, tick_type, price)
                    else:
                        self._on_tick_price(req_id, tick_type, price)
                elif kind == "tick_size":
                    _, req_id, tick_type, size = item
                    if req_id in self._enrich_req:
                        self._on_enrich_size(req_id, tick_type, size)
                    else:
                        self._on_tick_size(req_id, tick_type, size)
                elif kind == "tick_string":
                    _, req_id, tick_type, value = item
                    if req_id in self._enrich_req:
                        self._on_enrich_string(req_id, tick_type, value)
                elif kind == "contract_details":
                    _, req_id, cd = item
                    self._on_contract_details(req_id, cd)
                elif kind == "contract_details_end":
                    _, req_id = item
                    self._on_contract_details_end(req_id)
                elif kind == "req_error":
                    _, req_id, code, msg = item
                    self._on_aux_req_error(req_id, code, msg)
                elif kind == "order_status":
                    _, order_id, status, filled, avg_price = item
                    self._on_order_status(order_id, status, filled, avg_price)
                elif kind == "exec_details":
                    _, contract, execution = item
                    self._on_exec(contract, execution)
                elif kind == "position":
                    _, symbol, qty, avg_cost = item
                    self._bus.publish(
                        Topics.portfolio_position(symbol),
                        {"symbol": symbol, "qty": qty, "avg_cost": avg_cost})
                elif kind == "account_summary":
                    _, tag, value = item[0], item[1], item[2]
                    account_id = item[3] if len(item) > 3 else ""
                    self._on_account_summary(tag, value, account_id)
                elif kind == "account_summary_end":
                    self._on_account_summary_end()
                elif kind == "account_value":
                    # reqAccountUpdates → updateAccountValue 실시간 수신
                    _, key, val, acct = item
                    self._on_account_value(key, val, acct)
                elif kind == "portfolio_update":
                    # reqAccountUpdates → updatePortfolio 실시간 수신
                    _, sym, pos, mkt_price, unreal_pnl, real_pnl = item
                    self._on_portfolio_update(sym, pos, mkt_price,
                                              unreal_pnl, real_pnl)
                elif kind == "account_download_end":
                    # 초기 스냅샷 완료 → ACCOUNT_UPDATE publish
                    self._on_account_summary_end()
                elif kind == "historical_bar":
                    _, req_id, bar = item
                    self._on_historical_bar(req_id, bar)
                elif kind == "historical_end":
                    _, req_id = item
                    self._on_historical_end(req_id)
                elif kind == "scanner_row":
                    # ("scanner_row", req_id, rank, cd, distance, benchmark, projection)
                    _, req_id, rank, contract_details = item[:4]
                    distance   = item[4] if len(item) > 4 else ""
                    benchmark  = item[5] if len(item) > 5 else ""
                    projection = item[6] if len(item) > 6 else ""
                    self._on_scanner_row(req_id, rank, contract_details,
                                         distance, benchmark, projection)
                elif kind == "scanner_end":
                    _, req_id = item
                    self._on_scanner_end(req_id)
            except Exception:
                log.exception("drain_tick_queue 처리 오류: %s", item)

        # 스캐너 보강 작업 진행 (웨이브 마감 / 다음 웨이브 / 결과 발행)
        if self._enrich_jobs:
            try:
                self._check_enrich_jobs()
            except Exception:
                log.exception("스캐너 보강 작업 처리 오류")

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
        # 체결 후 잔고 즉시 재조회 (reqAccountUpdates는 push지만 딜레이 있을 수 있음)
        if self._ib:
            self._ib.reqAccountUpdates(True, "")

    # tag → ACCOUNT_UPDATE dict 키 매핑
    _ACCT_TAG_MAP = {
        "NetLiquidation":  "net_liquidation",
        "TotalCashValue":  "cash_balance",
        "BuyingPower":     "buying_power",
        "AvailableFunds":  "available_funds",
        "UnrealizedPnL":   "unrealized_pnl",
        "RealizedPnL":     "realized_pnl",
        "MaintMarginReq":  "maintenance_margin",
        "InitMarginReq":   "initial_margin",
        "ExcessLiquidity": "excess_liquidity",
        "AccountType":     "account_type",    # "CASH" / "MARGIN"
    }

    def _on_account_summary(self, tag: str, value: str,
                            account_id: str = "") -> None:
        # 낱개 토픽 (하위 호환)
        try:
            self._bus.publish(Topics.ACCOUNT_BALANCE, {
                "tag": tag, "value": float(value)})
        except (ValueError, TypeError):
            pass
        # 버퍼에 누적
        key = self._ACCT_TAG_MAP.get(tag)
        if key:
            if key == "account_type":
                self._acct_buf[key] = value          # 문자열 그대로
            else:
                try:
                    self._acct_buf[key] = float(value)
                except (ValueError, TypeError):
                    pass
        if account_id:
            self._acct_buf["account_id"] = account_id

    def _on_account_summary_end(self) -> None:
        """reqAccountSummary 응답 완료 → ACCOUNT_UPDATE publish"""
        if not self._acct_buf:
            return
        account_type = self._acct_buf.get("account_type", "")
        data = dict(self._acct_buf)
        data["currency"] = "USD"
        # 현금계좌면 마진 항목을 0으로 명시 (IBKR이 0 반환해도 명확히)
        if account_type.upper() == "CASH":
            data.setdefault("maintenance_margin", 0.0)
            data.setdefault("initial_margin", 0.0)
            data.setdefault("excess_liquidity", data.get("available_funds", 0.0))
        self._bus.publish(Topics.ACCOUNT_UPDATE, data)
        log.debug("ACCOUNT_UPDATE published: %s", {k: v for k, v in data.items()
                                                    if k != "account_type"})

    # reqAccountUpdates 태그 매핑 (updateAccountValue)
    _ACCT_VALUE_MAP = {
        "NetLiquidation":        "net_liquidation",
        "TotalCashBalance":      "cash_balance",
        "BuyingPower":           "buying_power",
        "AvailableFunds":        "available_funds",
        "UnrealizedPnL":         "unrealized_pnl",
        "RealizedPnL":           "realized_pnl",
        "MaintMarginReq":        "maintenance_margin",
        "InitMarginReq":         "initial_margin",
        "ExcessLiquidity":       "excess_liquidity",
        "AccountType":           "account_type",
        "GrossPositionValue":    "gross_position_value",
    }

    def _on_account_value(self, key: str, val: str, acct: str) -> None:
        """reqAccountUpdates → updateAccountValue 실시간 처리"""
        mapped = self._ACCT_VALUE_MAP.get(key)
        if not mapped:
            return
        if acct:
            self._acct_buf["account_id"] = acct
        if mapped == "account_type":
            self._acct_buf[mapped] = val
        else:
            try:
                self._acct_buf[mapped] = float(val)
            except (ValueError, TypeError):
                pass
        # 핵심 값 수신 시마다 즉시 publish (실시간 반영)
        if mapped in ("net_liquidation", "cash_balance", "buying_power",
                      "unrealized_pnl"):
            self._on_account_summary_end()

    def _on_portfolio_update(self, symbol: str, position: float,
                             market_price: float,
                             unrealized_pnl: float, realized_pnl: float) -> None:
        """reqAccountUpdates → updatePortfolio 포지션 시가 실시간 처리"""
        self._bus.publish(
            Topics.portfolio_position(symbol),
            {"symbol": symbol, "qty": int(position),
             "market_price": market_price,
             "unrealized_pnl": unrealized_pnl,
             "realized_pnl": realized_pnl})

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

    # ── 스캐너 ──────────────────────────────────────────────
    def request_scanner(self, req_id: int, scan_code: str,
                        filters: dict | None = None) -> None:
        """
        TWS 마켓 스캐너 조회.
        결과: Topics.SCANNER_RESULT publish
        filters 키: min_price, max_price, above_volume, market(STK/ETF)
        """
        if not self._connected or not self._ib:
            self._bus.publish(Topics.SCANNER_ERROR,
                              {"req_id": req_id, "msg": "브로커 미연결"})
            return

        try:
            from ibapi.scanner import ScannerSubscription   # type: ignore
        except ImportError:
            self._bus.publish(Topics.SCANNER_ERROR,
                              {"req_id": req_id, "msg": "ibapi 미설치"})
            return

        flt = filters or {}
        sub              = ScannerSubscription()
        sub.instrument   = flt.get("market", "STK")
        sub.locationCode = flt.get("location", "STK.US.MAJOR")
        sub.scanCode     = scan_code
        sub.numberOfRows = int(flt.get("num_rows", 50))

        # 가격 필터 — 0 이면 IBKR이 무제한으로 처리
        min_p = flt.get("min_price", 0.0)
        max_p = flt.get("max_price", 0.0)
        if min_p and min_p > 0:
            sub.abovePrice = float(min_p)
        if max_p and max_p > 0:
            sub.belowPrice = float(max_p)

        # 거래량 필터 — MOST_ACTIVE 계열에는 생략 (이미 거래량순 정렬)
        vol = int(flt.get("above_volume", 0))
        _no_vol_codes = {"MOST_ACTIVE", "MOST_ACTIVE_USD",
                         "MOST_ACTIVE_AVG_USD", "HOT_BY_VOLUME"}
        if vol > 0 and scan_code not in _no_vol_codes:
            sub.aboveVolume = vol

        # 시총 필터
        mktcap_a = flt.get("mktcap_above", 0.0)
        mktcap_b = flt.get("mktcap_below", 0.0)
        if mktcap_a:
            sub.marketCapAbove = float(mktcap_a)
        if mktcap_b:
            sub.marketCapBelow = float(mktcap_b)

        self._scanner_buf[req_id] = {"scan_code": scan_code, "rows": [],
                                     "contracts": [], "fallback": []}
        self._ib.reqScannerSubscription(req_id, sub, [], [])
        log.info("Scanner request: req_id=%d scan_code=%s", req_id, scan_code)

    def cancel_scanner(self, req_id: int) -> None:
        if self._ib:
            self._ib.cancelScannerSubscription(req_id)
        self._scanner_buf.pop(req_id, None)

    def _on_scanner_row(self, req_id: int, rank: int,
                        contract_details: Any,
                        distance: str = "", benchmark: str = "",
                        projection: str = "", legs_str: str = "") -> None:
        """
        스캐너 1행 수신.
        IBKR scannerData 는 종목/순위만 주고 가격·등락률·거래량은 주지 않는다.
        (distance/benchmark/projection 은 대부분 빈 문자열)
        → 여기서는 종목만 모으고, scanner_end 후 reqMktData 로 시세를 보강한다.
        """
        buf = self._scanner_buf.get(req_id)
        if buf is None:
            return
        c = contract_details.contract
        # 스캐너 구독은 scannerDataEnd 후에도 갱신 행을 다시 보내므로 중복 제거
        if any(r["symbol"] == c.symbol for r in buf["rows"]):
            return

        def _f(s: str) -> float:
            try:
                return float(s) if s else 0.0
            except (ValueError, TypeError):
                return 0.0

        buf["rows"].append({
            "rank":       rank + 1,
            "symbol":     c.symbol,
            "con_id":     getattr(c, "conId", 0) or 0,
            "long_name":  getattr(contract_details, "longName", "") or "",
            "exchange":   (getattr(c, "primaryExchange", "")
                           or getattr(c, "primaryExch", "") or c.exchange),
            "currency":   c.currency,
            "price":      0.0,
            "prev_close": 0.0,
            "change":     0.0,
            "change_pct": 0.0,
            "volume":     0,
            "avg_volume": 0,
            "market_cap": 0,
            "fundamentals": {},
        })
        buf["contracts"].append(c)
        # 일부 스캔 코드는 distance 에 값이 들어옴 — 시세 보강 실패 시 대체값
        buf["fallback"].append((_f(distance), _f(benchmark)))

    def _on_scanner_end(self, req_id: int) -> None:
        buf = self._scanner_buf.pop(req_id, None)
        # 스냅샷만 필요 — 구독을 해제하지 않으면 TWS 스캐너 구독이 계속 누적됨
        if self._ib:
            try:
                self._ib.cancelScannerSubscription(req_id)
            except Exception:
                pass
        if not buf:
            return
        rows = buf["rows"]
        log.info("Scanner rows: req_id=%d rows=%d — 시세 보강 시작",
                 req_id, len(rows))
        if not rows or not self._connected or not self._ib:
            self._publish_scanner_result(req_id, buf["scan_code"], rows)
            return

        self._enrich_jobs[req_id] = {
            "scan_code": buf["scan_code"],
            "rows":      rows,
            "contracts": buf["contracts"],
            "fallback":  buf["fallback"],
            "raw":       [dict() for _ in rows],
            "pending":   list(range(len(rows))),
            "active":    {},          # mkt req_id → row index
            "wave_end":  0.0,
        }
        self._start_enrich_wave(req_id)

    def _publish_scanner_result(self, req_id: int, scan_code: str,
                                rows: list) -> None:
        self._bus.publish(Topics.SCANNER_RESULT, {
            "req_id":    req_id,
            "scan_code": scan_code,
            "results":   rows,
            "ts":        time.time(),
        })
        log.info("Scanner result: req_id=%d rows=%d", req_id, len(rows))

    # ── 스캐너 결과 시세 보강 ───────────────────────────────
    # 시세 라인 한도(기본 100) 보호 — 한 번에 최대 _ENRICH_BATCH 종목씩 요청
    _ENRICH_BATCH   = 25
    _ENRICH_WAVE_S  = 3.0     # 웨이브당 최대 대기 시간(초)
    # tickSize 의 거래량 단위. TWS API 10.x 이상은 주(share) 단위.
    # 구버전(9.x)에서 100주 단위로 들어오면 100 으로 변경.
    _VOLUME_MULT    = 1

    # tick type → raw 키 (실시간 / 지연 시세 모두)
    _ENRICH_PRICE_TICKS = {
        4: "last", 68: "last",
        9: "close", 75: "close",
        14: "open", 76: "open",
        6: "high", 72: "high",
        7: "low", 73: "low",
        1: "bid", 66: "bid",
        2: "ask", 67: "ask",
    }
    _ENRICH_SIZE_TICKS = {8: "volume", 74: "volume", 21: "avg_volume"}

    # Fundamental Ratios(tick 47) 키 → 결과 필드
    _RATIO_KEYS = {
        "MKTCAP":     "market_cap_m",   # 백만 USD
        "PEEXCLXOR":  "pe",
        "TTMEPSXCLX": "eps",
        "PRICE2BK":   "pbr",
        "YIELD":      "div_yield",
        "TTMDIVSHR":  "div_per_share",
        "BETA":       "beta",
        "TTMROEPCT":  "roe",
        "NHIG":       "high_52w",
        "NLOW":       "low_52w",
        "TTMREV":     "revenue_ttm_m",  # 백만 USD
        "TTMNPMGN":   "net_margin",
        "QTOTD2EQ":   "debt_to_equity",
    }

    def _start_enrich_wave(self, scan_id: int) -> None:
        job = self._enrich_jobs.get(scan_id)
        if not job:
            return
        batch = job["pending"][:self._ENRICH_BATCH]
        job["pending"] = job["pending"][self._ENRICH_BATCH:]
        for idx in batch:
            c = job["contracts"][idx]
            if not c.exchange:
                c.exchange = "SMART"
            with self._lock:
                rid = self._next_req_id
                self._next_req_id += 1
            self._enrich_req[rid] = (scan_id, idx)
            job["active"][rid] = idx
            try:
                # 165 = 평균거래량(tick 21), 258 = 펀더멘털 비율(tick 47)
                self._ib.reqMktData(rid, c, "165,258", False, False, [])
            except Exception:
                log.exception("보강 시세 요청 실패: %s", c.symbol)
                self._enrich_req.pop(rid, None)
                job["active"].pop(rid, None)
        job["wave_end"] = time.time() + self._ENRICH_WAVE_S

    def _enrich_raw(self, rid: int) -> Optional[dict]:
        ref = self._enrich_req.get(rid)
        if not ref:
            return None
        job = self._enrich_jobs.get(ref[0])
        return job["raw"][ref[1]] if job else None

    def _on_enrich_price(self, rid: int, tick_type: int, price: float) -> None:
        key = self._ENRICH_PRICE_TICKS.get(tick_type)
        raw = self._enrich_raw(rid)
        if key and raw is not None and price and price > 0:
            raw[key] = float(price)

    def _on_enrich_size(self, rid: int, tick_type: int, size: Any) -> None:
        key = self._ENRICH_SIZE_TICKS.get(tick_type)
        raw = self._enrich_raw(rid)
        if key and raw is not None:
            try:
                v = float(size)
            except (ValueError, TypeError):
                return
            if v > 0:
                raw[key] = v * self._VOLUME_MULT

    def _on_enrich_string(self, rid: int, tick_type: int, value: str) -> None:
        raw = self._enrich_raw(rid)
        if raw is None or tick_type != 47 or not value:
            return
        ratios = {}
        for part in value.split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            field = self._RATIO_KEYS.get(k.strip())
            if not field:
                continue
            try:
                ratios[field] = float(v)
            except (ValueError, TypeError):
                pass
        raw["ratios"] = ratios

    @staticmethod
    def _enrich_complete(raw: dict) -> bool:
        if raw.get("failed"):
            return True
        return (bool(raw.get("last") or raw.get("close"))
                and "close" in raw and "volume" in raw
                and "avg_volume" in raw and "ratios" in raw)

    def _check_enrich_jobs(self) -> None:
        now = time.time()
        for scan_id in list(self._enrich_jobs):
            job = self._enrich_jobs[scan_id]
            active = job["active"]
            if active:
                done = all(self._enrich_complete(job["raw"][i])
                           for i in active.values())
                if not done and now < job["wave_end"]:
                    continue
                # 웨이브 종료 → 시세 구독 해제
                for rid in list(active):
                    try:
                        self._ib.cancelMktData(rid)
                    except Exception:
                        pass
                    self._enrich_req.pop(rid, None)
                active.clear()
            if job["pending"] and self._connected and self._ib:
                self._start_enrich_wave(scan_id)
                continue
            # 전체 완료 → 행 계산 후 결과 발행
            self._finalize_enrich(job)
            del self._enrich_jobs[scan_id]
            self._publish_scanner_result(scan_id, job["scan_code"], job["rows"])

    def _finalize_enrich(self, job: dict) -> None:
        got = 0
        for idx, row in enumerate(job["rows"]):
            raw = job["raw"][idx]
            last  = raw.get("last", 0.0)
            close = raw.get("close", 0.0)      # tick 9 = 전일 종가
            price = last or close
            if price:
                got += 1
                row["price"]      = round(price, 4)
                row["prev_close"] = close
                if close:
                    row["change"]     = round(price - close, 4)
                    row["change_pct"] = round((price - close) / close * 100, 2)
            else:
                # 시세 미수신(시세 권한 없음 등) → 스캐너 제공값으로 대체
                fb_price, fb_pct = job["fallback"][idx]
                if fb_price:
                    row["price"]      = fb_price
                    row["change_pct"] = fb_pct
                    row["change"]     = round(fb_price * fb_pct / 100, 2)
            for k in ("open", "high", "low"):
                if raw.get(k):
                    row[k] = raw[k]
            row["volume"]     = int(raw.get("volume", 0))
            row["avg_volume"] = int(raw.get("avg_volume", 0))
            ratios = dict(raw.get("ratios", {}))
            mcap_m = ratios.pop("market_cap_m", 0.0)
            if mcap_m:
                row["market_cap"] = mcap_m * 1e6
            row["fundamentals"] = ratios
        log.info("Scanner 보강 완료: 시세 수신 %d / %d", got, len(job["rows"]))

    # ── 계약 상세 (기업 정보) ───────────────────────────────
    def request_contract_details(self, symbol: str, callback,
                                 con_id: int = 0) -> bool:
        """
        종목 계약 상세 조회 (회사명, 업종 분류, 거래소 등).
        callback(details: dict | None, error: str) 가 Qt 메인 스레드에서 호출된다.
        """
        if not self._connected or not self._ib:
            return False
        c = _make_stk_contract(symbol)
        if con_id:
            c.conId = int(con_id)
        with self._lock:
            rid = self._next_req_id
            self._next_req_id += 1
        self._cd_callbacks[rid] = [callback, []]
        self._ib.reqContractDetails(rid, c)
        return True

    def _on_contract_details(self, rid: int, cd: Any) -> None:
        entry = self._cd_callbacks.get(rid)
        if entry is not None:
            entry[1].append(cd)

    def _on_contract_details_end(self, rid: int) -> None:
        entry = self._cd_callbacks.pop(rid, None)
        if not entry:
            return
        callback, details = entry
        if not details:
            self._safe_cb(callback, None, "계약 정보 없음")
            return
        # 여러 개면 미국 주식(SMART/USD) 우선
        cd = next((d for d in details
                   if d.contract.currency == "USD"), details[0])
        c = cd.contract
        info = {
            "symbol":           c.symbol,
            "con_id":           c.conId,
            "long_name":        getattr(cd, "longName", ""),
            "industry":         getattr(cd, "industry", ""),
            "category":         getattr(cd, "category", ""),
            "subcategory":      getattr(cd, "subcategory", ""),
            "stock_type":       getattr(cd, "stockType", ""),
            "primary_exchange": c.primaryExchange,
            "currency":         c.currency,
            "time_zone":        getattr(cd, "timeZoneId", ""),
            "min_tick":         getattr(cd, "minTick", 0.0),
            "liquid_hours":     getattr(cd, "liquidHours", ""),
        }
        self._safe_cb(callback, info, "")

    def _on_aux_req_error(self, rid: int, code: int, msg: str) -> None:
        if rid in self._enrich_req:
            # 시세 권한 없음 / 계약 없음 / 시세 라인 초과 → 해당 종목은 대기 종료
            if code in (101, 200, 354, 10089, 10090, 10091, 10168, 10197):
                raw = self._enrich_raw(rid)
                if raw is not None:
                    raw["failed"] = True
                    raw["error"] = f"[{code}] {msg}"
        elif rid in self._cd_callbacks:
            callback, _ = self._cd_callbacks.pop(rid)
            self._safe_cb(callback, None, f"[{code}] {msg}")

    @staticmethod
    def _safe_cb(callback, *args) -> None:
        try:
            callback(*args)
        except RuntimeError:
            pass   # 콜백 대상 위젯이 이미 닫힘
        except Exception:
            log.exception("contract details callback 오류")
