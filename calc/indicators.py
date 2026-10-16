# calc/indicators.py
# 기술 지표 계산 (이동평균, RSI 등) — calc_queue 전용 프로세스에서 실행

from __future__ import annotations
import collections
import math


class SMA:
    """단순 이동평균"""
    def __init__(self, period: int) -> None:
        self.period = period
        self._buf: collections.deque[float] = collections.deque(maxlen=period)

    def update(self, price: float) -> float | None:
        self._buf.append(price)
        if len(self._buf) < self.period:
            return None
        return sum(self._buf) / self.period


class EMA:
    """지수 이동평균"""
    def __init__(self, period: int) -> None:
        self.period = period
        self.k = 2 / (period + 1)
        self._ema: float | None = None
        self._count = 0

    def update(self, price: float) -> float | None:
        self._count += 1
        if self._count < self.period:
            self._ema = price if self._ema is None else (self._ema + price) / 2
            return None
        if self._count == self.period:
            self._ema = price
            return self._ema
        self._ema = price * self.k + self._ema * (1 - self.k)
        return round(self._ema, 4)


class RSI:
    """RSI (Relative Strength Index)"""
    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._gains: collections.deque[float] = collections.deque(maxlen=period)
        self._losses: collections.deque[float] = collections.deque(maxlen=period)
        self._prev: float | None = None

    def update(self, price: float) -> float | None:
        if self._prev is None:
            self._prev = price
            return None
        delta = price - self._prev
        self._prev = price
        self._gains.append(max(delta, 0))
        self._losses.append(max(-delta, 0))
        if len(self._gains) < self.period:
            return None
        avg_gain = sum(self._gains) / self.period
        avg_loss = sum(self._losses) / self.period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)
