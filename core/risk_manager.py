# core/risk_manager.py
# 리스크 관리 — 일일 손실 한도 / 포지션 크기 / 거래 횟수

from __future__ import annotations
import logging
from config.settings import RiskConfig
from core.bus import DataBus
from core.topics import Topics
from core.order_validator import OrderRequest, ValidationResult

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, bus: DataBus, cfg: RiskConfig) -> None:
        self._bus = bus
        self._cfg = cfg
        self._killed = False
        self._daily_pnl = 0.0
        self._daily_trades = 0
        # symbol → 보유 수량
        self._positions: dict[str, int] = {}

        bus.subscribe(Topics.RISK_KILL_SWITCH, self, self._on_kill_switch)

    # ── 킬스위치 ─────────────────────────────────────────
    def _on_kill_switch(self, _data) -> None:
        self._killed = True
        log.critical("Kill switch activated — 모든 거래 차단")

    def trigger_kill_switch(self, reason: str = "") -> None:
        self._killed = True
        self._bus.publish(Topics.RISK_KILL_SWITCH, {"reason": reason})
        log.critical("Kill switch triggered: %s", reason)

    # ── 주문 승인 ─────────────────────────────────────────
    def check_order(self, req: OrderRequest) -> ValidationResult:
        if self._killed:
            return ValidationResult(False, "Kill switch 활성 — 거래 차단")

        if self._daily_trades >= self._cfg.max_daily_trades:
            return ValidationResult(
                False, f"일일 최대 거래 횟수 초과: {self._cfg.max_daily_trades}"
            )

        if req.quantity > self._cfg.max_position_size:
            return ValidationResult(
                False,
                f"종목당 최대 수량 초과: {req.quantity} > {self._cfg.max_position_size}",
            )

        return ValidationResult(True)

    # ── PnL 갱신 ─────────────────────────────────────────
    def update_pnl(self, pnl_delta: float) -> None:
        self._daily_pnl += pnl_delta
        self._bus.publish(Topics.RISK_DAILY_PNL, {"pnl": self._daily_pnl})

        if self._daily_pnl <= -self._cfg.max_daily_loss:
            self.trigger_kill_switch(
                f"일일 손실 한도 초과: {self._daily_pnl:.2f}"
            )

    def on_fill(self) -> None:
        self._daily_trades += 1

    def reset_daily(self) -> None:
        """영업일 시작 시 호출"""
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._killed = False
