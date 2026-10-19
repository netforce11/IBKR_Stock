# ui/panels/market_journal_panel.py
# 시장 추적 관리 일지 패널
#
# 기능:
#   - 1주 / 2주 단위 테이블 뷰 (날짜=행, 항목=열)
#   - 행 클릭 → 하단 편집 폼 로드
#   - 경제지표: Tradier_Working 캘린더 JSON 자동 로드
#   - 데이터 저장: ~/trading_terminal/ibkr_stock/data/market_journal.json
#   - 색상: 기존 라이트 테마 (#F5F5F5) 통일

from __future__ import annotations
import json
import logging
from datetime import date, timedelta
from pathlib  import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QLineEdit,
    QTextEdit, QSplitter, QFrame, QSizePolicy,
    QButtonGroup,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui  import QColor, QFont

from shared.ui.panels.base_panel import BasePanel
from shared.core.bus import DataBus

log = logging.getLogger(__name__)

# ── 저장 경로 ─────────────────────────────────────────────────
_JOURNAL_PATH = (Path.home() / "trading_terminal" / "ibkr_stock"
                 / "data" / "market_journal.json")
# Tradier 경제지표 캘린더 (읽기 전용)
_TRADIER_CAL  = (Path.home() / "trading_terminal" / "Tradier_Working"
                 / "data" / "calendar_memos.json")

# ── 색상 (라이트 테마 — 기존 패널과 동일) ──────────────────────
_BG        = "#F5F5F5"
_BG_ROW    = "#FFFFFF"
_BG_ALT    = "#F0F4F8"
_BG_TODAY  = "#DBEAFE"   # 오늘 행 강조 (연파랑)
_BG_HEADER = "#E8ECF0"
_BORDER    = "#D0D7DE"
_TEXT      = "#111827"
_DIM       = "#555E6B"
_SEL_BG    = "#DBEAFE"
_ACCENT    = "#1565C0"
_UP        = "#1565C0"
_DOWN      = "#C62828"
_WEEKEND   = "#F8FAFC"   # 주 구분 (주말 다음 월요일 위 선)

_BTN = (
    "QPushButton { background:#E8ECF0; color:#111827; border:1px solid #D0D7DE;"
    " border-radius:3px; padding:3px 10px; font-size:11px; }"
    "QPushButton:checked { background:#1565C0; color:#fff; border-color:#1565C0; }"
    "QPushButton:hover { background:#D0D8E4; }"
)
_BTN_SAVE = (
    "QPushButton { background:#1565C0; color:#fff; font-weight:bold;"
    " border:none; border-radius:3px; padding:4px 16px; font-size:12px; }"
    "QPushButton:hover { background:#1976D2; }"
)
_BTN_CANCEL = (
    "QPushButton { background:#E8ECF0; color:#555E6B; border:1px solid #D0D7DE;"
    " border-radius:3px; padding:4px 12px; font-size:12px; }"
    "QPushButton:hover { background:#D0D8E4; }"
)
_INPUT = (
    "QLineEdit, QTextEdit { background:#FFFFFF; color:#111827;"
    " border:1px solid #D0D7DE; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
    "QLineEdit:focus, QTextEdit:focus { border-color:#1565C0; }"
)
_LABEL = "QLabel { color:#555E6B; font-size:11px; }"
_LABEL_BOLD = "QLabel { color:#111827; font-size:12px; font-weight:bold; }"

# ── 테이블 컬럼 정의 ─────────────────────────────────────────
_COLS = [
    ("날짜",    80),
    ("요일",    36),
    ("경제지표", 140),
    ("주식분기점\n(TQQQ/SQQQ)", 120),
    ("옵션분기점", 110),
    ("선물분기점\n(MNQ)", 110),
    ("증거금\n($)", 72),
    ("기대수익\n주식($)", 80),
    ("기대수익\n옵션($)", 80),
    ("기대수익\n선물($)", 80),
    ("메모",    160),
]
_DOW_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _trading_days(start: date, n: int) -> list[date]:
    """start 날짜부터 n 거래일(월~금) 반환"""
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _week_start(d: date) -> date:
    """해당 날의 월요일 반환"""
    return d - timedelta(days=d.weekday())


class MarketJournalPanel(BasePanel):
    PANEL_ID     = "market_journal"
    TITLE        = "시장 일지"
    DEFAULT_SIZE = (1100, 620)

    def __init__(self, bus: DataBus, parent=None) -> None:
        self._journal: dict = {}        # {"2026-10-19": {...}}
        self._cal_data: dict = {}       # Tradier 캘린더
        self._view_weeks = 1            # 1 or 2
        self._sel_date: date | None = None
        super().__init__(bus, parent)
        self._load_journal()
        self._load_tradier_cal()
        self._refresh_table()
        # 오늘 행 자동 선택
        self._select_date(date.today())

    # ══════════════════════════════════════════════════════════
    # UI 구성
    # ══════════════════════════════════════════════════════════
    def _setup_ui(self) -> None:
        super()._setup_ui()
        self.setStyleSheet(f"QWidget {{ background:{_BG}; }}")

        # ── 툴바 ────────────────────────────────────────────
        tb = QWidget()
        tb.setFixedHeight(38)
        tb.setStyleSheet(
            f"QWidget {{ background:{_BG_HEADER};"
            f" border-bottom:1px solid {_BORDER}; }}"
        )
        tb_lay = QHBoxLayout(tb)
        tb_lay.setContentsMargins(8, 4, 8, 4)
        tb_lay.setSpacing(6)

        lbl = QLabel("📋 시장 추적 일지")
        lbl.setStyleSheet("QLabel { color:#111827; font-size:13px; font-weight:bold; }")
        tb_lay.addWidget(lbl)
        tb_lay.addStretch()

        # 이전/다음 주 이동
        self._btn_prev = QPushButton("◀ 이전")
        self._btn_next = QPushButton("다음 ▶")
        self._btn_prev.setStyleSheet(_BTN)
        self._btn_next.setStyleSheet(_BTN)
        self._btn_prev.setFixedWidth(64)
        self._btn_next.setFixedWidth(64)
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_next.clicked.connect(self._go_next)

        # 1주/2주 토글
        self._btn_1w = QPushButton("1주")
        self._btn_2w = QPushButton("2주")
        for b in (self._btn_1w, self._btn_2w):
            b.setCheckable(True)
            b.setFixedWidth(48)
            b.setStyleSheet(_BTN)
        self._btn_1w.setChecked(True)
        self._btn_1w.clicked.connect(lambda: self._set_weeks(1))
        self._btn_2w.clicked.connect(lambda: self._set_weeks(2))

        # 오늘로 이동
        btn_today = QPushButton("오늘")
        btn_today.setStyleSheet(_BTN)
        btn_today.setFixedWidth(48)
        btn_today.clicked.connect(self._goto_today)

        for w in (self._btn_prev, self._btn_next,
                  self._btn_1w, self._btn_2w, btn_today):
            tb_lay.addWidget(w)

        self._layout.addWidget(tb)

        # ── 스플리터: 상단 테이블 | 하단 편집 폼 ───────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(4)

        # 테이블
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self._table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self._table.horizontalHeader().setStyleSheet(
            f"QHeaderView::section {{ background:{_BG_HEADER};"
            f" color:{_TEXT}; border:1px solid {_BORDER};"
            f" font-size:11px; padding:4px; }}"
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(True)
        self._table.setGridStyle(Qt.PenStyle.SolidLine)
        self._table.setStyleSheet(
            f"QTableWidget {{ background:{_BG_ROW}; color:{_TEXT};"
            f" border:1px solid {_BORDER}; font-size:11px; gridline-color:{_BORDER}; }}"
            f"QTableWidget::item:selected {{ background:{_SEL_BG}; color:{_TEXT}; }}"
        )
        for i, (_, w) in enumerate(_COLS):
            self._table.setColumnWidth(i, w)
        self._table.horizontalHeader().setSectionResizeMode(
            10, QHeaderView.ResizeMode.Stretch)   # 메모 열 자동 확장
        self._table.cellClicked.connect(self._on_row_clicked)
        splitter.addWidget(self._table)

        # 편집 폼
        splitter.addWidget(self._build_edit_form())
        splitter.setSizes([380, 240])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        self._layout.addWidget(splitter, 1)

        # 기준 날짜 (현재 뷰의 시작 월요일)
        self._view_start = _week_start(date.today())

    def _build_edit_form(self) -> QWidget:
        """하단 편집 폼"""
        w = QWidget()
        w.setStyleSheet(
            f"QWidget {{ background:{_BG_HEADER};"
            f" border-top:2px solid {_ACCENT}; }}"
        )
        root = QVBoxLayout(w)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # 폼 제목
        self._form_title = QLabel("행을 클릭하면 편집할 수 있습니다")
        self._form_title.setStyleSheet(_LABEL_BOLD)
        root.addWidget(self._form_title)

        # ── 행 1: 경제지표 / 주식분기점 / 옵션분기점 ──────
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        def _field(label: str, width: int | None = None) -> tuple:
            vb = QVBoxLayout()
            vb.setSpacing(2)
            lbl = QLabel(label)
            lbl.setStyleSheet(_LABEL)
            inp = QLineEdit()
            inp.setStyleSheet(_INPUT)
            if width:
                inp.setFixedWidth(width)
            vb.addWidget(lbl)
            vb.addWidget(inp)
            return vb, inp

        vb, self._f_econ    = _field("경제지표 (Tradier 자동로드 + 수정 가능)", 260)
        row1.addLayout(vb)
        vb, self._f_stock   = _field("주식분기점 (TQQQ↑/SQQQ↓)", 180)
        row1.addLayout(vb)
        vb, self._f_option  = _field("옵션분기점", 160)
        row1.addLayout(vb)
        vb, self._f_futures = _field("선물분기점 MNQ (상단/하단)", 160)
        row1.addLayout(vb)
        root.addLayout(row1)

        # ── 행 2: 증거금 / 기대수익 3개 / 메모 ────────────
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        vb, self._f_margin   = _field("증거금 ($)", 90)
        row2.addLayout(vb)
        vb, self._f_exp_stk  = _field("기대수익 주식 ($)", 100)
        row2.addLayout(vb)
        vb, self._f_exp_opt  = _field("기대수익 옵션 ($)", 100)
        row2.addLayout(vb)
        vb, self._f_exp_fut  = _field("기대수익 선물 ($)", 100)
        row2.addLayout(vb)

        # 메모 (늘어나는 텍스트)
        memo_vb = QVBoxLayout()
        memo_vb.setSpacing(2)
        memo_lbl = QLabel("메모")
        memo_lbl.setStyleSheet(_LABEL)
        self._f_memo = QLineEdit()
        self._f_memo.setStyleSheet(_INPUT)
        memo_vb.addWidget(memo_lbl)
        memo_vb.addWidget(self._f_memo)
        row2.addLayout(memo_vb, 1)

        root.addLayout(row2)

        # ── 저장/취소 버튼 ─────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("취소")
        btn_cancel.setStyleSheet(_BTN_CANCEL)
        btn_cancel.clicked.connect(self._on_cancel)
        btn_save = QPushButton("저장")
        btn_save.setStyleSheet(_BTN_SAVE)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        root.addLayout(btn_row)

        return w

    # ══════════════════════════════════════════════════════════
    # 데이터 로드/저장
    # ══════════════════════════════════════════════════════════
    def _load_journal(self) -> None:
        try:
            if _JOURNAL_PATH.exists():
                self._journal = json.loads(_JOURNAL_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("[Journal] 로드 실패: %s", e)

    def _save_journal(self) -> None:
        try:
            _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            _JOURNAL_PATH.write_text(
                json.dumps(self._journal, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            log.error("[Journal] 저장 실패: %s", e)

    def _load_tradier_cal(self) -> None:
        """Tradier_Working 경제지표 캘린더 읽기 (읽기 전용)"""
        try:
            if _TRADIER_CAL.exists():
                raw = json.loads(_TRADIER_CAL.read_text(encoding="utf-8"))
                # 값이 dict면 텍스트 추출, str이면 그대로
                for k, v in raw.items():
                    if isinstance(v, dict):
                        # memo 키 우선, 없으면 첫 번째 값
                        self._cal_data[k] = (
                            v.get("memo") or v.get("text") or
                            next(iter(v.values()), "") or ""
                        )
                    elif isinstance(v, str):
                        self._cal_data[k] = v
        except Exception as e:
            log.warning("[Journal] Tradier 캘린더 로드 실패: %s", e)

    def _get_entry(self, d: date) -> dict:
        key = d.isoformat()
        return self._journal.get(key, {})

    def _set_entry(self, d: date, entry: dict) -> None:
        self._journal[d.isoformat()] = entry
        self._save_journal()

    # ══════════════════════════════════════════════════════════
    # 테이블 렌더링
    # ══════════════════════════════════════════════════════════
    def _refresh_table(self) -> None:
        n_days = 5 * self._view_weeks
        days   = _trading_days(self._view_start, n_days)
        today  = date.today()

        self._table.setRowCount(len(days))
        self._row_dates = days   # 행 인덱스 → date 매핑

        for r, d in enumerate(days):
            key    = d.isoformat()
            entry  = self._journal.get(key, {})
            dow    = _DOW_KR[d.weekday()]
            is_today = (d == today)

            # 셀 배경색
            if is_today:
                bg = QColor(_BG_TODAY)
            elif r % 2 == 0:
                bg = QColor(_BG_ROW)
            else:
                bg = QColor(_BG_ALT)

            # 2주 보기: 두 번째 주 앞에 구분선 효과 (굵은 상단 테두리는
            #   QTableWidget에서 직접 못 그리므로 배경으로 구분)
            if self._view_weeks == 2 and r == 5:
                self._table.setRowHeight(r, 26)

            values = [
                d.strftime("%m/%d"),
                dow,
                entry.get("econ",    self._cal_data.get(key, "")),
                entry.get("stock",   ""),
                entry.get("option",  ""),
                entry.get("futures", ""),
                entry.get("margin",  ""),
                entry.get("exp_stk", ""),
                entry.get("exp_opt", ""),
                entry.get("exp_fut", ""),
                entry.get("memo",    ""),
            ]

            for c, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setForeground(QColor(_TEXT))
                item.setBackground(bg)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter |
                    (Qt.AlignmentFlag.AlignCenter if c < 2
                     else Qt.AlignmentFlag.AlignLeft)
                )
                if is_today and c == 0:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self._table.setItem(r, c, item)

            self._table.setRowHeight(r, 26)

    # ══════════════════════════════════════════════════════════
    # 행 선택 / 편집 폼
    # ══════════════════════════════════════════════════════════
    def _on_row_clicked(self, row: int, _col: int) -> None:
        if row < len(self._row_dates):
            self._select_date(self._row_dates[row])

    def _select_date(self, d: date) -> None:
        self._sel_date = d
        key   = d.isoformat()
        entry = self._journal.get(key, {})

        # Tradier 캘린더 자동 로드 (저장된 값 없을 때만)
        econ_default = entry.get("econ", self._cal_data.get(key, ""))

        self._form_title.setText(
            f"✏️  {d.strftime('%Y-%m-%d')} ({_DOW_KR[d.weekday()]}) 편집"
        )
        self._f_econ.setText(econ_default)
        self._f_stock.setText(entry.get("stock",   ""))
        self._f_option.setText(entry.get("option",  ""))
        self._f_futures.setText(entry.get("futures", ""))
        self._f_margin.setText(entry.get("margin",  ""))
        self._f_exp_stk.setText(entry.get("exp_stk", ""))
        self._f_exp_opt.setText(entry.get("exp_opt", ""))
        self._f_exp_fut.setText(entry.get("exp_fut", ""))
        self._f_memo.setText(entry.get("memo",    ""))

        # 테이블 행 선택 표시
        if hasattr(self, "_row_dates"):
            for r, rd in enumerate(self._row_dates):
                if rd == d:
                    self._table.selectRow(r)
                    break

    @pyqtSlot()
    def _on_save(self) -> None:
        if self._sel_date is None:
            return
        entry = {
            "econ":    self._f_econ.text().strip(),
            "stock":   self._f_stock.text().strip(),
            "option":  self._f_option.text().strip(),
            "futures": self._f_futures.text().strip(),
            "margin":  self._f_margin.text().strip(),
            "exp_stk": self._f_exp_stk.text().strip(),
            "exp_opt": self._f_exp_opt.text().strip(),
            "exp_fut": self._f_exp_fut.text().strip(),
            "memo":    self._f_memo.text().strip(),
        }
        self._set_entry(self._sel_date, entry)
        self._refresh_table()
        # 저장 후 선택 유지
        self._select_date(self._sel_date)

    @pyqtSlot()
    def _on_cancel(self) -> None:
        if self._sel_date:
            self._select_date(self._sel_date)   # 원래 값으로 복원

    # ══════════════════════════════════════════════════════════
    # 뷰 이동
    # ══════════════════════════════════════════════════════════
    def _set_weeks(self, n: int) -> None:
        self._view_weeks = n
        self._btn_1w.setChecked(n == 1)
        self._btn_2w.setChecked(n == 2)
        self._refresh_table()

    def _go_prev(self) -> None:
        self._view_start -= timedelta(weeks=self._view_weeks)
        self._refresh_table()

    def _go_next(self) -> None:
        self._view_start += timedelta(weeks=self._view_weeks)
        self._refresh_table()

    def _goto_today(self) -> None:
        self._view_start = _week_start(date.today())
        self._refresh_table()
        self._select_date(date.today())

    # ══════════════════════════════════════════════════════════
    # 단축키
    # ══════════════════════════════════════════════════════════
    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [
            ("Ctrl+S", self._on_save),
        ]
