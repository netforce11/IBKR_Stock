# ui/main_window.py
# 메인 윈도우 — 4단계 명시적 부팅 + 탭/도킹 레이아웃

from __future__ import annotations
import queue
import json
import logging
from pathlib import Path

from PyQt6.QtWidgets import QMainWindow, QApplication, QStatusBar, QMenuBar
from PyQt6.QtCore import QTimer

from config.settings import CONFIG, LAYOUT_FILE
from core.bus import get_bus
from core.topics import Topics
from core.risk_manager import RiskManager
from core.engine import TradingEngine
from ui.panels.orderbook_panel import OrderbookPanel
from ui.panels.portfolio_panel import PortfolioPanel

log = logging.getLogger(__name__)


def _make_broker(bus):
    if CONFIG.broker == "ibkr":
        from broker.ibkr_broker import IBKRBroker
        return IBKRBroker(bus, CONFIG.ibkr.host, CONFIG.ibkr.port, CONFIG.ibkr.client_id)
    from broker.mock_broker import MockBroker
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
        self._bus.subscribe(Topics.SYSTEM_STATUS,   self, self._on_status)
        self._bus.subscribe(Topics.SYSTEM_ERROR,    self, self._on_error)
        self._bus.subscribe(Topics.RISK_KILL_SWITCH, self, self._on_kill)
        self._bus.subscribe(Topics.BROKER_CONNECTED, self, self._on_broker_connected)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        # 4단계 부팅 (QTimer 딜레이 방식 금지 → 명시적 단계 호출)
        self._boot_step1()

    # ── Step1: Bus 초기화 ─────────────────────────────────
    def _boot_step1(self) -> None:
        log.info("Boot Step1: Bus init")
        self._status_bar.showMessage("Step1: Bus 초기화...")
        # Bus는 get_bus()로 이미 초기화됨
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

    # ── Step3: Feed 시작 ──────────────────────────────────
    def _boot_step3(self) -> None:
        log.info("Boot Step3: Feed start")
        self._status_bar.showMessage("Step3: Feed 시작...")
        from calc.calc_process import CalcProcess
        self._calc = CalcProcess(self._bus, self._calc_queue)
        self._calc.start()

        # CalcProcess로 price 이벤트 브릿지 (tick_queue → calc_queue)
        self._bus.subscribe("stock.price.*", self, self._bridge_to_calc)
        self._boot_step4()

    # ── Step4: Engine 시작 ────────────────────────────────
    def _boot_step4(self) -> None:
        log.info("Boot Step4: Engine start")
        self._status_bar.showMessage("Step4: Engine 시작...")
        risk = RiskManager(self._bus, CONFIG.risk)
        self._engine = TradingEngine(self._bus, self._cmd_queue, risk, self._broker)
        self._engine.start()
        self._build_panels()
        self._restore_layout()
        self._status_bar.showMessage("준비")
        log.info("Boot complete")

    # ── 패널 생성 ─────────────────────────────────────────
    def _build_panels(self) -> None:
        from PyQt6.QtCore import Qt
        self._ob_panel = OrderbookPanel("AAPL", self._bus, self)
        self._pf_panel = PortfolioPanel(self._bus, self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._ob_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._pf_panel)

        # 피드 구독 시작
        self._cmd_queue.put({"action": "subscribe_feed", "symbol": "AAPL"})

    # ── 레이아웃 저장/복원 ────────────────────────────────
    def _save_layout(self) -> None:
        state = self.saveState().data().hex()
        LAYOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAYOUT_FILE.write_text(json.dumps({"state": state}))

    def _restore_layout(self) -> None:
        if LAYOUT_FILE.exists():
            try:
                data = json.loads(LAYOUT_FILE.read_text())
                from PyQt6.QtCore import QByteArray
                self.restoreState(QByteArray.fromHex(data["state"].encode()))
            except Exception:
                log.warning("레이아웃 복원 실패")

    def closeEvent(self, event) -> None:
        self._save_layout()
        self._engine.stop()
        self._calc.stop()
        self._broker.disconnect()
        self._bus.unsubscribe_all(self)
        super().closeEvent(event)

    # ── DataBus 콜백 (UI 스레드 보장 불필요 — QMetaObject 대신 직접) ──
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
        """price 이벤트 → calc_queue"""
        self._calc_queue.put(data)
