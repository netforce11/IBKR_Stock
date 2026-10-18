"""
core/watchlist_manager.py
관심종목 그룹 관리 — JSON 저장/복원

데이터 구조:
    {
      "groups": [
        {"name": "기술주",   "symbols": ["AAPL", "NVDA", "MSFT"]},
        {"name": "ETF",      "symbols": ["SPY", "QQQ", "IWM"]},
        {"name": "즐겨찾기", "symbols": ["TSLA", "AMZN"]}
      ],
      "last_symbol": "AAPL"
    }

저장 경로: ~/trading_terminal/IBKR_Stock/data/watchlist.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Dict

log = logging.getLogger(__name__)

# 기본 관심종목 (최초 실행 시)
_DEFAULTS: List[Dict] = [
    {"name": "기술주",   "symbols": ["AAPL", "MSFT", "NVDA", "META", "GOOG"]},
    {"name": "ETF",      "symbols": ["SPY", "QQQ", "IWM", "DIA"]},
    {"name": "즐겨찾기", "symbols": []},
]


class WatchlistManager:
    """관심종목 그룹 저장/복원 + CRUD"""

    def __init__(self, data_dir: Path) -> None:
        self._file = Path(data_dir) / "watchlist.json"
        self._groups: List[Dict] = []   # [{"name": str, "symbols": [str]}]
        self._last_symbol: str = "AAPL"
        self._load()

    # ── 로드 ────────────────────────────────────────────
    def _load(self) -> None:
        if self._file.exists():
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                self._groups       = raw.get("groups", _DEFAULTS)
                self._last_symbol  = raw.get("last_symbol", "AAPL")
                log.debug("관심종목 로드: %s", self._file)
                return
            except Exception:
                log.exception("관심종목 파일 읽기 실패 — 기본값 사용")
        self._groups = [dict(g) for g in _DEFAULTS]
        self.save()

    # ── 저장 ────────────────────────────────────────────
    def save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(
                json.dumps({"groups": self._groups,
                            "last_symbol": self._last_symbol},
                           indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception:
            log.exception("관심종목 저장 실패")

    # ── 그룹 조회 ────────────────────────────────────────
    @property
    def groups(self) -> List[Dict]:
        """[{"name": str, "symbols": [str]}, ...]"""
        return self._groups

    def group_names(self) -> List[str]:
        return [g["name"] for g in self._groups]

    def symbols_of(self, group_name: str) -> List[str]:
        for g in self._groups:
            if g["name"] == group_name:
                return list(g["symbols"])
        return []

    # ── 그룹 CRUD ───────────────────────────────────────
    def add_group(self, name: str) -> bool:
        name = name.strip()
        if not name or any(g["name"] == name for g in self._groups):
            return False
        self._groups.append({"name": name, "symbols": []})
        self.save()
        return True

    def rename_group(self, old: str, new: str) -> bool:
        new = new.strip()
        if not new or any(g["name"] == new for g in self._groups):
            return False
        for g in self._groups:
            if g["name"] == old:
                g["name"] = new
                self.save()
                return True
        return False

    def delete_group(self, name: str) -> bool:
        before = len(self._groups)
        self._groups = [g for g in self._groups if g["name"] != name]
        if len(self._groups) < before:
            self.save()
            return True
        return False

    def move_group(self, name: str, delta: int) -> None:
        """그룹 순서 이동 (delta=+1 아래로, -1 위로)"""
        idx = next((i for i, g in enumerate(self._groups)
                    if g["name"] == name), -1)
        if idx < 0:
            return
        new_idx = max(0, min(len(self._groups) - 1, idx + delta))
        g = self._groups.pop(idx)
        self._groups.insert(new_idx, g)
        self.save()

    # ── 종목 CRUD ───────────────────────────────────────
    def add_symbol(self, group_name: str, symbol: str) -> bool:
        sym = symbol.strip().upper()
        if not sym:
            return False
        for g in self._groups:
            if g["name"] == group_name:
                if sym not in g["symbols"]:
                    g["symbols"].append(sym)
                    self.save()
                return True
        return False

    def remove_symbol(self, group_name: str, symbol: str) -> bool:
        sym = symbol.strip().upper()
        for g in self._groups:
            if g["name"] == group_name and sym in g["symbols"]:
                g["symbols"].remove(sym)
                self.save()
                return True
        return False

    def move_symbol(self, group_name: str, symbol: str, delta: int) -> None:
        sym = symbol.strip().upper()
        for g in self._groups:
            if g["name"] == group_name and sym in g["symbols"]:
                idx = g["symbols"].index(sym)
                new_idx = max(0, min(len(g["symbols"]) - 1, idx + delta))
                g["symbols"].pop(idx)
                g["symbols"].insert(new_idx, sym)
                self.save()
                return

    # ── 마지막 선택 종목 ─────────────────────────────────
    @property
    def last_symbol(self) -> str:
        return self._last_symbol

    def set_last_symbol(self, symbol: str) -> None:
        self._last_symbol = symbol.upper()
        self.save()
