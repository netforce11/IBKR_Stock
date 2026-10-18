# config/settings.py
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR    = Path("/home/netforce/trading_terminal/ibkr_stock")
DATA_DIR    = BASE_DIR / "data"
LOG_DIR     = BASE_DIR / "logs"
LAYOUT_FILE = BASE_DIR / "grid_layout.json"
_CFG_FILE   = BASE_DIR / "app_config.json"   # ← 설정 저장 파일


@dataclass
class RiskConfig:
    max_daily_loss:     float = 500.0     # 일일 최대 손실 ($)
    max_position_size:  int   = 1000      # 종목당 최대 수량
    max_total_position: float = 50000.0   # 전체 최대 포지션 ($)
    max_daily_trades:   int   = 100       # 일일 최대 거래 횟수


@dataclass
class IBKRConfig:
    host:      str = "127.0.0.1"
    port:      int = 7497          # TWS paper: 7497, live: 7496, Gateway paper: 4002
    client_id: int = 1


@dataclass
class AppConfig:
    broker: str        = "mock"    # "ibkr" | "mock"
    risk:   RiskConfig = field(default_factory=RiskConfig)
    ibkr:   IBKRConfig = field(default_factory=IBKRConfig)
    theme:  str        = "dark"

    # ── 파일 저장/불러오기 ──────────────────────────────
    def save(self, path: Path = _CFG_FILE) -> None:
        """현재 설정을 JSON으로 저장"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("설정 저장: %s", path)
        except Exception:
            log.exception("설정 저장 실패")

    @classmethod
    def load(cls, path: Path = _CFG_FILE) -> "AppConfig":
        """JSON에서 설정 불러오기 — 파일 없으면 기본값 반환"""
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            risk = RiskConfig(**raw.get("risk", {}))
            ibkr = IBKRConfig(**raw.get("ibkr", {}))
            return cls(
                broker=raw.get("broker", "mock"),
                theme=raw.get("theme",  "dark"),
                risk=risk,
                ibkr=ibkr,
            )
        except Exception:
            log.exception("설정 불러오기 실패 — 기본값 사용")
            return cls()


# 앱 전역 싱글턴 — 파일이 있으면 로드, 없으면 기본값
CONFIG = AppConfig.load()
