# data/db_manager.py
# DB 접근 — 별도 스레드 + 큐 방식 (UI 블로킹 없음)
# 날짜별 분리: data_YYYYMMDD.db

from __future__ import annotations
import queue
import sqlite3
import threading
import logging
from datetime import datetime
from pathlib import Path
from config.settings import DATA_DIR

log = logging.getLogger(__name__)


class DBManager:
    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="DBManager", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._q.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def save_trade(self, trade: dict) -> None:
        self._q.put(("trade", trade))

    def save_tick(self, tick: dict) -> None:
        self._q.put(("tick", tick))

    def _loop(self) -> None:
        while self._running:
            try:
                item = self._q.get(timeout=1)
                if item is None:
                    break
                kind, data = item
                self._write(kind, data)
            except queue.Empty:
                continue
            except Exception:
                log.exception("DBManager error")

    def _write(self, kind: str, data: dict) -> None:
        db_path = DATA_DIR / f"data_{datetime.now().strftime('%Y%m%d')}.db"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            if kind == "trade":
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS trades "
                    "(ts TEXT, symbol TEXT, side TEXT, qty INTEGER, price REAL)"
                )
                conn.execute(
                    "INSERT INTO trades VALUES (?,?,?,?,?)",
                    (datetime.now().isoformat(),
                     data["symbol"], data["side"], data["qty"], data["price"])
                )
            elif kind == "tick":
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS ticks "
                    "(ts TEXT, symbol TEXT, price REAL, bid REAL, ask REAL)"
                )
                conn.execute(
                    "INSERT INTO ticks VALUES (?,?,?,?,?)",
                    (datetime.now().isoformat(),
                     data["symbol"], data.get("price"), data.get("bid"), data.get("ask"))
                )
