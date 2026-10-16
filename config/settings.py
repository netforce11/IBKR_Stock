# config/settings.py
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path.home() / "trading_terminal" / "ibkr_stock"
DATA_DIR = BASE_DIR / "data"
LOG_DIR  = BASE_DIR / "logs"
LAYOUT_FILE = BASE_DIR / "grid_layout.json"

@dataclass
class RiskConfig:
    max_daily_loss: float = 500.0        # 일일 최대 손실 ($)
    max_position_size: int = 1000        # 종목당 최대 수량
    max_total_position: float = 50000.0  # 전체 최대 포지션 ($)
    max_daily_trades: int = 100          # 일일 최대 거래 횟수

@dataclass
class IBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497          # TWS paper: 7497, live: 7496
    client_id: int = 1

@dataclass
class AppConfig:
    broker: str = "mock"      # "ibkr" | "mock"
    risk: RiskConfig = field(default_factory=RiskConfig)
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    theme: str = "dark"

CONFIG = AppConfig()
