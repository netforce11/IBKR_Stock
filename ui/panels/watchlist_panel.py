"""
ui/panels/watchlist_panel.py
관심종목 패널 — 그룹 탭 + 종목 리스트 + 실시간 현재가

기능:
  ● 그룹 탭 전환 (+ 추가 / 우클릭 → 이름변경/삭제)
  ● 종목 클릭 → watchlist.symbol_selected 토픽 발행
    → OrderbookPanel / OrderPanel / StockChartPanel 전체 반영
  ● 실시간 현재가 / 등락률 표시 (stock.price.* 구독)
  ● 종목 추가(Enter) / 우클릭 → 삭제
  ● 그룹별 종목 JSON 자동 저장/복원
"""
from __future__ import annotations
import logging
from typing import Dict, Optional

from PyQt6.QtWidgets import (
    QTabWidget, QTabBar, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QPushButton, QLabel,
    QMenu, QInputDialog, QMessageBox,
    QAbstractItemView, QSizePolicy, QCalendarWidget,
)
from PyQt6.QtCore import Qt, pyqtSlot, QPoint, QDate
from PyQt6.QtGui import QFont, QColor, QAction, QPalette

from shared.core.bus import DataBus
from shared.core.topics import Topics
from shared.ui.panels.base_panel import BasePanel
from core.watchlist_manager import WatchlistManager

log = logging.getLogger(__name__)

# ── 색상 ──────────────────────────────────────────────────
_BG       = "#F5F5F5"
_BG_ROW   = "#FFFFFF"
_BG_ALT   = "#F0F4F8"
_BORDER   = "#D0D7DE"
_TEXT     = "#111827"
_DIM      = "#555E6B"
_SEL_BG   = "#DBEAFE"
_SEL_FG   = "#1E3A5F"
_UP       = "#1565C0"   # 상승 파랑
_DOWN     = "#C62828"   # 하락 빨강
_FLAT     = "#555E6B"   # 보합 회색

# 현재 선택된 종목 행 강조
_ACT_BG   = "#1565C0"
_ACT_FG   = "#FFFFFF"

# 탭 스타일
_TAB_QSS = """
QTabWidget::pane   { border:1px solid #D0D7DE; background:#F5F5F5; }
QTabBar::tab       { padding:5px 14px; font-size:12px; font-weight:600;
                     background:#E8ECF0; color:#555E6B;
                     border:1px solid #D0D7DE; border-bottom:none;
                     margin-right:2px; border-radius:3px 3px 0 0; }
QTabBar::tab:selected   { background:#0969DA; color:#fff; border-color:#0969DA; }
QTabBar::tab:hover      { background:#D0D8E4; }
"""

_TABLE_QSS = f"""
QTableWidget {{
    background-color:{_BG_ROW};
    alternate-background-color:{_BG_ALT};
    gridline-color:{_BORDER};
    border:none;
    font-size:12px;
}}
QTableWidget::item {{
    background-color:{_BG_ROW};
    padding:3px 6px;
    border:none;
}}
QTableWidget::item:alternate {{
    background-color:{_BG_ALT};
}}
QTableWidget::item:selected {{
    background-color:{_SEL_BG};
    color:{_SEL_FG};
}}
QHeaderView::section {{
    background-color:#E8ECF0;
    color:{_DIM};
    font-size:11px;
    font-weight:600;
    border:none;
    border-bottom:1px solid {_BORDER};
    padding:3px 6px;
}}
QScrollBar:vertical {{
    background:#E8ECF0; width:6px; border:none;
}}
QScrollBar::handle:vertical {{
    background:#9CA3AF; border-radius:3px; min-height:20px;
}}
"""

_INPUT_QSS = (
    f"QLineEdit {{ background:#fff; color:{_TEXT}; border:1px solid {_BORDER};"
    " border-radius:3px; padding:4px 6px; font-size:12px; }"
)
_ADD_BTN_QSS = (
    "QPushButton { background:#0969DA; color:#fff; border:none;"
    " border-radius:3px; padding:4px 10px; font-size:12px; font-weight:600; }"
    "QPushButton:hover { background:#0550AE; }"
)


class _GroupPage(QWidget):
    """탭 하나 = 그룹 하나 = 종목 테이블 + 입력창"""

    # 열 인덱스
    COL_SYM   = 0
    COL_PRICE = 1
    COL_CHG   = 2

    def __init__(self, group_name: str, symbols: list[str],
                 on_select, on_add, on_remove,
                 parent=None) -> None:
        super().__init__(parent)
        self._group  = group_name
        self._on_select = on_select
        self._on_add    = on_add
        self._on_remove = on_remove

        # 종목 → {price, prev_close} 캐시
        self._cache: Dict[str, dict] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # ── 종목 테이블 ─────────────────────────────────
        self._tbl = QTableWidget(0, 3)
        self._tbl.setHorizontalHeaderLabels(["종목", "현재가", "등락률"])
        self._tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._tbl.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setStyleSheet(_TABLE_QSS)
        self._tbl.cellClicked.connect(self._on_cell_clicked)
        self._tbl.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._tbl.customContextMenuRequested.connect(
            self._on_context_menu)
        lay.addWidget(self._tbl)

        # ── 종목 추가 입력 ──────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(4)
        self._input = QLineEdit()
        self._input.setPlaceholderText("종목 코드 입력 (예: AAPL)")
        self._input.setStyleSheet(_INPUT_QSS)
        self._input.returnPressed.connect(self._add_from_input)
        add_btn = QPushButton("＋ 추가")
        add_btn.setStyleSheet(_ADD_BTN_QSS)
        add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._add_from_input)
        row.addWidget(self._input)
        row.addWidget(add_btn)
        lay.addLayout(row)

        # 초기 종목 로드
        for sym in symbols:
            self._append_row(sym)

    # ── 행 추가 ─────────────────────────────────────────
    def _append_row(self, symbol: str) -> None:
        sym = symbol.upper()
        row = self._tbl.rowCount()
        self._tbl.insertRow(row)
        self._tbl.setRowHeight(row, 28)

        sym_item = QTableWidgetItem(sym)
        sym_item.setFont(QFont("", 12, QFont.Weight.Bold))
        price_item = QTableWidgetItem("—")
        chg_item   = QTableWidgetItem("—")
        bg = QColor(_BG_ROW if row % 2 == 0 else _BG_ALT)
        for item in (sym_item, price_item, chg_item):
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setBackground(bg)
            item.setForeground(QColor(_TEXT))
        self._tbl.setItem(row, self.COL_SYM,   sym_item)
        self._tbl.setItem(row, self.COL_PRICE, price_item)
        self._tbl.setItem(row, self.COL_CHG,   chg_item)

    def symbols(self) -> list[str]:
        return [self._tbl.item(r, self.COL_SYM).text()
                for r in range(self._tbl.rowCount())]

    # ── 클릭 ────────────────────────────────────────────
    def _on_cell_clicked(self, row: int, _col: int) -> None:
        item = self._tbl.item(row, self.COL_SYM)
        if item:
            self._highlight_row(row)
            self._on_select(item.text())

    def _highlight_row(self, active_row: int) -> None:
        """선택 행 강조 (파랑), 나머지 초기화"""
        for r in range(self._tbl.rowCount()):
            is_active = (r == active_row)
            bg = QColor(_ACT_BG) if is_active else QColor(_BG_ROW if r % 2 == 0 else _BG_ALT)
            fg = QColor(_ACT_FG) if is_active else QColor(_TEXT)
            for c in range(3):
                item = self._tbl.item(r, c)
                if not item:
                    continue
                item.setBackground(bg)
                if is_active:
                    # 선택 행은 흰색으로 통일
                    item.setForeground(fg)
                elif c == self.COL_CHG:
                    # 등락률 열은 캐시에서 색상 복원
                    sym_item = self._tbl.item(r, self.COL_SYM)
                    sym = sym_item.text() if sym_item else ""
                    cached = self._cache.get(sym, {})
                    price = cached.get("price", 0.0)
                    prev  = cached.get("prev_close", 0.0)
                    if prev and prev > 0:
                        chg = (price - prev) / prev * 100
                        item.setForeground(QColor(
                            _UP if chg > 0 else _DOWN if chg < 0 else _FLAT))
                    else:
                        item.setForeground(QColor(_TEXT))
                else:
                    item.setForeground(fg)

    def highlight_symbol(self, symbol: str) -> None:
        """외부에서 현재 선택 종목 행 강조 요청"""
        for r in range(self._tbl.rowCount()):
            if self._tbl.item(r, self.COL_SYM).text() == symbol.upper():
                self._highlight_row(r)
                return

    # ── 우클릭 컨텍스트 메뉴 ────────────────────────────
    def _on_context_menu(self, pos: QPoint) -> None:
        item = self._tbl.itemAt(pos)
        if not item:
            return
        row = self._tbl.row(item)
        sym = self._tbl.item(row, self.COL_SYM).text()

        menu = QMenu(self)
        act_del = menu.addAction(f"🗑 {sym} 삭제")
        act_up  = menu.addAction("↑ 위로 이동")
        act_dn  = menu.addAction("↓ 아래로 이동")
        chosen = menu.exec(self._tbl.viewport().mapToGlobal(pos))

        if chosen == act_del:
            self._on_remove(self._group, sym)
            self._tbl.removeRow(row)
        elif chosen == act_up and row > 0:
            self._swap_rows(row, row - 1)
        elif chosen == act_dn and row < self._tbl.rowCount() - 1:
            self._swap_rows(row, row + 1)

    def _swap_rows(self, r1: int, r2: int) -> None:
        for col in range(3):
            t1 = self._tbl.item(r1, col)
            t2 = self._tbl.item(r2, col)
            if t1 and t2:
                t1.setText(t2.text()), t2.setText(t1.text())
                t1.setForeground(t2.foreground()), t2.setForeground(t1.foreground())
        # 캐시는 종목명 기반이라 swap 불필요

    # ── 입력창에서 추가 ─────────────────────────────────
    def _add_from_input(self) -> None:
        sym = self._input.text().strip().upper()
        if not sym:
            return
        existing = self.symbols()
        if sym in existing:
            self._input.clear()
            return
        ok = self._on_add(self._group, sym)
        if ok:
            self._append_row(sym)
        self._input.clear()

    # ── 실시간 가격 갱신 ─────────────────────────────────
    def update_price(self, symbol: str, data: dict) -> None:
        sym = symbol.upper()
        price = data.get("last") or data.get("price", 0.0)
        prev  = data.get("prev_close", 0.0)
        self._cache[sym] = {"price": price, "prev_close": prev}

        for r in range(self._tbl.rowCount()):
            if self._tbl.item(r, self.COL_SYM).text() == sym:
                p_item = self._tbl.item(r, self.COL_PRICE)
                c_item = self._tbl.item(r, self.COL_CHG)
                if price:
                    p_item.setText(f"${price:,.2f}")
                if prev and prev > 0:
                    chg_pct = (price - prev) / prev * 100
                    sign    = "+" if chg_pct >= 0 else ""
                    c_item.setText(f"{sign}{chg_pct:.2f}%")
                    color = QColor(_UP if chg_pct > 0
                                   else _DOWN if chg_pct < 0 else _FLAT)
                    c_item.setForeground(color)
                break


# ════════════════════════════════════════════════════════
# WatchlistPanel
# ════════════════════════════════════════════════════════
class WatchlistPanel(BasePanel):
    PANEL_ID     = "watchlist"
    TITLE        = "관심종목"
    DEFAULT_SIZE = (300, 500)

    def __init__(self, bus: DataBus,
                 wl_manager: WatchlistManager,
                 feed=None,
                 parent=None) -> None:
        super().__init__(bus, parent)
        self._wl   = wl_manager
        self._feed = feed           # IBKRFeed — 구독 추가/삭제용
        self._pages: Dict[str, _GroupPage] = {}   # group_name → page
        self._cur_symbol: str = wl_manager.last_symbol

        # 글로벌 다크 테마 덮어쓰기 — 명시적 라이트 팔레트 설정
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window,     QColor(_BG))
        pal.setColor(QPalette.ColorRole.Base,       QColor(_BG_ROW))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(_BG_ALT))
        pal.setColor(QPalette.ColorRole.Text,       QColor(_TEXT))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(_TEXT))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"WatchlistPanel {{ background:{_BG}; }}")
        self._build_ui()

        # 모든 그룹의 모든 종목 가격 구독 (DataBus + 브로커 피드)
        for g in self._wl.groups:
            for sym in g["symbols"]:
                bus.subscribe(Topics.stock_price(sym), self,
                              lambda d, s=sym: self._invoke_main("_on_price", (s, d)))
                if feed:
                    try:
                        feed.subscribe(sym)
                    except Exception:
                        pass

    # ── UI 구성 ─────────────────────────────────────────
    def _build_ui(self) -> None:
        # 헤더 — 현재 선택 종목 표시
        hdr = QHBoxLayout()
        hdr.setContentsMargins(6, 4, 6, 2)
        lbl = QLabel("관심종목")
        lbl.setStyleSheet(
            "font-size:13px; font-weight:700; color:#111827;")
        self._cur_lbl = QLabel(f"▶ {self._cur_symbol}")
        self._cur_lbl.setStyleSheet(
            "font-size:12px; color:#0969DA; font-weight:600;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(self._cur_lbl)
        self._layout.addLayout(hdr)

        # 그룹 탭
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(_TAB_QSS)
        self._tabs.setTabsClosable(False)
        self._tabs.tabBar().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._tabs.tabBar().customContextMenuRequested.connect(
            self._on_tab_context_menu)
        self._layout.addWidget(self._tabs)

        # 그룹 탭 추가 버튼 (탭바 끝에 "＋")
        add_tab_btn = QToolButton_compat(self)
        add_tab_btn.setText(" ＋ ")
        add_tab_btn.setToolTip("새 그룹 추가")
        add_tab_btn.setStyleSheet(
            "QToolButton { font-size:14px; font-weight:bold;"
            " border:none; color:#0969DA; padding:0 4px; }"
            "QToolButton:hover { color:#0550AE; }")
        add_tab_btn.clicked.connect(self._add_group)
        self._tabs.setCornerWidget(add_tab_btn)

        # 기존 그룹 페이지 생성
        for g in self._wl.groups:
            self._add_tab_page(g["name"], g["symbols"])

        # ── 하단 캘린더 (차트 날짜 연동) ─────────────────
        self._build_calendar()

    def _build_calendar(self) -> None:
        """관심종목 하단 캘린더 — 날짜 클릭 시 CHART_DATE_SELECT 발행"""
        # 구분선 레이블
        sep = QLabel("── 차트 날짜 ──")
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep.setStyleSheet(
            "font-size:10px; color:#9CA3AF; padding:4px 0 2px 0;")
        self._layout.addWidget(sep)

        self._wl_calendar = QCalendarWidget()
        self._wl_calendar.setMaximumDate(QDate.currentDate())
        self._wl_calendar.setSelectedDate(QDate.currentDate())
        self._wl_calendar.setGridVisible(False)
        self._wl_calendar.setNavigationBarVisible(True)
        self._wl_calendar.setFixedHeight(190)
        self._wl_calendar.setStyleSheet("""
            QCalendarWidget QWidget { background:#F5F5F5; color:#111827; }
            QCalendarWidget QAbstractItemView {
                background:#fff; color:#111827;
                selection-background-color:#0969DA;
                selection-color:#fff;
            }
            QCalendarWidget QToolButton {
                background:#E8ECF0; color:#111827;
                border:none; border-radius:3px;
                padding:2px 6px; font-size:11px;
            }
            QCalendarWidget QToolButton:hover { background:#D0D8E4; }
            QCalendarWidget QMenu {
                background:#fff; color:#111827;
            }
            QCalendarWidget #qt_calendar_navigationbar {
                background:#E8ECF0;
            }
        """)
        self._wl_calendar.selectionChanged.connect(self._on_cal_date_changed)
        self._layout.addWidget(self._wl_calendar)

        # 이전/다음 버튼
        nav = QHBoxLayout()
        nav.setSpacing(4)
        _nav_qss = (
            "QPushButton { background:#E8ECF0; color:#111827;"
            " border:1px solid #D0D7DE; border-radius:3px;"
            " padding:3px 8px; font-size:11px; }"
            "QPushButton:hover { background:#D0D8E4; }"
        )
        btn_prev = QPushButton("◀ 전날")
        btn_next = QPushButton("다음 ▶")
        btn_prev.setStyleSheet(_nav_qss)
        btn_next.setStyleSheet(_nav_qss)
        btn_prev.clicked.connect(self._cal_prev_day)
        btn_next.clicked.connect(self._cal_next_day)
        nav.addWidget(btn_prev)
        nav.addWidget(btn_next)
        self._layout.addLayout(nav)

    def _on_cal_date_changed(self) -> None:
        """캘린더 날짜 선택 → CHART_DATE_SELECT DataBus 발행"""
        d = self._wl_calendar.selectedDate().toPyDate()
        self._bus.publish(Topics.CHART_DATE_SELECT, {"date": d.isoformat()})

    def _cal_prev_day(self) -> None:
        from datetime import timedelta
        d = self._wl_calendar.selectedDate().toPyDate() - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        self._wl_calendar.setSelectedDate(QDate(d.year, d.month, d.day))

    def _cal_next_day(self) -> None:
        from datetime import timedelta, date
        d = self._wl_calendar.selectedDate().toPyDate() + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        if d > date.today():
            d = date.today()
        self._wl_calendar.setSelectedDate(QDate(d.year, d.month, d.day))

    def _add_tab_page(self, name: str, symbols: list[str]) -> None:
        page = _GroupPage(
            group_name=name,
            symbols=symbols,
            on_select=self._on_symbol_select,
            on_add=self._wl.add_symbol,
            on_remove=self._wl.remove_symbol,
        )
        self._pages[name] = page
        self._tabs.addTab(page, name)

    # ── 종목 선택 → DataBus 발행 ────────────────────────
    def _on_symbol_select(self, symbol: str) -> None:
        sym = symbol.upper()
        self._cur_symbol = sym
        self._cur_lbl.setText(f"▶ {sym}")
        self._wl.set_last_symbol(sym)

        # 현재 페이지 행 강조
        cur_page = self._current_page()
        if cur_page:
            cur_page.highlight_symbol(sym)

        # 피드 구독 추가 (아직 안 됐을 경우)
        if self._feed:
            try:
                self._feed.subscribe(sym)
            except Exception:
                pass

        # 전체 패널 연동: 호가창 / 주문 / 차트 동시 전환
        self._bus.publish(Topics.WATCHLIST_SELECT, {"symbol": sym})
        log.debug("관심종목 선택: %s", sym)

    def _current_page(self) -> Optional[_GroupPage]:
        idx = self._tabs.currentIndex()
        w = self._tabs.widget(idx)
        return w if isinstance(w, _GroupPage) else None

    # ── 실시간 가격 콜백 ────────────────────────────────
    @pyqtSlot(object)
    def _on_price(self, payload) -> None:
        sym, data = payload
        for page in self._pages.values():
            page.update_price(sym, data)

    # ── 그룹 탭 우클릭 컨텍스트 ────────────────────────
    def _on_tab_context_menu(self, pos: QPoint) -> None:
        bar = self._tabs.tabBar()
        idx = bar.tabAt(pos)
        if idx < 0:
            return
        name = self._tabs.tabText(idx)

        menu = QMenu(self)
        act_rename = menu.addAction("✏ 그룹 이름 변경")
        act_left   = menu.addAction("← 왼쪽으로")
        act_right  = menu.addAction("→ 오른쪽으로")
        menu.addSeparator()
        act_del    = menu.addAction("🗑 그룹 삭제")
        chosen = menu.exec(bar.mapToGlobal(pos))

        if chosen == act_rename:
            self._rename_group(idx, name)
        elif chosen == act_left:
            self._wl.move_group(name, -1)
            self._rebuild_tabs()
        elif chosen == act_right:
            self._wl.move_group(name, +1)
            self._rebuild_tabs()
        elif chosen == act_del:
            if QMessageBox.question(
                    self, "그룹 삭제",
                    f"'{name}' 그룹을 삭제할까요?",
                    QMessageBox.StandardButton.Yes |
                    QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                ok = self._wl.delete_group(name)
                if ok:
                    self._tabs.removeTab(idx)
                    self._pages.pop(name, None)

    def _add_group(self) -> None:
        name, ok = QInputDialog.getText(
            self, "새 그룹", "그룹 이름을 입력하세요:")
        if ok and name.strip():
            if self._wl.add_group(name.strip()):
                self._add_tab_page(name.strip(), [])

    def _rename_group(self, idx: int, old_name: str) -> None:
        new_name, ok = QInputDialog.getText(
            self, "그룹 이름 변경", "새 이름:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            if self._wl.rename_group(old_name, new_name.strip()):
                self._tabs.setTabText(idx, new_name.strip())
                page = self._pages.pop(old_name, None)
                if page:
                    page._group = new_name.strip()
                    self._pages[new_name.strip()] = page

    def _rebuild_tabs(self) -> None:
        """그룹 순서 변경 후 탭 재구성"""
        old_pages = dict(self._pages)
        while self._tabs.count():
            self._tabs.removeTab(0)
        self._pages.clear()
        for g in self._wl.groups:
            page = old_pages.get(g["name"])
            if page:
                self._pages[g["name"]] = page
                self._tabs.addTab(page, g["name"])

    # ── 패널 종료 시 구독 해제 ──────────────────────────
    def closeEvent(self, event) -> None:
        self._bus.unsubscribe_all(self)
        super().closeEvent(event)

    def register_shortcuts(self) -> list:
        return []


# PyQt6 QToolButton (코너 위젯용 경량 임포트)
from PyQt6.QtWidgets import QToolButton as QToolButton_compat  # noqa: E402
