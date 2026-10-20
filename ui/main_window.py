# ui/main_window.py
# 메인 윈도우 — QMdiArea 기반 키움 영웅문 스타일
# 가상화면 슬롯 6개 / 패널찾기 / 탭묶기 / 즐겨찾기 툴바

from __future__ import annotations
import json
import queue
import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QMdiArea, QStatusBar, QMenuBar, QMenu, QToolBar,
    QToolButton, QLabel, QWidget, QButtonGroup, QSizePolicy,
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QMessageBox,
)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from config.settings import CONFIG, LAYOUT_FILE, DATA_DIR
from shared.core.bus import get_bus
from shared.core.topics import Topics
from shared.core.risk_manager import RiskManager
from shared.ui.shortcut_manager import ShortcutManager
from shared.ui.layout_manager import PanelLayoutManager, TabGroupSubWindow
from core.engine import TradingEngine
from core.storage.stock_db import StockDBManager
from core.feed.ibkr_feed import IBKRFeed
from ui.panels.orderbook_panel import OrderbookPanel
from ui.panels.portfolio_panel import PortfolioPanel
from ui.panels.order_panel import OrderPanel
from ui.panels.fill_history_panel import FillHistoryPanel
from ui.panels.watchlist_panel import WatchlistPanel
from ui.panels.settings_panel import SettingsPanel
from ui.panels.market_journal_panel import MarketJournalPanel
from ui.panels.scanner_panel import ScannerPanel
from ui.panels.account_panel import AccountPanel
from core.watchlist_manager import WatchlistManager
from core.storage.scanner_storage import ScannerStorage
from ui.status_bar_widget import HtsStatusBar

log = logging.getLogger(__name__)

# ── 가상화면 슬롯 수 ────────────────────────────────────
_NUM_SLOTS = 6

def _slot_file(slot: int) -> Path:
    return Path(LAYOUT_FILE).parent / f"layout_slot{slot}.json"

# ── 즐겨찾기 파일 ──────────────────────────────────────
_FAV_FILE = Path(LAYOUT_FILE).parent / "toolbar_favorites.json"

# ── 슬롯/즐겨찾기 버튼 스타일 ─────────────────────────
_SLOT_QSS = (
    "QToolButton {"
    "  min-width:28px; min-height:24px;"
    "  font-size:13px; font-weight:600;"
    "  border:1px solid #B0B8C4; border-radius:4px;"
    "  margin:1px 2px; padding:0 4px;"
    "  background:#E8ECF0; color:#333;"
    "}"
    "QToolButton:hover   { background:#D0D8E4; }"
    "QToolButton:checked { background:#0969DA; color:#fff; border-color:#0550AE; }"
)
_BTN_QSS = (
    "QToolButton { min-height:24px; font-size:12px;"
    " border:1px solid #B0B8C4; border-radius:4px;"
    " padding:0 8px; background:#E8ECF0; color:#333; margin:1px 2px; }"
    "QToolButton:hover { background:#D0D8E4; }"
)
_FAV_QSS = (
    "QToolButton { min-height:24px; font-size:12px;"
    " border:1px solid #94A3B8; border-radius:4px;"
    " padding:0 8px; background:#DBEAFE; color:#1E3A5F; margin:1px 2px; }"
    "QToolButton:hover { background:#BFDBFE; }"
)


def _make_broker(bus):
    if CONFIG.broker == "ibkr":
        from broker.ibkr_broker import IBKRBroker
        return IBKRBroker(bus, CONFIG.ibkr.host, CONFIG.ibkr.port, CONFIG.ibkr.client_id)
    from shared.broker.mock_broker import MockBroker
    return MockBroker(bus)


class _BrokerConnectThread(QThread):
    """브로커 연결을 백그라운드 스레드에서 실행 — UI 블로킹 방지"""
    succeeded = pyqtSignal(object)   # broker 객체 전달
    failed    = pyqtSignal(str)      # 에러 메시지 전달

    def __init__(self, bus, parent=None):
        super().__init__(parent)
        self._bus = bus

    def run(self):
        try:
            broker = _make_broker(self._bus)
            broker.connect()
            self.succeeded.emit(broker)
        except Exception as e:
            self.failed.emit(str(e))


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

        # ── QMdiArea ──────────────────────────────────────
        self._mdi = QMdiArea()
        self._mdi.setBackground(Qt.GlobalColor.lightGray)
        self._mdi.setOption(QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation)
        self.setCentralWidget(self._mdi)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        # 가상화면 슬롯 상태 (0 = 없음)
        self._cur_slot: int = 0
        self._switching_slot: bool = False   # 중복 전환 방지
        self._fav_actions: list[tuple] = []   # (QAction, pid)

        self._build_menu()
        self._build_toolbar()

        # 4단계 부팅
        self._boot_step1()

    # ════════════════════════════════════════════════════
    # 메뉴바
    # ════════════════════════════════════════════════════
    def _build_menu(self) -> None:
        mb = self.menuBar()

        # ── 보기 메뉴 ────────────────────────────────────
        view_menu = mb.addMenu("보기(&V)")
        _panel_meta = [
            ("orderbook",    "호가창"),
            ("portfolio",    "포트폴리오"),
            ("account",      "💰 계좌"),
            ("stock_chart",  "차트"),
            ("order",        "주문"),
            ("fill_history", "체결내역"),
            ("scanner",         "🔍 스캐너"),
            ("settings",        "⚙ 설정"),
            ("market_journal",  "📋 시장일지"),
        ]
        self._view_actions: dict[str, QAction] = {}
        for pid, label in _panel_meta:
            act = QAction(label, self, checkable=True, checked=True)
            act.triggered.connect(
                lambda v, p=pid: self._layout_mgr.show_panel(p, v))
            view_menu.addAction(act)
            self._view_actions[pid] = act

        # ── 레이아웃 메뉴 ────────────────────────────────
        layout_menu = mb.addMenu("레이아웃(&L)")
        _layout_acts = [
            ("레이아웃 저장",       self._save_layout),
            ("레이아웃 복원",       self._restore_layout),
            None,
            ("기본 레이아웃",       self._reset_layout),
            None,
            ("타일 배열",           self._mdi.tileSubWindows),
            ("계단식 배열",         self._mdi.cascadeSubWindows),
            None,
            ("📑 탭으로 묶기...",   self._show_tab_group_dialog),
        ]
        for item in _layout_acts:
            if item is None:
                layout_menu.addSeparator()
            else:
                lbl, fn = item
                layout_menu.addAction(QAction(lbl, self, triggered=fn))

    # ════════════════════════════════════════════════════
    # 툴바 — 타이틀바 바로 아래 핵심 기능
    # ════════════════════════════════════════════════════
    def _build_toolbar(self) -> None:
        tb = QToolBar("메인 툴바")
        tb.setMovable(False)
        tb.setStyleSheet("QToolBar { spacing:4px; padding:2px 4px; }")
        self.addToolBar(tb)

        # ── 타일/계단 ────────────────────────────────────
        tb.addAction(QAction("타일", self,
                             triggered=self._mdi.tileSubWindows,
                             toolTip="타일 배열"))
        tb.addAction(QAction("계단", self,
                             triggered=self._mdi.cascadeSubWindows,
                             toolTip="계단식 배열"))
        tb.addSeparator()

        # ── 저장 / 초기화 ────────────────────────────────
        tb.addAction(QAction("저장", self,
                             triggered=self._save_layout,
                             toolTip="현재 레이아웃 저장"))
        tb.addAction(QAction("초기화", self,
                             triggered=self._reset_layout,
                             toolTip="기본 레이아웃으로 초기화"))
        tb.addSeparator()

        # ── 가상화면 슬롯 1~6 ────────────────────────────
        lbl = QLabel("  화면: ")
        lbl.setStyleSheet("color:#444; font-size:12px; padding-right:2px;")
        tb.addWidget(lbl)

        self._slot_btns: list[QToolButton] = []
        self._slot_grp = QButtonGroup(self)
        self._slot_grp.setExclusive(True)

        for s in range(1, _NUM_SLOTS + 1):
            btn = QToolButton(self)
            btn.setText(str(s))
            btn.setCheckable(True)
            btn.setToolTip(f"가상화면 {s}  (클릭: 전환 / 저장 후 복원)")
            btn.setStyleSheet(_SLOT_QSS)
            btn.clicked.connect(lambda _checked, n=s: self._switch_slot(n))
            self._slot_grp.addButton(btn, s)
            tb.addWidget(btn)
            self._slot_btns.append(btn)

        tb.addSeparator()

        # ── 패널 찾기 ────────────────────────────────────
        find_btn = QToolButton(self)
        find_btn.setText("🔍 패널찾기")
        find_btn.setToolTip("패널 이름으로 검색 후 앞으로 가져오기")
        find_btn.setStyleSheet(_BTN_QSS)
        find_btn.clicked.connect(self._show_panel_finder)
        tb.addWidget(find_btn)

        # ── 탭묶기 ───────────────────────────────────────
        tab_btn = QToolButton(self)
        tab_btn.setText("📑 탭묶기")
        tab_btn.setToolTip("선택한 패널들을 하나의 탭 창으로 묶기")
        tab_btn.setStyleSheet(_BTN_QSS)
        tab_btn.clicked.connect(self._show_tab_group_dialog)
        tb.addWidget(tab_btn)

        tb.addSeparator()

        # ── 즐겨찾기 패널 버튼 영역 (동적 추가) ──────────
        self._fav_toolbar = tb
        # 오른쪽 끝 여백 + ⚙ 등록/삭제 버튼
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        self._fav_spacer_action = tb.addWidget(spacer)

        gear_btn = QToolButton(self)
        gear_btn.setText("⚙ 즐겨찾기")
        gear_btn.setToolTip("자주 쓰는 패널을 툴바에 등록/삭제")
        gear_btn.setStyleSheet(_BTN_QSS)
        gear_btn.clicked.connect(self._show_fav_manager)
        self._gear_action = tb.addWidget(gear_btn)

        settings_btn = QToolButton(self)
        settings_btn.setText("⚙ 설정")
        settings_btn.setToolTip("브로커·리스크·접속 설정")
        settings_btn.setStyleSheet(
            "QToolButton { min-height:24px; font-size:12px;"
            " border:1px solid #93C5FD; border-radius:4px;"
            " padding:0 8px; background:#DBEAFE; color:#1E3A5F; margin:1px 2px; }"
            "QToolButton:hover { background:#BFDBFE; }"
        )
        settings_btn.clicked.connect(self._open_settings)
        tb.addWidget(settings_btn)

    # ════════════════════════════════════════════════════
    # 4단계 부팅
    # ════════════════════════════════════════════════════
    def _boot_step1(self) -> None:
        log.info("Boot Step1: Bus init")
        self._status_bar.showMessage("Step1: Bus 초기화...")
        self._boot_step2()

    def _boot_step2(self) -> None:
        log.info("Boot Step2: Broker connect (async)")
        self._status_bar.showMessage("Step2: Broker 연결 중... (TWS/Gateway 응답 대기)")

        self._broker_thread = _BrokerConnectThread(self._bus, self)
        self._broker_thread.succeeded.connect(
            self._on_broker_connect_ok, Qt.ConnectionType.QueuedConnection)
        self._broker_thread.failed.connect(
            self._on_broker_connect_fail, Qt.ConnectionType.QueuedConnection)
        self._broker_thread.start()

    def _on_broker_connect_ok(self, broker) -> None:
        log.info("Boot Step2: Broker connect OK")
        self._broker = broker
        # IBKRBroker: ibapi 스레드 → tick_queue → Qt 메인 스레드 드레인
        # 50ms 간격으로 메인 스레드에서 tick_queue 소비 (힙 손상 방지)
        if hasattr(broker, "drain_tick_queue"):
            from PyQt6.QtCore import QTimer
            self._tick_drain_timer = QTimer(self)
            self._tick_drain_timer.setInterval(50)
            self._tick_drain_timer.timeout.connect(broker.drain_tick_queue)
            self._tick_drain_timer.start()
            log.info("tick_queue drain timer 시작 (50ms)")
        self._boot_step3()

    def _on_broker_connect_fail(self, err_msg: str) -> None:
        log.error("Broker connect failed: %s", err_msg)

        # MockBroker로 자동 폴백 — 나머지 부팅 계속 진행
        from shared.broker.mock_broker import MockBroker
        self._broker = MockBroker(self._bus)
        try:
            self._broker.connect()
        except Exception:
            pass

        self._status_bar.showMessage("⚠ MockBroker 모드 (IBKR 미연결)")

        # 사용자에게 비차단 방식으로 알림 (QMessageBox는 이벤트 루프 블로킹 → 직접 배너 표시)
        from PyQt6.QtWidgets import QLabel
        from PyQt6.QtCore import QTimer
        banner = QLabel(
            f"  ⚠  IBKR 연결 실패 — MockBroker 모드로 실행 중\n"
            f"  원인: {err_msg}\n"
            f"  (TWS/Gateway를 실행한 뒤 재시작하세요)",
            self,
        )
        banner.setStyleSheet(
            "QLabel { background:#FEF3C7; color:#92400E; border:1px solid #F59E0B;"
            " border-radius:4px; padding:8px 12px; font-size:12px; }"
        )
        banner.setWordWrap(True)
        banner.adjustSize()
        # 메인 윈도우 중앙 상단에 배치
        bw = min(self.width() - 40, 600)
        banner.setFixedWidth(bw)
        banner.move((self.width() - bw) // 2, 60)
        banner.show()
        banner.raise_()
        # 10초 후 자동 제거
        QTimer.singleShot(10_000, banner.deleteLater)

        self._boot_step3()

    def _boot_step3(self) -> None:
        log.info("Boot Step3: Feed + DB start")
        self._status_bar.showMessage("Step3: Feed / DB 시작...")

        from shared.calc.calc_process import CalcProcess
        self._calc = CalcProcess(self._bus, self._calc_queue)
        self._calc.start()
        self._bus.subscribe("stock.price.*", self, self._bridge_to_calc)

        self._db = StockDBManager(self._bus)
        self._db.start()

        self._feed = IBKRFeed(self._broker, self._bus)
        self._boot_step4()

    def _boot_step4(self) -> None:
        log.info("Boot Step4: Engine start")
        self._status_bar.showMessage("Step4: Engine 시작...")
        risk = RiskManager(self._bus, CONFIG.risk)
        self._engine = TradingEngine(self._bus, self._cmd_queue, risk, self._broker)
        self._engine.start()

        self._shortcut_mgr = ShortcutManager(self)
        self._layout_mgr = PanelLayoutManager(self._mdi, LAYOUT_FILE)

        # QMdiArea 활성 서브윈도우 변경 → 단축키 활성 패널 전환
        # (focusInEvent 대신 이 시그널을 사용해야 QMdiArea에서 안정적으로 동작)
        self._mdi.subWindowActivated.connect(self._on_sub_activated)

        self._build_panels()

        # 저장된 레이아웃 복원 → 없으면 빈 화면 (패널은 숨긴 채 대기)
        self._layout_mgr.restore()   # 파일 없으면 모두 hide 상태 유지

        # 즐겨찾기 복원 (패널 등록 후에 해야 제목 조회 가능)
        self._load_favorites()

        # 우측 상태바 위젯 (연결 상태 / 구독 수 / 레이턴시)
        self._hts_status = HtsStatusBar(self._status_bar, self._bus, self._feed)

        self._status_bar.showMessage("준비")

        # ── 가상화면 단축키 Ctrl+1 ~ Ctrl+6 ────────────────
        from PyQt6.QtGui import QKeySequence, QShortcut
        for n in range(1, _NUM_SLOTS + 1):
            sc = QShortcut(QKeySequence(f"Ctrl+{n}"), self)
            sc.activated.connect(lambda _n=n: self._switch_slot(_n))

        log.info("Boot complete")

    # ════════════════════════════════════════════════════
    # 패널 생성
    # ════════════════════════════════════════════════════
    def _build_panels(self) -> None:
        # ── 관심종목 ──────────────────────────────────────
        self._wl_mgr = WatchlistManager(DATA_DIR)
        self._wl_panel = WatchlistPanel(self._bus, self._wl_mgr, self._feed)
        self._wl_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._wl_panel, title="관심종목")

        self._ob_panel = OrderbookPanel("AAPL", self._bus)
        self._ob_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._ob_panel, title="호가창 — AAPL")

        self._pf_panel = PortfolioPanel(self._bus)
        self._pf_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._pf_panel, title="포트폴리오")

        self._acct_panel = AccountPanel(self._bus)
        self._acct_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._acct_panel, title="💰 계좌")

        self._order_panel = OrderPanel(self._cmd_queue, self._bus)
        self._order_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._order_panel, title="주문")

        self._fill_panel = FillHistoryPanel(self._bus, self._db)
        self._fill_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._fill_panel, title="체결내역")

        try:
            from ui.panels.chart_panel import StockChartPanel
            self._chart_panel = StockChartPanel(self._bus, broker=self._broker)
            self._chart_panel.set_shortcut_manager(self._shortcut_mgr)
            self._layout_mgr.add(self._chart_panel, title="차트")
        except ImportError as e:
            log.warning("차트 패널 로드 실패 (pyqtgraph?): %s", e)

        # ── 스캐너 패널 ────────────────────────────────────
        try:
            self._scanner_storage = ScannerStorage(DATA_DIR)
            self._scanner_panel = ScannerPanel(
                self._cmd_queue, self._bus, self._broker, self._scanner_storage)
            self._scanner_panel.set_shortcut_manager(self._shortcut_mgr)
            self._layout_mgr.add(self._scanner_panel, title="🔍 스캐너")
            log.info("ScannerPanel 등록 완료 (PANEL_ID=%s)", self._scanner_panel.PANEL_ID)
        except Exception as e:
            log.exception("ScannerPanel 로드 실패: %s", e)
            self._scanner_panel = None

        # ── 설정 패널 ─────────────────────────────────────
        self._settings_panel = SettingsPanel(self._bus)
        self._layout_mgr.add(self._settings_panel, title="⚙ 설정")

        # ── 시장 일지 패널 ────────────────────────────────
        self._journal_panel = MarketJournalPanel(self._bus)
        self._journal_panel.set_shortcut_manager(self._shortcut_mgr)
        self._layout_mgr.add(self._journal_panel, title="📋 시장일지")

        # 관심종목 마지막 선택 종목으로 기본 피드 구독
        _last_sym = self._wl_mgr.last_symbol
        self._feed.subscribe(_last_sym)

    # ════════════════════════════════════════════════════
    # 활성 패널 → 단축키 전환
    # ════════════════════════════════════════════════════
    def _on_sub_activated(self, sub_win) -> None:
        """
        QMdiArea.subWindowActivated 시그널 핸들러.
        서브윈도우가 활성화되면 안에 있는 BasePanel을 찾아
        ShortcutManager에 알린다.
        focusInEvent 대신 이 시그널을 사용해야
        QMdiArea + 자식 위젯 환경에서 안정적으로 동작한다.
        """
        if sub_win is None:
            return
        widget = sub_win.widget()
        if widget is None:
            return
        # TabGroupSubWindow 안에 탭 위젯이 있는 경우도 처리
        if hasattr(widget, "currentWidget"):
            widget = widget.currentWidget() or widget
        if hasattr(widget, "PANEL_ID"):
            self._shortcut_mgr.set_active(widget)

    # ════════════════════════════════════════════════════
    # 레이아웃 관리
    # ════════════════════════════════════════════════════
    def _save_layout(self) -> None:
        """현재 슬롯 파일 + 기본 파일 동시 저장."""
        if not hasattr(self, "_layout_mgr"):
            return
        if self._cur_slot > 0:
            self._layout_mgr.save(_slot_file(self._cur_slot))
        self._layout_mgr.save()
        self._status_bar.showMessage("레이아웃 저장 완료", 2000)

    def _restore_layout(self) -> None:
        if not hasattr(self, "_layout_mgr"):
            return
        target = _slot_file(self._cur_slot) if self._cur_slot > 0 else None
        if not self._layout_mgr.restore(target):
            self._reset_layout()
        self._status_bar.showMessage("레이아웃 복원 완료", 2000)

    def _reset_layout(self, *, save: bool = True) -> None:
        """키움 영웅문 기본 배치"""
        mdi_w = self._mdi.width()  or 1400
        mdi_h = self._mdi.height() or 860

        left_w  = 320
        right_w = mdi_w - left_w - 8
        top_h   = mdi_h // 2
        bot_h   = mdi_h - top_h - 8
        order_w = 280
        fill_h  = 192
        chart_w = right_w - order_w - 8

        wl_w = 280   # 관심종목 너비
        ob_w = left_w  # 호가창 너비 (기존과 동일)

        # 왼쪽: 관심종목 | 호가창  (세로 전체)
        self._layout_mgr.place("watchlist",    0,                     0,               wl_w,   mdi_h)
        self._layout_mgr.place("orderbook",    wl_w+4,                0,               ob_w,   mdi_h)

        right_x = wl_w + 4 + ob_w + 4
        right_w2 = mdi_w - right_x
        chart_w2 = right_w2 - order_w - 8

        acct_w = 300   # 계좌 패널 너비 (주문 패널 옆)
        chart_w3 = right_w2 - order_w - acct_w - 16

        self._layout_mgr.place("order",        right_x,                        0,        order_w,  top_h)
        self._layout_mgr.place("account",      right_x + order_w + 8,          0,        acct_w,   top_h)
        self._layout_mgr.place("portfolio",    right_x + order_w + acct_w + 16, 0,       chart_w3, top_h)
        self._layout_mgr.place("stock_chart",  right_x,               top_h+8,         right_w2, bot_h - fill_h - 8)
        self._layout_mgr.place("fill_history", right_x,               top_h+8+bot_h-fill_h, right_w2, fill_h)

        if save:
            self._layout_mgr.save()

    # ════════════════════════════════════════════════════
    # 가상화면 슬롯 전환
    # ════════════════════════════════════════════════════
    def _switch_slot(self, slot: int) -> None:
        """현재 상태 저장 → 슬롯 파일 복원 (없으면 기본 배치)."""
        # 부팅 미완료 시 무시 (Step4 이전에 버튼 클릭 방어)
        if not hasattr(self, "_layout_mgr"):
            return
        # 중복 전환 방지 (연속 시그널로 인한 segfault 방지)
        if self._switching_slot:
            return
        if slot == self._cur_slot:
            return
        self._switching_slot = True
        try:
            # 현재 슬롯 저장
            if self._cur_slot > 0:
                self._layout_mgr.save(_slot_file(self._cur_slot))
            self._layout_mgr.save()   # 기본 파일도 동기화

            self._cur_slot = slot
            for i, btn in enumerate(self._slot_btns, 1):
                btn.setChecked(i == slot)

            sf = _slot_file(slot)
            if sf.exists():
                self._layout_mgr.restore(sf)
                self._status_bar.showMessage(f"화면 {slot} 복원", 2000)
            else:
                # 새 슬롯: 모든 패널 숨김 → 사용자가 직접 배치
                self._layout_mgr.hide_all()
                self._status_bar.showMessage(f"화면 {slot} — 빈 화면 (패널찾기로 추가)", 2000)
        finally:
            self._switching_slot = False

    # ════════════════════════════════════════════════════
    # 패널 찾기 다이얼로그
    # ════════════════════════════════════════════════════
    def _show_panel_finder(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("패널 찾기")
        dlg.resize(320, 440)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(6)
        lay.setContentsMargins(10, 10, 10, 10)

        search = QLineEdit()
        search.setPlaceholderText("패널 이름 검색...")
        search.setStyleSheet(
            "QLineEdit { font-size:13px; padding:6px 8px;"
            " border:1px solid #B0B8C4; border-radius:4px; }")
        lay.addWidget(search)

        lst = QListWidget()
        lst.setStyleSheet(
            "QListWidget { font-size:13px; border:1px solid #D0D7DE; }"
            "QListWidget::item { padding:6px 8px; }"
            "QListWidget::item:selected { background:#0969DA; color:#fff; }"
            "QListWidget::item:hover { background:#EAF0FB; }")
        lay.addWidget(lst)

        btn_row = QHBoxLayout()
        open_btn   = QPushButton("열기")
        cancel_btn = QPushButton("닫기")
        open_btn.setDefault(True)
        btn_row.addStretch()
        btn_row.addWidget(open_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)
        cancel_btn.clicked.connect(dlg.reject)

        from shared.ui.layout_manager import TabGroupSubWindow
        # ── 진단 로그: _subs 전체 목록 출력 ──────────────────
        log.debug("[패널찾기] _subs 목록: %s",
                  {pid: type(sub).__name__
                   for pid, sub in self._layout_mgr._subs.items()})
        _all: list[tuple[str, str]] = sorted(
            [(pid, sub.windowTitle())
             for pid, sub in self._layout_mgr._subs.items()
             if not pid.startswith("__tabgroup_")],   # 탭그룹 컨테이너 자체는 숨김
            key=lambda x: x[1]
        )

        def _refresh(text: str = "") -> None:
            lst.clear()
            kw = text.strip().lower()
            for pid, title in _all:
                if not kw or kw in title.lower() or kw in pid.lower():
                    item = QListWidgetItem(title)
                    item.setData(Qt.ItemDataRole.UserRole, pid)
                    lst.addItem(item)
            if lst.count():
                lst.setCurrentRow(0)

        def _open_selected() -> None:
            item = lst.currentItem()
            if not item:
                return
            pid = item.data(Qt.ItemDataRole.UserRole)
            self._layout_mgr.bring_to_front(pid)
            dlg.accept()

        lst.itemDoubleClicked.connect(lambda _: _open_selected())
        open_btn.clicked.connect(_open_selected)
        search.returnPressed.connect(_open_selected)
        search.textChanged.connect(_refresh)
        _refresh()
        search.setFocus()
        dlg.exec()

    # ════════════════════════════════════════════════════
    # 탭묶기 다이얼로그
    # ════════════════════════════════════════════════════
    def _show_tab_group_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("📑 탭으로 묶기")
        dlg.resize(340, 420)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)

        from PyQt6.QtWidgets import QLabel as _QL
        lay.addWidget(_QL("탭으로 묶을 패널을 2개 이상 선택하세요.\n"
                          "(더블클릭 또는 체크박스로 선택)"))

        lst = QListWidget()
        lst.setStyleSheet(
            "QListWidget { font-size:13px; border:1px solid #D0D7DE; }"
            "QListWidget::item { padding:6px 8px; }"
            "QListWidget::item:selected { background:#0969DA; color:#fff; }")
        lay.addWidget(lst)

        for pid, sub in sorted(self._layout_mgr._subs.items(),
                                key=lambda kv: kv[1].windowTitle()):
            if isinstance(sub, TabGroupSubWindow):
                continue
            item = QListWidgetItem(sub.windowTitle())
            item.setData(Qt.ItemDataRole.UserRole, pid)
            item.setCheckState(Qt.CheckState.Unchecked)
            lst.addItem(item)

        def _toggle(item: QListWidgetItem) -> None:
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked)

        lst.itemDoubleClicked.connect(_toggle)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("탭 그룹 만들기")
        ok_btn.setDefault(True)
        ok_btn.setStyleSheet(
            "QPushButton { background:#0969DA; color:#fff;"
            " border-radius:4px; padding:4px 12px; font-size:13px; }"
            "QPushButton:hover { background:#0550AE; }")
        cancel_btn = QPushButton("취소")
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)
        cancel_btn.clicked.connect(dlg.reject)

        def _apply() -> None:
            pids = []
            for i in range(lst.count()):
                it = lst.item(i)
                if it.checkState() == Qt.CheckState.Checked:
                    pids.append(it.data(Qt.ItemDataRole.UserRole))
            if len(pids) < 2:
                QMessageBox.warning(dlg, "알림", "2개 이상 선택해주세요.")
                return
            first = self._layout_mgr._subs.get(pids[0])
            r = first.geometry() if first else None
            x, y = (r.x(), r.y()) if r else (100, 100)
            w = max(r.width() if r else 0, 600)
            h = max(r.height() if r else 0, 450)
            self._layout_mgr.create_tab_group(pids, x=x, y=y, w=w, h=h)
            dlg.accept()

        ok_btn.clicked.connect(_apply)
        dlg.exec()

    # ════════════════════════════════════════════════════
    # 즐겨찾기 관리
    # ════════════════════════════════════════════════════
    def _load_favorites(self) -> None:
        try:
            pids = json.loads(_FAV_FILE.read_text()) if _FAV_FILE.exists() else []
        except Exception:
            pids = []
        for pid in pids:
            self._add_fav_btn(pid)

    def _save_favorites(self) -> None:
        pids = [pid for _, pid in self._fav_actions]
        try:
            _FAV_FILE.parent.mkdir(parents=True, exist_ok=True)
            _FAV_FILE.write_text(json.dumps(pids, ensure_ascii=False))
        except Exception as e:
            log.warning("즐겨찾기 저장 실패: %s", e)

    def _add_fav_btn(self, pid: str) -> None:
        if any(p == pid for _, p in self._fav_actions):
            return
        sub = self._layout_mgr._subs.get(pid)
        title = sub.windowTitle() if sub else pid

        btn = QToolButton(self)
        btn.setText(title)
        btn.setToolTip(f"패널 열기: {title}")
        btn.setStyleSheet(_FAV_QSS)
        btn.clicked.connect(lambda _, p=pid: self._layout_mgr.bring_to_front(p))

        # spacer 바로 앞에 삽입
        action = self._fav_toolbar.insertWidget(self._fav_spacer_action, btn)
        self._fav_actions.append((action, pid))

    def _remove_fav_btn(self, pid: str) -> None:
        for i, (action, p) in enumerate(self._fav_actions):
            if p == pid:
                self._fav_toolbar.removeAction(action)
                self._fav_actions.pop(i)
                return

    def _show_fav_manager(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙ 툴바 즐겨찾기 등록/삭제")
        dlg.resize(340, 460)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)

        from PyQt6.QtWidgets import QLabel as _QL
        lay.addWidget(_QL("✅ 체크 = 툴바에 표시  |  더블클릭으로 토글"))

        lst = QListWidget()
        lst.setStyleSheet(
            "QListWidget { font-size:13px; border:1px solid #D0D7DE; }"
            "QListWidget::item { padding:6px 8px; }"
            "QListWidget::item:selected { background:#0969DA; color:#fff; }")
        lay.addWidget(lst)

        fav_pids = {pid for _, pid in self._fav_actions}
        all_panels = sorted(
            [(pid, sub.windowTitle())
             for pid, sub in self._layout_mgr._subs.items()],
            key=lambda x: x[1]
        )
        for pid, title in all_panels:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            item.setCheckState(
                Qt.CheckState.Checked if pid in fav_pids
                else Qt.CheckState.Unchecked)
            lst.addItem(item)

        def _toggle(item: QListWidgetItem) -> None:
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked)

        lst.itemDoubleClicked.connect(_toggle)

        btn_row = QHBoxLayout()
        ok_btn     = QPushButton("확인")
        cancel_btn = QPushButton("취소")
        ok_btn.setDefault(True)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)
        cancel_btn.clicked.connect(dlg.reject)

        def _apply() -> None:
            new_pids: list[str] = []
            for i in range(lst.count()):
                it = lst.item(i)
                if it.checkState() == Qt.CheckState.Checked:
                    new_pids.append(it.data(Qt.ItemDataRole.UserRole))
            for pid in list(fav_pids):
                if pid not in new_pids:
                    self._remove_fav_btn(pid)
            for pid in new_pids:
                self._add_fav_btn(pid)
            self._save_favorites()
            dlg.accept()

        ok_btn.clicked.connect(_apply)
        dlg.exec()

    # ════════════════════════════════════════════════════
    # DataBus 콜백
    # ════════════════════════════════════════════════════
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

    def _open_settings(self) -> None:
        """설정 패널을 앞으로 가져오기"""
        self._layout_mgr.bring_to_front("settings")

    def _bridge_to_calc(self, data) -> None:
        self._calc_queue.put(data)

    # ════════════════════════════════════════════════════
    # 앱 종료
    # ════════════════════════════════════════════════════
    def closeEvent(self, event) -> None:
        self._save_layout()
        self._engine.stop()
        self._calc.stop()
        self._db.stop()
        self._feed.unsubscribe("AAPL")
        self._broker.disconnect()
        self._bus.unsubscribe_all(self)
        super().closeEvent(event)
