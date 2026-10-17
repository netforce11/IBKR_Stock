"""
core/storage/stock_db.py
─────────────────────────
주식 HTS SQLite 저장 매니저 (주문/체결/포지션/일일손익)

저장 흐름:
  order.placed    → order_log INSERT  (status='접수')
  order.filled    → order_log UPDATE  (status='체결') + fills INSERT
  order.cancelled → order_log UPDATE  (status='취소')

DB 파일: ~/trading_terminal/IBKR_Stock/data/stock_YYYYMMDD.db (날짜별)
테이블:
  order_log   — 주문 내역
  fills       — 체결 내역 (P&L 포함)
  positions   — 포지션 스냅샷
  daily_risk  — 일일 리스크 상태 (재시작 후 복원용)

설계 원칙:
  - PyQt import 없음 (Core 레이어)
  - 별도 스레드 + 큐 방식 → UI 블로킹 없음
  - DataBus 구독으로 자동 저장
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
_DB_DIR = Path.home() / "trading_terminal" / "IBKR_Stock" / "data"


def _now_et() -> datetime:
    return datetime.now(ET)


def _today_et() -> date:
    return _now_et().date()


def _db_path(d: Optional[date] = None) -> Path:
    """ET 기준 날짜별 DB 경로"""
    if d is None:
        d = _today_et()
    return _DB_DIR / f"stock_{d.strftime('%Y%m%d')}.db"


# ── DDL ─────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS order_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT    NOT NULL UNIQUE,
    ts          REAL    NOT NULL,
    date_et     TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL,   -- BUY / SELL
    qty         INTEGER NOT NULL,
    price       REAL,               -- NULL = 시장가
    order_type  TEXT    NOT NULL,   -- LMT / MKT
    status      TEXT    DEFAULT '접수',
    raw_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ol_date   ON order_log (date_et);
CREATE INDEX IF NOT EXISTS idx_ol_oid    ON order_log (order_id);
CREATE INDEX IF NOT EXISTS idx_ol_symbol ON order_log (symbol);

CREATE TABLE IF NOT EXISTS fills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id     TEXT    NOT NULL UNIQUE,
    order_id    TEXT    NOT NULL,
    ts          REAL    NOT NULL,
    date_et     TEXT    NOT NULL,
    time_et     TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL,   -- BUY / SELL
    qty         INTEGER NOT NULL,
    fill_price  REAL    NOT NULL,
    commission  REAL    DEFAULT 0.0,
    realized_pnl REAL   DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_fill_date   ON fills (date_et);
CREATE INDEX IF NOT EXISTS idx_fill_symbol ON fills (symbol);
CREATE INDEX IF NOT EXISTS idx_fill_oid    ON fills (order_id);

CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    date_et     TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    qty         INTEGER NOT NULL,
    avg_cost    REAL    NOT NULL,
    market_price REAL   DEFAULT 0.0,
    unrealized_pnl REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_pos_symbol ON positions (symbol, date_et);

CREATE TABLE IF NOT EXISTS daily_risk (
    date_et         TEXT PRIMARY KEY,
    daily_loss      REAL DEFAULT 0.0,
    trade_count     INTEGER DEFAULT 0,
    kill_switch     INTEGER DEFAULT 0,
    last_updated    REAL
);
"""


def _get_conn(d: Optional[date] = None) -> sqlite3.Connection:
    path = _db_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()
    return conn


# ── StockDBManager ───────────────────────────────────────────
class StockDBManager:
    """
    DataBus 구독 → 자동 저장.
    별도 쓰기 스레드로 UI 블로킹 없음.

    사용법:
        db = StockDBManager(bus)
        db.start()
        # ...
        db.stop()

        # 오늘 체결 조회
        fills = db.query_fills()
        # 오늘 주문 조회
        orders = db.query_orders()
        # 일일 리스크 상태 복원
        risk = db.load_daily_risk()
    """

    def __init__(self, bus) -> None:
        self._bus = bus
        self._write_q: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # DataBus 구독
        bus.subscribe("order.placed",    self, self._on_placed)
        bus.subscribe("order.filled",    self, self._on_filled)
        bus.subscribe("order.cancelled", self, self._on_cancelled)
        bus.subscribe("portfolio.position.*", self, self._on_position)
        bus.subscribe("risk.daily_pnl",  self, self._on_daily_pnl)

    # ── 라이프사이클 ─────────────────────────────────────────
    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._writer_loop,
                                        daemon=True, name="StockDB-Writer")
        self._thread.start()
        log.info("StockDBManager started — %s", _db_path())

    def stop(self) -> None:
        self._running = False
        self._write_q.put(None)  # sentinel
        if self._thread:
            self._thread.join(timeout=3)
        log.info("StockDBManager stopped")

    # ── 쓰기 스레드 ──────────────────────────────────────────
    def _writer_loop(self) -> None:
        while self._running:
            try:
                task = self._write_q.get(timeout=1)
                if task is None:
                    break
                fn, args = task
                fn(*args)
            except queue.Empty:
                pass
            except Exception as e:
                log.exception("DB write error: %s", e)

    def _enqueue(self, fn, *args) -> None:
        self._write_q.put((fn, args))

    # ── DataBus 콜백 ─────────────────────────────────────────
    def _on_placed(self, data: dict) -> None:
        self._enqueue(self._write_order, data, "접수")

    def _on_filled(self, data: dict) -> None:
        self._enqueue(self._write_fill, data)

    def _on_cancelled(self, data: dict) -> None:
        oid = str(data.get("order_id", ""))
        self._enqueue(self._update_order_status, oid, "취소")

    def _on_position(self, data: dict) -> None:
        self._enqueue(self._write_position, data)

    def _on_daily_pnl(self, data: dict) -> None:
        self._enqueue(self._write_daily_risk, data)

    # ── 실제 DB 쓰기 (쓰기 스레드에서 실행) ─────────────────
    def _write_order(self, data: dict, status: str) -> None:
        now = _now_et()
        try:
            conn = _get_conn()
            conn.execute("""
                INSERT OR IGNORE INTO order_log
                (order_id, ts, date_et, symbol, side, qty, price, order_type, status, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                str(data.get("order_id", str(uuid.uuid4())[:8])),
                time.time(),
                now.date().isoformat(),
                str(data.get("symbol", "")),
                str(data.get("side", "")),
                int(data.get("qty", 0)),
                data.get("price"),
                str(data.get("order_type", "LMT")),
                status,
                json.dumps(data, ensure_ascii=False),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("_write_order error: %s", e)

    def _write_fill(self, data: dict) -> None:
        now = _now_et()
        oid = str(data.get("order_id", ""))
        try:
            conn = _get_conn()
            # order_log 상태 업데이트
            if oid:
                conn.execute(
                    "UPDATE order_log SET status='체결' WHERE order_id=?", (oid,))
            # fills 삽입
            conn.execute("""
                INSERT OR IGNORE INTO fills
                (fill_id, order_id, ts, date_et, time_et,
                 symbol, side, qty, fill_price, commission, realized_pnl)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(data.get("fill_id", str(uuid.uuid4())[:12])),
                oid,
                time.time(),
                now.date().isoformat(),
                now.strftime("%H:%M:%S"),
                str(data.get("symbol", "")),
                str(data.get("side", "")),
                int(data.get("qty", 0)),
                float(data.get("fill_price", data.get("price", 0.0))),
                float(data.get("commission", 0.0)),
                float(data.get("realized_pnl", 0.0)),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("_write_fill error: %s", e)

    def _update_order_status(self, order_id: str, status: str) -> None:
        if not order_id:
            return
        try:
            conn = _get_conn()
            conn.execute(
                "UPDATE order_log SET status=? WHERE order_id=?",
                (status, order_id))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("_update_order_status error: %s", e)

    def _write_position(self, data: dict) -> None:
        now = _now_et()
        try:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO positions
                (ts, date_et, symbol, qty, avg_cost, market_price, unrealized_pnl)
                VALUES (?,?,?,?,?,?,?)
            """, (
                time.time(),
                now.date().isoformat(),
                str(data.get("symbol", "")),
                int(data.get("qty", 0)),
                float(data.get("avg_cost", 0.0)),
                float(data.get("market_price", 0.0)),
                float(data.get("unrealized_pnl", 0.0)),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("_write_position error: %s", e)

    def _write_daily_risk(self, data: dict) -> None:
        today = _today_et().isoformat()
        try:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO daily_risk (date_et, daily_loss, trade_count, kill_switch, last_updated)
                VALUES (?,?,?,?,?)
                ON CONFLICT(date_et) DO UPDATE SET
                    daily_loss   = excluded.daily_loss,
                    trade_count  = excluded.trade_count,
                    kill_switch  = excluded.kill_switch,
                    last_updated = excluded.last_updated
            """, (
                today,
                float(data.get("daily_loss", 0.0)),
                int(data.get("trade_count", 0)),
                int(data.get("kill_switch", 0)),
                time.time(),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("_write_daily_risk error: %s", e)

    # ── 조회 API (UI 스레드에서 호출 가능) ─────────────────
    def query_fills(self, d: Optional[date] = None) -> List[Dict]:
        """당일(또는 지정일) 체결 목록 (최신순)"""
        if d is None:
            d = _today_et()
        path = _db_path(d)
        if not path.exists():
            return []
        try:
            conn = _get_conn(d)
            rows = conn.execute("""
                SELECT fill_id, time_et, symbol, side, qty,
                       fill_price, commission, realized_pnl
                FROM fills WHERE date_et=? ORDER BY ts DESC
            """, (d.isoformat(),)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error("query_fills error: %s", e)
            return []

    def query_orders(self, d: Optional[date] = None) -> List[Dict]:
        """당일(또는 지정일) 주문 목록 (최신순)"""
        if d is None:
            d = _today_et()
        path = _db_path(d)
        if not path.exists():
            return []
        try:
            conn = _get_conn(d)
            rows = conn.execute("""
                SELECT order_id, date_et, symbol, side, qty,
                       price, order_type, status
                FROM order_log WHERE date_et=? ORDER BY ts DESC
            """, (d.isoformat(),)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error("query_orders error: %s", e)
            return []

    def load_daily_risk(self, d: Optional[date] = None) -> Dict:
        """
        재시작 후 일일 리스크 상태 복원.
        RiskManager.__init__에서 호출.
        반환: {"daily_loss": float, "trade_count": int, "kill_switch": bool}
        """
        if d is None:
            d = _today_et()
        path = _db_path(d)
        if not path.exists():
            return {"daily_loss": 0.0, "trade_count": 0, "kill_switch": False}
        try:
            conn = _get_conn(d)
            row = conn.execute(
                "SELECT daily_loss, trade_count, kill_switch FROM daily_risk WHERE date_et=?",
                (d.isoformat(),)
            ).fetchone()
            conn.close()
            if row:
                return {
                    "daily_loss":  float(row["daily_loss"]),
                    "trade_count": int(row["trade_count"]),
                    "kill_switch": bool(row["kill_switch"]),
                }
        except Exception as e:
            log.error("load_daily_risk error: %s", e)
        return {"daily_loss": 0.0, "trade_count": 0, "kill_switch": False}
