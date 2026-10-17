# ui/main_window.py
# 메인 윈도우 — QMdiArea 기반 키움 영웅문 스타일
# 패널 자유 배치 + PanelLayoutManager

from __future__ import annotations
import queue
import logging

from PyQt6.QtWidgets import (
    QMainWindow, QMdiArea, QStatusBar, QMenuBar, QMenu, QToolBar
)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt

from config.settings import CONFIG, LAYOUT_FILE
from shared.core.bus import get_bus
from shared.core.topics import Topics
from shared.core.risk_manager import RiskManager
from shared.ui.shortcut_manager import ShortcutManager
from shared.ui.layout_manager import PanelLayoutManager
from core.engine import TradingEngine
from ui.panels.orderbook_panel import OrderbookPanel
from ui.panels.portfolio_panel import PortfolioPanel

log = logging.getLogger(__name__)


def _make_broker(bus):
    if CONFIG.broker == "ibkr":
        from broker.ibkr_broker import IBKRBroker
        return IBKRBroker(bus, CONFIG.ibkr.host, CONFIG.ibkr.port, CONFIG.ibkr.client_id)
    from shared.broker.mock_broker import MockBroker
    return MockBroker(bus)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("IBKR Stock HTS")
        self.resize(1400, 900)

        # 4큐
        self._tick_queue: queue.Queue = queue.Queue()
        self._ui_queue:   queue.Queue = queue.Queue()
        self._cmd_queue:  queue.Queue = queue.Queue()
        self._calc_queue: queue.Queue = queue.Queue()

        self._bus = get_bus()
        self._bus.subscribe(Topics.SYSTEM_STATUS,    self, self._on_status)
        self._bus.subscribe(Topics.SYSTEM_ERROR,     self, self._on_error)
        self._bus.subscribe(Topics.RISK_KILL_SWITCH, self, self._on_kill)
        self._bus.subscribe(Topics.BROKER_CONNECTED, self, self._on_broker_connected)

        # ── QMdiArea (키움 영웅문 스타일 컨테이너) ──────────
        self._mdi = QMdiArea()
        self._mdi.setBackground(Qt.GlobalColor.darkGray)
        self._mdi.setOption(QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation)
        self.setCentralWidget(self._mdi)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._build_menu()
        self._build_toolbar()

        # 4단계 부팅
        self._boot_step1()

    # ── 메뉴바 ────────────────────────────────────────────
    def _build_menu(self) -> None:
        mb = self.menuBar()

        # 보기 메뉴
        view_menu = mb.addMenu("보기(&V)")
        self._act_orderbook  = QAction("호가창",    self, checkable=True, checked=True)
        self._act_portfolio  = QAction("포트폴리오", self, checkable=True, checked=True)
        self._act_chart      = QAction("차트",      self, checkable=True, checked=True)
        self._act_orderbook.triggered.connect(
            lambda v: self._layout_mgr.show_panel("orderbook", v))
        self._act_portfolio.triggered.connect(
            lambda v: self._layout_mgr.show_panel("portfolio", v))
        self._act_chart.triggered.connect(
            lambda v: self._layout_mgr.show_panel("stock_chart", v))
        for act in (self._act_orderbook, self._act_portfolio, self._act_chart):
            view_menu.addAction(act)

        # 레이아웃 메뉴
        layout_menu = mb.addMenu("레이아웃(&L)")
        act_save    = QAction("레이아웃 저장",   self)
        act_restore = QAction("레이아웃 복원",   self)
        act_reset   = QAction("기본 레이아웃",   self)
        act_save.triggered.connect(self._save_layout)
        act_restore.triggered.connect(self._restore_layout)
        act_reset.triggered.connect(self._reset_layout)
        layout_menu.addAction(act_save)
        layout_menu.addAction(act_restore)
        layout_menu.addSeparator()
        layout_menu.addAction(act_reset)

    # ── 툴바 ──────────────────────────────────────────────
    def _build_toolbar(self) -> None:
        tb = QToolBar("메인 툴바")
        tb.setMovable(False)
        self.addToolBar(tb)
        act_tile    = QAction("타일 배열",   self)
        act_cascade = QAction("계단식 배열", self)
        act_tile.triggered.connect(self._mdi.tileSubWindows)
        act_cascade.triggered.connect(self._mdi.cascadeSubWindows)
        tb.addAction(act_tile)
        tb.addAction(act_cascade)

    # ── Step1: Bus 초기화 ─────────────────────────────────
    def _boot_step1(self) -> None:
        log.info("Boot Step1: Bus init")
        self._status_bar.showMessage("Step1: Bus 초기화...")
        self._boot_step2()

    # ── Step2: Broker 연결 ────────────────────────────────
    def _boot_step2(self) -> None:
        log.info("Boot Step2: Broker connect")
        self._status_bar.showMessage("Step2: Broker 연결...")
        self._broker = _make_broker(self._bus)
        try:
            self._broker.connect()
        except Exception as e:
            log.exception("Broker connect failed")
            self._status_bar.showMessage(f"Broker 연결 실패: {e}")
            return
        self._boot_step3()

    # ── Step3: Feed / CalcProcess 시작 ───────────────────
    def _boot_step3(self) -> None:
        log.info("Boot Step3: Feed start")
        self._status_bar.showMessage("Step3: Feed 시작...")
        from shared.calc.calc_process import CalcProcess
        self._calc = CalcProcess(self._bus, self._calc_queue)
        self._calc.start()
        self._bus.subscribe("stock.price.*", self, self._bridge_to_calc)
        self._boot_step4()

    # ── Step4: Engine + 패널 빌드 ────────────────────────
    def _boot_step4(self) -> None:
        log.info("Boot Step4: Engine start")
        self._status_bar.showMessage("Step4: Engine 시작...")
        risk = RiskManager(self._bus, CONFIG.risk)
        self._engine = TradingEngine(self._bus, self._cmd_queue, risk, self._broker)
        self._engine.start()

        self._shortcut_mgr = ShortcutManager(self)

        # PanelLayoutManager 초기화
        self._layout_mgr = PanelLayoutManager(self._mdi, LAYOUT_FILE)

        self._build_panels()
        self._restore_layout()          # 저장된 레이아웃 복원
        self._status_bar.showMessage("준비")
        log.info("Boot complete")

    # ── 패널 생성 ─────────────────────────────────────────
    def _build_panels(self) -> None:
        # 호가창
        self._ob_panel = OrderbookPanel("AAPL", self._bus)
        self._ob_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._ob_panel, title="호가창 — AAPL")

        # 포트폴리오
        self._pf_panel = PortfolioPanel(self._bus)
        self._pf_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._pf_panel, title="포트폴리오")

        # 차트 (pyqtgraph 없으면 skip)
        try:
            from ui.panels.chart_panel import StockChartPanel
            self._chart_panel = StockChartPanel(self._bus)
            self._chart_panel.set_shortcut_manager(self._shortcut_mgr)
            self._layout_mgr.add(self._chart_panel, title="차트")
        except ImportError as e:
            log.warning("차트 패널 로드 실패 (pyqtgraph?): %s", e)

        # 피드 구독 시작
        self._cmd_queue.put({"action": "subscribe_feed", "symbol": "AAPL"})

        # 저장된 레이아웃 없을 때 기본 배치
        self._reset_layout(save=False)

    # ── 레이아웃 관리 ─────────────────────────────────────
    def _save_layout(self) -> None:
        self._layout_mgr.save()
        self._status_bar.showMessage("레이아웃 저장 완료")

    def _restore_layout(self) -> None:
        self._layout_mgr.restore()

    def _reset_layout(self, *, save: bool = True) -> None:
        """키움 영웅문 기본 배치: 호가창 좌측, 포트폴리오 우상, 차트 우하"""
        mdi_w = self._mdi.width()  or 1400
        mdi_h = self._mdi.height() or 860

        left_w  = 320
        right_w = mdi_w - left_w - 8
        top_h   = mdi_h // 2
        bot_h   = mdi_h - top_h - 8

        self._layout_mgr.place("orderbook",   0,      0,     left_w,  mdi_h)
        self._layout_mgr.place("portfolio",   left_w+8, 0,   right_w, top_h)
        self._layout_mgr.place("stock_chart", left_w+8, top_h+8, right_w, bot_h)

        if save:
            self._layout_mgr.save()

    # ── DataBus 콜백 ──────────────────────────────────────
    def _on_status(self, data) -> None:
        self._status_bar.showMessage(str(data))

    def _on_error(self, data) -> None:
        msg = data.get("msg", "") if isinstance(data, dict) else str(data)
        self._status_bar.showMessage(f"오류: {msg}")

    def _on_kill(self, data) -> None:
        reason = data.get("reason", "") if isinstance(data, dict) else ""
        self._status_bar.showMessage(f"⚠️ KILL SWITCH: {reason}")

    def _on_broker_connected(self, data) -> None:
        broker = data.get("broker", "") if isinstance(data, dict) else ""
        self._status_bar.showMessage(f"Broker 연결됨: {broker}")

    def _bridge_to_calc(self, data) -> None:
        self._calc_queue.put(data)

    # ── 앱 종료 ──────────────────────────────────────────
    def closeEvent(self, event) -> None:
        self._save_layout()
        self._engine.stop()
        self._calc.stop()
        self._broker.disconnect()
        self._bus.unsubscribe_all(self)
        super().closeEvent(event)
