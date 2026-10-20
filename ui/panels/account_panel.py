# ui/panels/account_panel.py
# 계좌 정보 패널 — Buying Power / 총 자산 / 잔고 / 마진 사용률 / 손익
#
# 구독 토픽:
#   Topics.ACCOUNT_UPDATE  → 계좌 전체 갱신
#   Topics.RISK_DAILY_PNL  → 일일 손익 별도 갱신
#   Topics.BROKER_CONNECTED / BROKER_DISCONNECTED → 연결 상태

from __future__ import annotations

from PyQt6.QtWidgets import (
    QLabel, QFrame, QHBoxLayout, QVBoxLayout,
    QWidget, QProgressBar, QSizePolicy, QGridLayout,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont, QColor

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel


# ── 색상 상수 ─────────────────────────────────────────────
_GREEN  = "#22C55E"
_RED    = "#EF4444"
_YELLOW = "#F59E0B"
_BLUE   = "#3B82F6"
_GRAY   = "#94A3B8"
_BG     = "#0F172A"   # 패널 배경 (다크)
_CARD   = "#1E293B"   # 카드 배경
_BORDER = "#334155"   # 카드 테두리

# 마진 사용률 임계값
_MARGIN_WARN  = 60.0   # 60% 이상 → 황색
_MARGIN_CRIT  = 80.0   # 80% 이상 → 적색


def _pnl_color(value: float) -> str:
    if value > 0:
        return _GREEN
    if value < 0:
        return _RED
    return _GRAY


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "--"
    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.2f}"


def _fmt_usd_plain(value: float | None) -> str:
    if value is None:
        return "--"
    return f"${value:,.2f}"


# ══════════════════════════════════════════════════════════
# 공통 카드 위젯
# ══════════════════════════════════════════════════════════
class _Card(QFrame):
    """라벨 + 값 + 선택적 부제목을 담는 소형 카드."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background:{_CARD}; border:1px solid {_BORDER};"
            "border-radius:6px; padding:0px; }}"
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        vlay = QVBoxLayout(self)
        vlay.setContentsMargins(10, 6, 10, 6)
        vlay.setSpacing(1)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color:{_GRAY}; font-size:10px; font-weight:600;"
                                "background:transparent; border:none;")
        vlay.addWidget(title_lbl)

        self._val_lbl = QLabel("--")
        self._val_lbl.setStyleSheet(
            "color:#E2E8F0; font-size:15px; font-weight:700;"
            "background:transparent; border:none;")
        vlay.addWidget(self._val_lbl)

        self._sub_lbl = QLabel("")
        self._sub_lbl.setStyleSheet(f"color:{_GRAY}; font-size:10px;"
                                    "background:transparent; border:none;")
        self._sub_lbl.setVisible(False)
        vlay.addWidget(self._sub_lbl)

    def set_value(self, text: str, color: str = "#E2E8F0") -> None:
        self._val_lbl.setText(text)
        self._val_lbl.setStyleSheet(
            f"color:{color}; font-size:15px; font-weight:700;"
            "background:transparent; border:none;")

    def set_sub(self, text: str, color: str = _GRAY) -> None:
        self._sub_lbl.setText(text)
        self._sub_lbl.setStyleSheet(
            f"color:{color}; font-size:10px; background:transparent; border:none;")
        self._sub_lbl.setVisible(bool(text))


# ══════════════════════════════════════════════════════════
# AccountPanel
# ══════════════════════════════════════════════════════════
class AccountPanel(BasePanel):
    PANEL_ID    = "account"
    TITLE       = "💰 계좌"
    DEFAULT_SIZE = (320, 480)

    def __init__(self, bus: DataBus, parent: QWidget | None = None) -> None:
        super().__init__(bus, parent)
        self._build_ui()
        self._subscribe()

    # ── UI 구성 ────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.setStyleSheet(f"background:{_BG};")

        # ── 연결 상태 배지 ─────────────────────────────────
        status_row = QHBoxLayout()
        status_row.setContentsMargins(4, 2, 4, 2)

        self._conn_dot = QLabel("●")
        self._conn_dot.setStyleSheet(f"color:{_GRAY}; font-size:10px;")
        self._conn_lbl = QLabel("연결 대기 중")
        self._conn_lbl.setStyleSheet(
            f"color:{_GRAY}; font-size:10px; font-weight:600;")

        self._acct_lbl = QLabel("")
        self._acct_lbl.setStyleSheet(
            f"color:{_GRAY}; font-size:10px;")
        self._acct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        status_row.addWidget(self._conn_dot)
        status_row.addWidget(self._conn_lbl)
        status_row.addStretch()
        status_row.addWidget(self._acct_lbl)

        status_wrap = QWidget()
        status_wrap.setLayout(status_row)
        status_wrap.setStyleSheet(
            f"background:{_CARD}; border-bottom:1px solid {_BORDER};")
        self._layout.addWidget(status_wrap)

        # ── 2열 카드 그리드 ────────────────────────────────
        grid = QGridLayout()
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(5)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._c_net_liq   = _Card("총 자산 (Net Liq.)")
        self._c_cash      = _Card("현금 잔고")
        self._c_buying    = _Card("매수 가능 금액")
        self._c_avail     = _Card("사용 가능 자금")
        self._c_unreal    = _Card("미실현 손익")
        self._c_daily_pnl = _Card("일일 손익")

        grid.addWidget(self._c_net_liq,   0, 0)
        grid.addWidget(self._c_cash,      0, 1)
        grid.addWidget(self._c_buying,    1, 0)
        grid.addWidget(self._c_avail,     1, 1)
        grid.addWidget(self._c_unreal,    2, 0)
        grid.addWidget(self._c_daily_pnl, 2, 1)

        grid_wrap = QWidget()
        grid_wrap.setLayout(grid)
        grid_wrap.setStyleSheet("background:transparent;")
        self._layout.addWidget(grid_wrap)

        # ── 마진 섹션 ──────────────────────────────────────
        margin_frame = QFrame()
        margin_frame.setStyleSheet(
            f"QFrame {{ background:{_CARD}; border:1px solid {_BORDER};"
            "border-radius:6px; }}")
        margin_vlay = QVBoxLayout(margin_frame)
        margin_vlay.setContentsMargins(10, 8, 10, 8)
        margin_vlay.setSpacing(4)

        # 헤더 행
        hdr = QHBoxLayout()
        m_title = QLabel("마진 사용률")
        m_title.setStyleSheet(
            f"color:{_GRAY}; font-size:10px; font-weight:600;"
            "background:transparent; border:none;")
        self._margin_pct_lbl = QLabel("-- %")
        self._margin_pct_lbl.setStyleSheet(
            "color:#E2E8F0; font-size:12px; font-weight:700;"
            "background:transparent; border:none;")
        self._margin_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        hdr.addWidget(m_title)
        hdr.addStretch()
        hdr.addWidget(self._margin_pct_lbl)
        margin_vlay.addLayout(hdr)

        # 프로그레스 바
        self._margin_bar = QProgressBar()
        self._margin_bar.setRange(0, 100)
        self._margin_bar.setValue(0)
        self._margin_bar.setTextVisible(False)
        self._margin_bar.setFixedHeight(8)
        self._margin_bar.setStyleSheet(self._bar_style(_BLUE))
        margin_vlay.addWidget(self._margin_bar)

        # 마진 세부 행 (유지마진 / 초과유동성)
        detail_row = QHBoxLayout()
        self._maint_lbl = QLabel("유지마진: --")
        self._excess_lbl = QLabel("초과유동성: --")
        for lbl in (self._maint_lbl, self._excess_lbl):
            lbl.setStyleSheet(
                f"color:{_GRAY}; font-size:10px;"
                "background:transparent; border:none;")
        self._excess_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        detail_row.addWidget(self._maint_lbl)
        detail_row.addStretch()
        detail_row.addWidget(self._excess_lbl)
        margin_vlay.addLayout(detail_row)

        self._layout.addWidget(margin_frame)

        # ── 추가 통계 행 ────────────────────────────────────
        stats_frame = QFrame()
        stats_frame.setStyleSheet(
            f"QFrame {{ background:{_CARD}; border:1px solid {_BORDER};"
            "border-radius:6px; }}")
        stats_grid = QGridLayout(stats_frame)
        stats_grid.setContentsMargins(10, 8, 10, 8)
        stats_grid.setSpacing(3)

        def _stat_pair(row: int, label: str):
            lbl = QLabel(label + ":")
            lbl.setStyleSheet(
                f"color:{_GRAY}; font-size:10px;"
                "background:transparent; border:none;")
            val = QLabel("--")
            val.setStyleSheet(
                "color:#E2E8F0; font-size:10px; font-weight:600;"
                "background:transparent; border:none;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            stats_grid.addWidget(lbl, row, 0)
            stats_grid.addWidget(val, row, 1)
            return val

        self._realized_val  = _stat_pair(0, "실현 손익 (당일)")
        self._init_margin_v = _stat_pair(1, "초기 마진")
        self._leverage_val  = _stat_pair(2, "레버리지")
        self._currency_val  = _stat_pair(3, "통화")

        stats_grid.setColumnStretch(1, 1)
        self._layout.addWidget(stats_frame)

        self._layout.addStretch()

    @staticmethod
    def _bar_style(color: str) -> str:
        return (
            "QProgressBar { border:none; border-radius:4px; background:#1E293B; }"
            f"QProgressBar::chunk {{ border-radius:4px; background:{color}; }}"
        )

    # ── 구독 ───────────────────────────────────────────────
    def _subscribe(self) -> None:
        bus = self._bus
        bus.subscribe(Topics.ACCOUNT_UPDATE,      self,
                      lambda d: self._invoke_main("_on_account", d))
        bus.subscribe(Topics.RISK_DAILY_PNL,      self,
                      lambda d: self._invoke_main("_on_daily_pnl", d))
        bus.subscribe(Topics.BROKER_CONNECTED,    self,
                      lambda d: self._invoke_main("_on_connected", d))
        bus.subscribe(Topics.BROKER_DISCONNECTED, self,
                      lambda d: self._invoke_main("_on_disconnected", d))

    # ── 슬롯 ───────────────────────────────────────────────
    @pyqtSlot(object)
    def _on_account(self, data: dict) -> None:
        """Topics.ACCOUNT_UPDATE 수신 — 전체 갱신."""
        net_liq         = data.get("net_liquidation")
        cash            = data.get("cash_balance")
        buying_power    = data.get("buying_power")
        avail           = data.get("available_funds")
        unrealized      = data.get("unrealized_pnl")
        daily_pnl       = data.get("daily_pnl")
        realized        = data.get("realized_pnl")
        maint_margin    = data.get("maintenance_margin")
        init_margin     = data.get("initial_margin")
        excess_liq      = data.get("excess_liquidity")
        currency        = data.get("currency", "USD")
        account_id      = data.get("account_id", "")

        # 총 자산
        self._c_net_liq.set_value(_fmt_usd_plain(net_liq))
        if net_liq is not None and cash is not None:
            pos_val = net_liq - cash
            self._c_net_liq.set_sub(
                f"포지션 평가 {_fmt_usd_plain(pos_val)}",
                _pnl_color(pos_val)
            )

        # 현금 잔고
        self._c_cash.set_value(_fmt_usd_plain(cash))

        # 매수 가능
        self._c_buying.set_value(_fmt_usd_plain(buying_power))
        if buying_power is not None and net_liq and net_liq > 0:
            lev = buying_power / net_liq
            self._c_buying.set_sub(f"레버리지 {lev:.1f}x")

        # 사용 가능
        self._c_avail.set_value(_fmt_usd_plain(avail))

        # 미실현 손익
        self._c_unreal.set_value(
            _fmt_usd(unrealized),
            _pnl_color(unrealized or 0)
        )
        if unrealized is not None and net_liq and net_liq > 0:
            pct = unrealized / net_liq * 100
            self._c_unreal.set_sub(f"{pct:+.2f}%", _pnl_color(unrealized))

        # 일일 손익
        self._c_daily_pnl.set_value(
            _fmt_usd(daily_pnl),
            _pnl_color(daily_pnl or 0)
        )

        # 마진 사용률
        if net_liq and maint_margin is not None and net_liq > 0:
            pct = min(maint_margin / net_liq * 100, 100.0)
            self._margin_bar.setValue(int(pct))
            self._margin_pct_lbl.setText(f"{pct:.1f} %")
            if pct >= _MARGIN_CRIT:
                color = _RED
            elif pct >= _MARGIN_WARN:
                color = _YELLOW
            else:
                color = _BLUE
            self._margin_bar.setStyleSheet(self._bar_style(color))
            self._margin_pct_lbl.setStyleSheet(
                f"color:{color}; font-size:12px; font-weight:700;"
                "background:transparent; border:none;")
        else:
            self._margin_bar.setValue(0)
            self._margin_pct_lbl.setText("-- %")

        self._maint_lbl.setText(
            f"유지마진: {_fmt_usd_plain(maint_margin)}")
        self._excess_lbl.setText(
            f"초과유동성: {_fmt_usd_plain(excess_liq)}")

        # 통계 행
        self._realized_val.setText(
            _fmt_usd(realized) if realized is not None else "--")
        if realized is not None:
            self._realized_val.setStyleSheet(
                f"color:{_pnl_color(realized)}; font-size:10px; font-weight:600;"
                "background:transparent; border:none;")

        self._init_margin_v.setText(_fmt_usd_plain(init_margin))

        if buying_power is not None and net_liq and net_liq > 0:
            lev = buying_power / net_liq
            self._leverage_val.setText(f"{lev:.2f}x")
        else:
            self._leverage_val.setText("--")

        self._currency_val.setText(currency)

        if account_id:
            self._acct_lbl.setText(account_id)

    @pyqtSlot(object)
    def _on_daily_pnl(self, data: dict) -> None:
        """Topics.RISK_DAILY_PNL 수신 — 일일 손익만 별도 갱신."""
        pnl = data.get("daily_pnl", 0.0)
        self._c_daily_pnl.set_value(_fmt_usd(pnl), _pnl_color(pnl))

    @pyqtSlot(object)
    def _on_connected(self, data: dict) -> None:
        broker = data.get("broker", "")
        self._conn_dot.setStyleSheet(f"color:{_GREEN}; font-size:10px;")
        self._conn_lbl.setStyleSheet(
            f"color:{_GREEN}; font-size:10px; font-weight:600;")
        self._conn_lbl.setText(f"연결됨 ({broker})")

    @pyqtSlot(object)
    def _on_disconnected(self, data: dict) -> None:
        self._conn_dot.setStyleSheet(f"color:{_RED}; font-size:10px;")
        self._conn_lbl.setStyleSheet(
            f"color:{_RED}; font-size:10px; font-weight:600;")
        self._conn_lbl.setText("연결 끊김")
        # 값 초기화
        for card in (self._c_net_liq, self._c_cash, self._c_buying,
                     self._c_avail, self._c_unreal, self._c_daily_pnl):
            card.set_value("--")
            card.set_sub("")
        self._margin_bar.setValue(0)
        self._margin_pct_lbl.setText("-- %")
