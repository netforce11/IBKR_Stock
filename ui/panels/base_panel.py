# ui/panels/base_panel.py
# 모든 패널의 부모 — 헤더/테마/DataBus 구독 자동 관리

from __future__ import annotations
from PyQt6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QMetaObject, Q_ARG
from core.bus import DataBus
from core.topics import Topics


class BasePanel(QDockWidget):
    """
    - PANEL_ID 로 탭별 레이아웃 저장/복원
    - 닫기 시 bus.unsubscribe_all(self) 자동 호출
    - UI 콜백은 _invoke_main() 으로 Qt 메인 스레드 보장
    """

    PANEL_ID: str = "base"

    def __init__(self, title: str, bus: DataBus, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._bus = bus
        self._setup_ui(title)
        self.visibilityChanged.connect(self._on_visibility_changed)

    def _setup_ui(self, title: str) -> None:
        self.setObjectName(self.PANEL_ID)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self.setWidget(container)

    # ── 스레드 안전 UI 콜백 ────────────────────────────────
    def _invoke_main(self, slot_name: str, data) -> None:
        """Core 스레드에서 호출 → Qt 메인 스레드로 전달"""
        QMetaObject.invokeMethod(
            self, slot_name,
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(object, data)
        )

    # ── 패널 종료 ─────────────────────────────────────────
    def _on_visibility_changed(self, visible: bool) -> None:
        if not visible:
            self._bus.unsubscribe_all(self)

    def closeEvent(self, event) -> None:
        self._bus.unsubscribe_all(self)
        super().closeEvent(event)
