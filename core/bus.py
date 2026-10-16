# core/bus.py
# DataBus — Core ↔ UI 데이터 흐름 중심
# Core는 PyQt를 절대 import하지 않음

from __future__ import annotations
import threading
from collections import defaultdict
from typing import Callable, Any


class DataBus:
    """
    pub/sub 이벤트 버스.
    - UI 콜백은 _invoke_main()으로 Qt 메인 스레드 보장 (UI 계층 책임)
    - Core 내부에서만 publish 호출
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        # topic → [(subscriber_obj, callback)]
        self._subs: dict[str, list[tuple[Any, Callable]]] = defaultdict(list)

    # ── 구독 ─────────────────────────────────────────────
    def subscribe(self, topic: str, subscriber: Any, callback: Callable) -> None:
        with self._lock:
            self._subs[topic].append((subscriber, callback))

    def unsubscribe(self, topic: str, subscriber: Any) -> None:
        with self._lock:
            self._subs[topic] = [
                (s, cb) for s, cb in self._subs[topic] if s is not subscriber
            ]

    def unsubscribe_all(self, subscriber: Any) -> None:
        """패널 종료 시 반드시 호출"""
        with self._lock:
            for topic in list(self._subs):
                self._subs[topic] = [
                    (s, cb) for s, cb in self._subs[topic] if s is not subscriber
                ]

    # ── 발행 ─────────────────────────────────────────────
    def publish(self, topic: str, data: Any = None) -> None:
        with self._lock:
            handlers = list(self._subs.get(topic, []))
        for _, cb in handlers:
            try:
                cb(data)
            except Exception as e:
                # 개별 핸들러 오류가 전체 버스를 멈추지 않도록
                import logging
                logging.getLogger("DataBus").exception(
                    "Handler error on topic %s: %s", topic, e
                )


# 싱글톤 인스턴스
_bus: DataBus | None = None


def get_bus() -> DataBus:
    global _bus
    if _bus is None:
        _bus = DataBus()
    return _bus
