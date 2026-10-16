# core/order_validator.py
# 주문 발주 전 필수 검증 — 심볼/수량/가격/호가단위

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class OrderRequest:
    symbol: str
    side: str          # "BUY" | "SELL"
    quantity: int
    price: Optional[float]   # None → 시장가
    order_type: str    # "MKT" | "LMT"


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""


class OrderValidator:
    # 미국 주식 최소 호가단위 (1달러 이상)
    MIN_TICK = 0.01

    def validate(self, req: OrderRequest) -> ValidationResult:
        if not req.symbol or not req.symbol.isalpha():
            return ValidationResult(False, f"유효하지 않은 심볼: {req.symbol!r}")

        if req.quantity <= 0:
            return ValidationResult(False, f"수량은 양수여야 합니다: {req.quantity}")

        if req.side not in ("BUY", "SELL"):
            return ValidationResult(False, f"유효하지 않은 방향: {req.side!r}")

        if req.order_type not in ("MKT", "LMT"):
            return ValidationResult(False, f"유효하지 않은 주문 유형: {req.order_type!r}")

        if req.order_type == "LMT":
            if req.price is None or req.price <= 0:
                return ValidationResult(False, "지정가 주문에는 가격이 필요합니다")
            # 호가단위 검증 (소수점 2자리)
            rounded = round(req.price, 2)
            if abs(rounded - req.price) > 1e-9:
                return ValidationResult(
                    False, f"호가단위 오류: {req.price} → {rounded}"
                )

        return ValidationResult(True)
