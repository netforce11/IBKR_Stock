"""
ui/panels/settings_panel.py
──────────────────────────
앱 설정 패널 — 브로커 선택 / IBKR 접속 정보 / 리스크 한도

[적용 방법]
  저장 버튼 → app_config.json 기록 → 재시작 안내 토스트
  브로커 변경(Mock↔IBKR)은 재시작 후 반영됨
"""
from __future__ import annotations
import logging
import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QPushButton, QFormLayout, QFrame,
    QMessageBox, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette, QColor, QFont

from shared.ui.panels.base_panel import BasePanel
from config.settings import CONFIG

log = logging.getLogger(__name__)

# ── 스타일 상수 ─────────────────────────────────────────
_PANEL_QSS = """
QWidget#settings_root {
    background: #F8FAFC;
}
QGroupBox {
    font-size: 12px;
    font-weight: 600;
    color: #1E293B;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 4px;
    background: #FFFFFF;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #0F172A;
}
QLabel {
    color: #334155;
    font-size: 12px;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    border: 1px solid #94A3B8;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 12px;
    background: #FFFFFF;
    color: #0F172A;
    min-height: 24px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #3B82F6;
}
QPushButton#save_btn {
    background: #2563EB;
    color: #FFFFFF;
    border: none;
    border-radius: 5px;
    font-size: 13px;
    font-weight: 600;
    padding: 6px 20px;
    min-height: 32px;
}
QPushButton#save_btn:hover   { background: #1D4ED8; }
QPushButton#save_btn:pressed { background: #1E40AF; }
QPushButton#reset_btn {
    background: #F1F5F9;
    color: #475569;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    font-size: 12px;
    padding: 5px 14px;
    min-height: 32px;
}
QPushButton#reset_btn:hover { background: #E2E8F0; }
"""

_NOTICE_QSS = (
    "background:#FEF9C3; color:#713F12;"
    " border:1px solid #FDE68A; border-radius:4px;"
    " padding:4px 8px; font-size:11px;"
)

_BROKER_INFO = {
    "mock": "가상 브로커 — 실제 연결 없이 랜덤 시세로 동작합니다.",
    "ibkr": "Interactive Brokers TWS/Gateway 연결이 필요합니다.\n"
            "TWS Paper: 7497 · TWS Live: 7496 · Gateway Paper: 4002 · Gateway Live: 4001",
}


class SettingsPanel(BasePanel):
    PANEL_ID    = "settings"
    TITLE       = "⚙ 설정"
    DEFAULT_SIZE = (420, 580)

    def __init__(self, bus=None, parent=None):
        super().__init__(bus, parent)
        self._changed = False
        self._build_ui()
        self._load_values()

    # ════════════════════════════════════════════════════
    # UI 구성
    # ════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        # 라이트 팔레트 강제 (다크 테마 오버라이드)
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window,     QColor("#F8FAFC"))
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#0F172A"))
        pal.setColor(QPalette.ColorRole.Base,       QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.Text,       QColor("#0F172A"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        root = QWidget(self)
        root.setObjectName("settings_root")
        root.setStyleSheet(_PANEL_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        lay = QVBoxLayout(root)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # ── 제목 ─────────────────────────────────────
        title = QLabel("설정")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        title.setStyleSheet("color:#0F172A;")
        lay.addWidget(title)

        # ── 재시작 안내 배너 ─────────────────────────
        self._notice = QLabel("⚠ 브로커 변경은 프로그램 재시작 후 적용됩니다.")
        self._notice.setStyleSheet(_NOTICE_QSS)
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)
        lay.addWidget(self._notice)

        # ── 브로커 섹션 ──────────────────────────────
        lay.addWidget(self._build_broker_group())

        # ── IBKR 접속 정보 섹션 ──────────────────────
        lay.addWidget(self._build_ibkr_group())

        # ── 리스크 한도 섹션 ─────────────────────────
        lay.addWidget(self._build_risk_group())

        lay.addStretch()

        # ── 버튼 행 ──────────────────────────────────
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()

        self._reset_btn = QPushButton("기본값 복원")
        self._reset_btn.setObjectName("reset_btn")
        self._reset_btn.clicked.connect(self._on_reset)

        self._save_btn = QPushButton("💾  저장")
        self._save_btn.setObjectName("save_btn")
        self._save_btn.clicked.connect(self._on_save)

        btn_lay.addWidget(self._reset_btn)
        btn_lay.addWidget(self._save_btn)
        lay.addLayout(btn_lay)

    def _build_broker_group(self) -> QGroupBox:
        grp = QGroupBox("브로커")
        form = QFormLayout(grp)
        form.setSpacing(8)

        self._broker_combo = QComboBox()
        self._broker_combo.addItems(["mock  —  가상 브로커 (개발/테스트)", "ibkr  —  Interactive Brokers"])
        self._broker_combo.currentIndexChanged.connect(self._on_broker_changed)
        form.addRow("브로커 선택:", self._broker_combo)

        self._broker_info = QLabel()
        self._broker_info.setWordWrap(True)
        self._broker_info.setStyleSheet("color:#475569; font-size:11px; padding:2px 0;")
        form.addRow("", self._broker_info)

        return grp

    def _build_ibkr_group(self) -> QGroupBox:
        self._ibkr_grp = QGroupBox("IBKR 접속 정보")
        form = QFormLayout(self._ibkr_grp)
        form.setSpacing(8)

        self._ibkr_host = QLineEdit()
        self._ibkr_host.setPlaceholderText("127.0.0.1")
        form.addRow("Host:", self._ibkr_host)

        self._ibkr_port = QSpinBox()
        self._ibkr_port.setRange(1, 65535)
        self._ibkr_port.setGroupSeparatorShown(False)

        port_lay = QHBoxLayout()
        port_lay.setSpacing(6)
        port_lay.addWidget(self._ibkr_port)
        for label, val in [("TWS Paper", 7497), ("TWS Live", 7496),
                            ("GW Paper", 4002), ("GW Live", 4001)]:
            b = QPushButton(label)
            b.setFixedHeight(24)
            b.setStyleSheet(
                "QPushButton { font-size:10px; padding:0 5px;"
                " border:1px solid #94A3B8; border-radius:3px;"
                " background:#F1F5F9; color:#334155; }"
                "QPushButton:hover { background:#E2E8F0; }"
            )
            b.clicked.connect(lambda _, v=val: self._ibkr_port.setValue(v))
            port_lay.addWidget(b)
        port_lay.addStretch()
        form.addRow("Port:", port_lay)

        self._ibkr_cid = QSpinBox()
        self._ibkr_cid.setRange(1, 999)
        form.addRow("Client ID:", self._ibkr_cid)

        return self._ibkr_grp

    def _build_risk_group(self) -> QGroupBox:
        grp = QGroupBox("리스크 한도")
        form = QFormLayout(grp)
        form.setSpacing(8)

        self._risk_loss = QDoubleSpinBox()
        self._risk_loss.setRange(0, 1_000_000)
        self._risk_loss.setSuffix("  $")
        self._risk_loss.setDecimals(0)
        form.addRow("일일 최대 손실:", self._risk_loss)

        self._risk_pos_size = QSpinBox()
        self._risk_pos_size.setRange(1, 100_000)
        self._risk_pos_size.setSuffix("  주")
        form.addRow("종목당 최대 수량:", self._risk_pos_size)

        self._risk_total = QDoubleSpinBox()
        self._risk_total.setRange(0, 10_000_000)
        self._risk_total.setSuffix("  $")
        self._risk_total.setDecimals(0)
        form.addRow("전체 최대 포지션:", self._risk_total)

        self._risk_trades = QSpinBox()
        self._risk_trades.setRange(1, 10_000)
        self._risk_trades.setSuffix("  회")
        form.addRow("일일 최대 거래 횟수:", self._risk_trades)

        return grp

    # ════════════════════════════════════════════════════
    # 값 로드 / 저장
    # ════════════════════════════════════════════════════
    def _load_values(self) -> None:
        """CONFIG → 위젯"""
        # 브로커
        idx = 0 if CONFIG.broker == "mock" else 1
        self._broker_combo.setCurrentIndex(idx)
        self._update_broker_ui(CONFIG.broker)

        # IBKR
        self._ibkr_host.setText(CONFIG.ibkr.host)
        self._ibkr_port.setValue(CONFIG.ibkr.port)
        self._ibkr_cid.setValue(CONFIG.ibkr.client_id)

        # 리스크
        self._risk_loss.setValue(CONFIG.risk.max_daily_loss)
        self._risk_pos_size.setValue(CONFIG.risk.max_position_size)
        self._risk_total.setValue(CONFIG.risk.max_total_position)
        self._risk_trades.setValue(CONFIG.risk.max_daily_trades)

    def _update_broker_ui(self, broker: str) -> None:
        self._broker_info.setText(_BROKER_INFO.get(broker, ""))
        self._ibkr_grp.setEnabled(broker == "ibkr")

    # ════════════════════════════════════════════════════
    # 이벤트 핸들러
    # ════════════════════════════════════════════════════
    def _on_broker_changed(self, idx: int) -> None:
        broker = "mock" if idx == 0 else "ibkr"
        self._update_broker_ui(broker)
        changed = (broker != CONFIG.broker)
        self._notice.setVisible(changed)

    def _on_save(self) -> None:
        """위젯 → CONFIG → JSON 저장"""
        broker = "mock" if self._broker_combo.currentIndex() == 0 else "ibkr"
        broker_changed = (broker != CONFIG.broker)

        # CONFIG 갱신
        CONFIG.broker = broker
        CONFIG.ibkr.host      = self._ibkr_host.text().strip() or "127.0.0.1"
        CONFIG.ibkr.port      = self._ibkr_port.value()
        CONFIG.ibkr.client_id = self._ibkr_cid.value()
        CONFIG.risk.max_daily_loss      = self._risk_loss.value()
        CONFIG.risk.max_position_size   = self._risk_pos_size.value()
        CONFIG.risk.max_total_position  = self._risk_total.value()
        CONFIG.risk.max_daily_trades    = self._risk_trades.value()

        CONFIG.save()

        if broker_changed:
            QMessageBox.information(
                self, "설정 저장 완료",
                f"브로커가 '{broker}'로 변경되었습니다.\n\n"
                "프로그램을 재시작하면 새 설정이 적용됩니다.",
                QMessageBox.StandardButton.Ok,
            )
        else:
            self._status("✅ 설정 저장 완료")

        self._notice.setVisible(False)
        log.info("설정 저장 완료 — broker=%s", broker)

    def _on_reset(self) -> None:
        ret = QMessageBox.question(
            self, "기본값 복원",
            "모든 설정을 기본값으로 되돌리겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            from config.settings import AppConfig
            defaults = AppConfig()
            CONFIG.broker = defaults.broker
            CONFIG.ibkr   = defaults.ibkr
            CONFIG.risk   = defaults.risk
            self._load_values()
            self._notice.setVisible(False)

    def _status(self, msg: str) -> None:
        if self._bus:
            from shared.core.topics import Topics
            self._bus.publish(Topics.SYSTEM_STATUS, {"msg": msg})
