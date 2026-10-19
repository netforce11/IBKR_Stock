# ui/panels/market_journal_panel.py
# 시장 추적 관리 일지 패널
#
# 편집 폼 구조:
#   Row1 : 경제지표 | 주식분기점(+📈) | 옵션분기점(D+N) | 선물분기점MNQ
#   Row2 : [주식] 투자금 × 수익률% = 기대수익  |  [옵션] 투자금 × 수익률% = 기대수익
#          [선물] 증거금 × 수익률% = 기대수익
#   Row3 : [분기옵션(D+N)] 투자금 × 수익률% = 기대수익  |  합산손익  |  메모
#
#   기대수익은 투자금 × 수익률 / 100 으로 자동 계산 (읽기전용)
#   합산 = 주식 + 옵션 + 선물 + 분기옵션
#
# 데이터 저장: ~/trading_terminal/ibkr_stock/data/market_journal.json

from __future__ import annotations
import json
import logging
import re
from datetime import date, timedelta
from pathlib  import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QLineEdit,
    QFrame, QSplitter,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui  import QColor

from shared.ui.panels.base_panel import BasePanel
from shared.core.bus import DataBus

log = logging.getLogger(__name__)

# ── 저장 경로 ─────────────────────────────────────────────────
_JOURNAL_PATH = (Path.home() / "trading_terminal" / "ibkr_stock"
                 / "data" / "market_journal.json")
_TRADIER_CAL  = (Path.home() / "trading_terminal" / "Tradier_Working"
                 / "data" / "calendar_memos.json")

# ── 색상 (라이트 테마) ──────────────────────────────────────────
_BG        = "#F5F5F5"
_BG_ROW    = "#FFFFFF"
_BG_ALT    = "#F0F4F8"
_BG_TODAY  = "#DBEAFE"
_BG_HEADER = "#E8ECF0"
_BORDER    = "#D0D7DE"
_TEXT      = "#111827"
_DIM       = "#555E6B"
_SEL_BG    = "#DBEAFE"
_ACCENT    = "#1565C0"
_UP        = "#1565C0"
_DOWN      = "#C62828"
_OPT_CHECK = "#7C3AED"   # 옵션 분기 타진 (보라)
_PIVOT_BG  = "#F5F3FF"   # 분기옵션 섹션 배경

# ── 스타일 ────────────────────────────────────────────────────
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
_BTN_FETCH = (
    "QPushButton { background:#E8F5E9; color:#1B5E20; border:1px solid #81C784;"
    " border-radius:3px; padding:2px 8px; font-size:11px; }"
    "QPushButton:hover { background:#C8E6C9; }"
    "QPushButton:disabled { background:#E8ECF0; color:#999; border-color:#D0D7DE; }"
)
_INPUT = (
    "QLineEdit { background:#FFFFFF; color:#111827;"
    " border:1px solid #D0D7DE; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
    "QLineEdit:focus { border-color:#1565C0; }"
)
_INPUT_CALC = (          # 자동계산 필드 (읽기전용) — 파란 틴트
    "QLineEdit { background:#EFF6FF; color:#1565C0; font-weight:bold;"
    " border:1px solid #BFDBFE; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
)
_INPUT_CALC_NEG = (
    "QLineEdit { background:#FEF2F2; color:#C62828; font-weight:bold;"
    " border:1px solid #FCA5A5; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
)
_INPUT_CALC_ZER = (
    "QLineEdit { background:#F9FAFB; color:#9CA3AF;"
    " border:1px solid #D0D7DE; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
)
_INPUT_PIVOT = (         # 분기옵션 필드 — 보라 틴트
    "QLineEdit { background:#FFFFFF; color:#4C1D95;"
    " border:1px solid #C4B5FD; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
    "QLineEdit:focus { border-color:#7C3AED; }"
)
_INPUT_PIVOT_CALC = (
    "QLineEdit { background:#F5F3FF; color:#6D28D9; font-weight:bold;"
    " border:1px solid #C4B5FD; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
)
_LBL       = "QLabel { color:#555E6B; font-size:11px; }"
_LBL_BOLD  = "QLabel { color:#111827; font-size:12px; font-weight:bold; }"
_LBL_SEC   = ("QLabel { color:#1565C0; font-size:11px; font-weight:bold;"
               " padding:1px 6px; background:#DBEAFE; border-radius:3px; }")
_LBL_PIVOT_SEC = ("QLabel { color:#6D28D9; font-size:11px; font-weight:bold;"
                  " padding:1px 6px; background:#EDE9FE; border-radius:3px; }")
_LBL_OP    = "QLabel { color:#9CA3AF; font-size:13px; padding:0 2px; }"

# ── MNQ 상수 ──────────────────────────────────────────────────
_QQQ_TO_NQ   = 40.0
_TQQQ_TO_QQQ = 7.1
_MNQ_MARGIN  = 2_000
_MNQ_MULT    = 2.0

# ── 항상 조회되는 고정 종목 ────────────────────────────────────
_FIXED_SYMBOLS = ["^NDX", "NVDA"]

# ── 테이블 컬럼 ───────────────────────────────────────────────
_COLS = [
    ("날짜",       72),
    ("요일",       32),
    ("경제지표",   130),
    ("주식분기점", 110),
    ("옵션분기점", 100),
    ("선물(MNQ)",  90),
    ("주식\n기대수익($)", 72),
    ("옵션\n기대수익($)", 72),
    ("선물\n기대수익($)", 72),
    ("분기옵션\n기대수익($)", 75),
    ("합산손익($)", 80),
    ("메모",       130),
]
_COL_OPT   = 4
_COL_TOTAL = 10
_DOW_KR = ["월", "화", "수", "목", "금", "토", "일"]


# ── 헬퍼 ──────────────────────────────────────────────────────
def _trading_days(start: date, n: int) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _nth_trading_day(from_date: date, n: int) -> date:
    count, d = 0, from_date
    while count < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _parse_float(text: str) -> float:
    try:
        return float(text.replace(",", "").replace("$", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _calc_exp(amt_text: str, rate_text: str) -> float:
    """투자금 × 수익률% → 기대수익 달러"""
    amt  = _parse_float(amt_text)
    rate = _parse_float(rate_text)
    return amt * rate / 100.0


def _fmt_exp(val: float) -> str:
    if val > 0:
        return f"+{val:,.0f}"
    elif val < 0:
        return f"{val:,.0f}"
    return ""


# ── 비동기 주가+MNQ 조회 스레드 ──────────────────────────────
class _PriceFetchThread(QThread):
    stock_done = pyqtSignal(str)
    mnq_done   = pyqtSignal(str, float)
    error      = pyqtSignal(str)

    def __init__(self, symbols: list[str], target_date: date, parent=None):
        super().__init__(parent)
        self._symbols = [s.strip().upper() for s in symbols if s.strip()]
        self._date    = target_date

    def run(self) -> None:
        try:
            import yfinance as yf
        except ImportError:
            self.error.emit("yfinance 미설치 (pip install yfinance)")
            return

        start = self._date
        end   = self._date + timedelta(days=4)

        parts_user:  list[str] = []
        parts_fixed: list[str] = []
        tqqq_close = qqq_close = ndx_close = 0.0

        def _get_close(sym: str) -> tuple[float, str]:
            """종가 + 실제 날짜 문자열 반환. 당일 없으면 최근 5일치 사용."""
            # 1차: 지정 날짜 범위
            h = yf.Ticker(sym).history(
                start=start.isoformat(), end=end.isoformat(),
                auto_adjust=True)
            if not h.empty:
                return float(h.iloc[0]["Close"]), h.index[0].strftime("%m/%d")
            # 2차: 최근 5거래일 (오늘 장중/데이터 미반영 대비)
            h2 = yf.Ticker(sym).history(period="5d", auto_adjust=True)
            if not h2.empty:
                return float(h2.iloc[-1]["Close"]), h2.index[-1].strftime("%m/%d")+"*"
            return 0.0, ""

        try:
            for sym in (self._symbols + _FIXED_SYMBOLS):
                is_fixed = sym in _FIXED_SYMBOLS
                close, dt_tag = _get_close(sym)
                if close == 0.0:
                    entry = f"{sym} N/A"
                else:
                    if sym == "^NDX":
                        entry = f"NDX {close:,.0f}"
                        if dt_tag.endswith("*"):
                            entry += f"({dt_tag})"
                        ndx_close = close
                    else:
                        entry = f"{sym} {close:.2f}"
                        if dt_tag.endswith("*"):
                            entry += f"({dt_tag})"
                    if sym == "TQQQ": tqqq_close = close
                    elif sym == "QQQ": qqq_close = close
                (parts_fixed if is_fixed else parts_user).append(entry)

            all_parts = parts_user + (
                [" │ " + " | ".join(parts_fixed)] if parts_fixed else [])
            self.stock_done.emit("".join(all_parts))
        except Exception as e:
            self.error.emit(str(e))
            return

        # MNQ 조회
        mnq_val, mnq_src = 0.0, ""
        try:
            h = yf.Ticker("MNQ=F").history(
                start=start.isoformat(), end=end.isoformat(), auto_adjust=True)
            if not h.empty:
                mnq_val, mnq_src = float(h.iloc[0]["Close"]), "직접"
            else:
                h2 = yf.Ticker("MNQ=F").history(period="5d", auto_adjust=True)
                if not h2.empty:
                    mnq_val, mnq_src = float(h2.iloc[-1]["Close"]), "직접(전일)"
        except Exception:
            pass

        if not mnq_val and ndx_close:
            mnq_val, mnq_src = ndx_close, f"NDX {ndx_close:,.0f}→추정"
        if not mnq_val:
            if not qqq_close:
                try:
                    h = yf.Ticker("QQQ").history(
                        start=start.isoformat(), end=end.isoformat(), auto_adjust=True)
                    if not h.empty:
                        qqq_close = float(h.iloc[0]["Close"])
                except Exception:
                    pass
            if qqq_close:
                mnq_val = round(qqq_close * _QQQ_TO_NQ)
                mnq_src = f"QQQ {qqq_close:.2f}→추정"
        if not mnq_val and tqqq_close:
            mnq_val = round(tqqq_close * _TQQQ_TO_QQQ * _QQQ_TO_NQ)
            mnq_src = f"TQQQ {tqqq_close:.2f}→추정"

        if mnq_val:
            label = (f"MNQ {mnq_val:,.0f}" if mnq_src == "직접"
                     else f"MNQ ~{mnq_val:,.0f} ({mnq_src})")
            self.mnq_done.emit(label, float(mnq_val))


# ══════════════════════════════════════════════════════════════
class MarketJournalPanel(BasePanel):
    PANEL_ID     = "market_journal"
    TITLE        = "시장 일지"
    DEFAULT_SIZE = (1200, 680)

    def __init__(self, bus: DataBus, parent=None) -> None:
        self._journal:      dict       = {}
        self._cal_data:     dict       = {}
        self._view_weeks:   int        = 1
        self._sel_date:     date|None  = None
        self._fetch_thread             = None
        self._mnq_price:    float      = 0.0
        super().__init__(bus, parent)
        self._load_journal()
        self._load_tradier_cal()
        self._refresh_table()
        self._select_date(date.today())

    # ══════════════════════════════════════════════════════════
    # UI 구성
    # ══════════════════════════════════════════════════════════
    def _setup_ui(self) -> None:
        super()._setup_ui()
        self.setStyleSheet(f"QWidget {{ background:{_BG}; }}")

        # 툴바
        tb = QWidget()
        tb.setFixedHeight(38)
        tb.setStyleSheet(f"QWidget {{ background:{_BG_HEADER};"
                         f" border-bottom:1px solid {_BORDER}; }}")
        tb_lay = QHBoxLayout(tb)
        tb_lay.setContentsMargins(8, 4, 8, 4)
        tb_lay.setSpacing(6)
        ttl = QLabel("📋 시장 추적 일지")
        ttl.setStyleSheet("QLabel { color:#111827; font-size:13px; font-weight:bold; }")
        tb_lay.addWidget(ttl)
        tb_lay.addStretch()
        self._btn_prev = QPushButton("◀ 이전")
        self._btn_next = QPushButton("다음 ▶")
        self._btn_1w   = QPushButton("1주")
        self._btn_2w   = QPushButton("2주")
        self._btn_3w   = QPushButton("3주")
        self._btn_4w   = QPushButton("4주")
        btn_today      = QPushButton("오늘")
        for b in (self._btn_prev, self._btn_next, btn_today):
            b.setStyleSheet(_BTN); b.setFixedWidth(64)
        for b in (self._btn_1w, self._btn_2w, self._btn_3w, self._btn_4w):
            b.setCheckable(True); b.setStyleSheet(_BTN); b.setFixedWidth(44)
        self._btn_1w.setChecked(True)
        btn_today.setFixedWidth(48)
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_next.clicked.connect(self._go_next)
        self._btn_1w.clicked.connect(lambda: self._set_weeks(1))
        self._btn_2w.clicked.connect(lambda: self._set_weeks(2))
        self._btn_3w.clicked.connect(lambda: self._set_weeks(3))
        self._btn_4w.clicked.connect(lambda: self._set_weeks(4))
        btn_today.clicked.connect(self._goto_today)
        for w in (self._btn_prev, self._btn_next,
                  self._btn_1w, self._btn_2w, self._btn_3w, self._btn_4w,
                  btn_today):
            tb_lay.addWidget(w)
        self._layout.addWidget(tb)

        # 스플리터
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(4)

        # 테이블
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self._table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.horizontalHeader().setStyleSheet(
            f"QHeaderView::section {{ background:{_BG_HEADER}; color:{_TEXT};"
            f" border:1px solid {_BORDER}; font-size:11px; padding:4px; }}")
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(True)
        self._table.setGridStyle(Qt.PenStyle.SolidLine)
        self._table.setStyleSheet(
            f"QTableWidget {{ background:{_BG_ROW}; color:{_TEXT};"
            f" border:1px solid {_BORDER}; font-size:11px; gridline-color:{_BORDER}; }}"
            f"QTableWidget::item:selected {{ background:{_SEL_BG}; color:{_TEXT}; }}")
        for i, (_, w) in enumerate(_COLS):
            self._table.setColumnWidth(i, w)
        self._table.horizontalHeader().setSectionResizeMode(
            11, QHeaderView.ResizeMode.Stretch)
        self._table.cellClicked.connect(self._on_row_clicked)
        splitter.addWidget(self._table)

        splitter.addWidget(self._build_edit_form())
        splitter.setSizes([360, 320])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        self._layout.addWidget(splitter, 1)
        self._view_start = _week_start(date.today())

    # ──────────────────────────────────────────────────────────
    def _build_edit_form(self) -> QWidget:
        """하단 편집 폼 (3행 구조)"""
        root_w = QWidget()
        root_w.setStyleSheet(
            f"QWidget {{ background:{_BG_HEADER}; border-top:2px solid {_ACCENT}; }}")
        root = QVBoxLayout(root_w)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(4)

        # 폼 제목
        self._form_title = QLabel("행을 클릭하면 편집할 수 있습니다")
        self._form_title.setStyleSheet(_LBL_BOLD)
        root.addWidget(self._form_title)

        # ═══════════════════════════════════════════════════════
        # Row 1: 경제지표 | 주식분기점+📈 | 옵션분기점(D+N) | 선물분기점MNQ
        # ═══════════════════════════════════════════════════════
        row1 = QHBoxLayout(); row1.setSpacing(10)

        # 경제지표
        row1.addLayout(self._vfield("경제지표 (Tradier 자동로드 + 수정 가능)",
                                    "_f_econ", 230))

        # 주식분기점 + 📈 버튼
        stk_vb = QVBoxLayout(); stk_vb.setSpacing(2)
        stk_vb.addWidget(self._lbl(
            "주식분기점  ( 나스닥 지수 | NVDA | 모두 항상 같이 조회 )"))
        stk_inner = QHBoxLayout(); stk_inner.setSpacing(4)
        self._f_stock = QLineEdit()
        self._f_stock.setStyleSheet(_INPUT)
        self._f_stock.setPlaceholderText("TQQQ,SQQQ  (NDX·NVDA 자동포함)")
        self._f_stock.setFixedWidth(200)
        self._btn_fetch = QPushButton("📈 조회")
        self._btn_fetch.setStyleSheet(_BTN_FETCH)
        self._btn_fetch.setFixedWidth(60)
        self._btn_fetch.setToolTip(
            "콤마로 구분 입력 (예: TQQQ,SQQQ)\n"
            "나스닥 지수(^NDX) + NVDA 항상 포함\n"
            "MNQ 선물도 동시 조회")
        self._btn_fetch.clicked.connect(self._fetch_stock_price)
        stk_inner.addWidget(self._f_stock)
        stk_inner.addWidget(self._btn_fetch)
        stk_vb.addLayout(stk_inner)
        row1.addLayout(stk_vb)

        # 옵션분기점 (D+N)
        opt_vb = QVBoxLayout(); opt_vb.setSpacing(2)
        opt_vb.addWidget(self._lbl("옵션분기점 (D+1~D+5 → 해당일 자동 기록)"))
        self._f_option = QLineEdit()
        self._f_option.setStyleSheet(_INPUT)
        self._f_option.setPlaceholderText("예: D+3  또는 직접 입력")
        self._f_option.setFixedWidth(160)
        opt_vb.addWidget(self._f_option)
        row1.addLayout(opt_vb)

        # 선물분기점 MNQ (📈 자동입력)
        fut_vb = QVBoxLayout(); fut_vb.setSpacing(2)
        fut_vb.addWidget(self._lbl("선물분기점 MNQ  (📈 조회 시 자동입력)"))
        self._f_futures = QLineEdit()
        self._f_futures.setStyleSheet(_INPUT)
        self._f_futures.setPlaceholderText("자동조회 또는 직접입력")
        self._f_futures.setFixedWidth(190)
        self._lbl_mnq_hint = QLabel("")
        self._lbl_mnq_hint.setStyleSheet(
            "QLabel { color:#1B5E20; font-size:10px; }")
        fut_vb.addWidget(self._f_futures)
        fut_vb.addWidget(self._lbl_mnq_hint)
        row1.addLayout(fut_vb)
        root.addLayout(row1)

        # 구분선
        root.addWidget(self._hline())

        # ═══════════════════════════════════════════════════════
        # Row 2: 투자금 × 수익률% = 기대수익  (주식 | 옵션 | 선물)
        # ═══════════════════════════════════════════════════════
        row2 = QHBoxLayout(); row2.setSpacing(16)

        row2.addWidget(self._lbl_section("📈 주식"))
        row2.addLayout(self._inv_group(
            "_f_amt_stk", "_f_rate_stk", "_f_exp_stk",
            amt_ph="1,000",  rate_ph="10",  w_amt=80))

        row2.addWidget(self._vsep())

        row2.addWidget(self._lbl_section("📊 옵션 (기본)"))
        row2.addLayout(self._inv_group(
            "_f_amt_opt", "_f_rate_opt", "_f_exp_opt",
            amt_ph="100",  rate_ph="100", w_amt=80))

        row2.addWidget(self._vsep())

        row2.addWidget(self._lbl_section("⚡ 선물 MNQ"))
        row2.addLayout(self._inv_group(
            "_f_amt_fut", "_f_rate_fut", "_f_exp_fut",
            amt_ph="2,000 (1계약)", rate_ph="35",  w_amt=100,
            amt_label="증거금 ($)"))

        row2.addStretch()
        root.addLayout(row2)

        # 구분선
        root.addWidget(self._hline())

        # ═══════════════════════════════════════════════════════
        # Row 3: 분기옵션(D+N) | 합산손익 | 메모 | 버튼
        # ═══════════════════════════════════════════════════════
        row3 = QHBoxLayout(); row3.setSpacing(16)

        pivot_sec = self._lbl_section("🎯 분기옵션 (D+N)", pivot=True)
        row3.addWidget(pivot_sec)
        row3.addLayout(self._inv_group(
            "_f_amt_pivot", "_f_rate_pivot", "_f_exp_pivot",
            amt_ph="100", rate_ph="1,000",
            w_amt=80, pivot=True))

        row3.addWidget(self._vsep())

        # 합산 손익
        tot_vb = QVBoxLayout(); tot_vb.setSpacing(2)
        tot_lbl = QLabel("합산 손익 ($)")
        tot_lbl.setStyleSheet(
            "QLabel { color:#1565C0; font-size:11px; font-weight:bold; }")
        self._f_total = QLineEdit()
        self._f_total.setReadOnly(True)
        self._f_total.setStyleSheet(_INPUT_CALC_ZER)
        self._f_total.setFixedWidth(100)
        self._f_total.setAlignment(Qt.AlignmentFlag.AlignRight)
        tot_vb.addWidget(tot_lbl)
        tot_vb.addWidget(self._f_total)
        row3.addLayout(tot_vb)

        row3.addWidget(self._vsep())

        # 메모
        memo_vb = QVBoxLayout(); memo_vb.setSpacing(2)
        memo_vb.addWidget(self._lbl("메모"))
        self._f_memo = QLineEdit()
        self._f_memo.setStyleSheet(_INPUT)
        memo_vb.addWidget(self._f_memo)
        row3.addLayout(memo_vb, 1)

        # 저장/취소
        btn_vb = QVBoxLayout(); btn_vb.setSpacing(4)
        btn_vb.addStretch()
        btn_cancel = QPushButton("취소")
        btn_cancel.setStyleSheet(_BTN_CANCEL)
        btn_cancel.clicked.connect(self._on_cancel)
        btn_save = QPushButton("저장")
        btn_save.setStyleSheet(_BTN_SAVE)
        btn_save.clicked.connect(self._on_save)
        btn_vb.addWidget(btn_cancel)
        btn_vb.addWidget(btn_save)
        row3.addLayout(btn_vb)
        root.addLayout(row3)

        # 실시간 계산 연결
        for f_amt, f_rate, f_exp in (
            ("_f_amt_stk",   "_f_rate_stk",   "_f_exp_stk"),
            ("_f_amt_opt",   "_f_rate_opt",   "_f_exp_opt"),
            ("_f_amt_fut",   "_f_rate_fut",   "_f_exp_fut"),
            ("_f_amt_pivot", "_f_rate_pivot", "_f_exp_pivot"),
        ):
            getattr(self, f_amt).textChanged.connect(
                lambda _, a=f_amt, r=f_rate, e=f_exp: self._recalc_exp(a, r, e))
            getattr(self, f_rate).textChanged.connect(
                lambda _, a=f_amt, r=f_rate, e=f_exp: self._recalc_exp(a, r, e))

        self._f_margin_ref = self._f_amt_fut   # MNQ hint 연결용 alias
        self._f_amt_fut.textChanged.connect(self._update_mnq_hint)

        return root_w

    # ── UI 헬퍼 ──────────────────────────────────────────────
    def _lbl(self, text: str) -> QLabel:
        l = QLabel(text); l.setStyleSheet(_LBL); return l

    def _lbl_section(self, text: str, pivot=False) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(_LBL_PIVOT_SEC if pivot else _LBL_SEC)
        return l

    def _hline(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet(f"QFrame {{ color:{_BORDER}; }}")
        f.setFixedHeight(1)
        return f

    def _vsep(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet(f"QFrame {{ color:{_BORDER}; }}")
        return f

    def _vfield(self, label: str, attr: str, width: int | None = None) -> QVBoxLayout:
        vb = QVBoxLayout(); vb.setSpacing(2)
        vb.addWidget(self._lbl(label))
        inp = QLineEdit(); inp.setStyleSheet(_INPUT)
        if width: inp.setFixedWidth(width)
        setattr(self, attr, inp)
        vb.addWidget(inp)
        return vb

    def _inv_group(
        self,
        attr_amt: str, attr_rate: str, attr_exp: str,
        amt_ph: str = "", rate_ph: str = "",
        w_amt: int = 80,
        amt_label: str = "투자금 ($)",
        pivot: bool = False,
    ) -> QHBoxLayout:
        """투자금 × 수익률% = 기대수익  레이아웃 반환"""
        inp_style  = _INPUT_PIVOT       if pivot else _INPUT
        calc_style = _INPUT_PIVOT_CALC  if pivot else _INPUT_CALC

        lay = QHBoxLayout(); lay.setSpacing(4)

        # 투자금
        a_vb = QVBoxLayout(); a_vb.setSpacing(1)
        a_vb.addWidget(self._lbl(amt_label))
        a_inp = QLineEdit(); a_inp.setStyleSheet(inp_style)
        a_inp.setPlaceholderText(amt_ph); a_inp.setFixedWidth(w_amt)
        setattr(self, attr_amt, a_inp)
        a_vb.addWidget(a_inp); lay.addLayout(a_vb)

        # ×
        x_vb = QVBoxLayout(); x_vb.setSpacing(1)
        x_vb.addWidget(QLabel(""))  # spacer for label row
        x_lbl = QLabel("×"); x_lbl.setStyleSheet(_LBL_OP)
        x_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        x_vb.addWidget(x_lbl); lay.addLayout(x_vb)

        # 수익률
        r_vb = QVBoxLayout(); r_vb.setSpacing(1)
        r_vb.addWidget(self._lbl("수익률 (%)"))
        r_inp = QLineEdit(); r_inp.setStyleSheet(inp_style)
        r_inp.setPlaceholderText(rate_ph); r_inp.setFixedWidth(60)
        setattr(self, attr_rate, r_inp)
        r_vb.addWidget(r_inp); lay.addLayout(r_vb)

        # =
        eq_vb = QVBoxLayout(); eq_vb.setSpacing(1)
        eq_vb.addWidget(QLabel(""))
        eq_lbl = QLabel("="); eq_lbl.setStyleSheet(_LBL_OP)
        eq_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eq_vb.addWidget(eq_lbl); lay.addLayout(eq_vb)

        # 기대수익 (읽기전용)
        e_vb = QVBoxLayout(); e_vb.setSpacing(1)
        e_vb.addWidget(self._lbl("기대수익 ($)"))
        e_inp = QLineEdit(); e_inp.setReadOnly(True)
        e_inp.setStyleSheet(calc_style)
        e_inp.setFixedWidth(80)
        e_inp.setAlignment(Qt.AlignmentFlag.AlignRight)
        setattr(self, attr_exp, e_inp)
        e_vb.addWidget(e_inp); lay.addLayout(e_vb)

        return lay

    # ══════════════════════════════════════════════════════════
    # 실시간 계산
    # ══════════════════════════════════════════════════════════
    def _recalc_exp(self, attr_amt: str, attr_rate: str, attr_exp: str) -> None:
        """투자금 × 수익률% → 기대수익 자동 계산 + 합산 갱신"""
        amt  = getattr(self, attr_amt).text()
        rate = getattr(self, attr_rate).text()
        exp  = _calc_exp(amt, rate)
        exp_field = getattr(self, attr_exp)
        if exp > 0:
            exp_field.setStyleSheet(_INPUT_PIVOT_CALC
                                    if "pivot" in attr_exp else _INPUT_CALC)
            exp_field.setText(_fmt_exp(exp))
        elif exp < 0:
            exp_field.setStyleSheet(_INPUT_CALC_NEG)
            exp_field.setText(_fmt_exp(exp))
        else:
            exp_field.setStyleSheet(_INPUT_CALC_ZER)
            exp_field.setText("")
        self._update_total()

    def _update_total(self) -> None:
        total = sum(
            _parse_float(getattr(self, a).text())
            for a in ("_f_exp_stk", "_f_exp_opt", "_f_exp_fut", "_f_exp_pivot")
        )
        if total > 0:
            self._f_total.setStyleSheet(_INPUT_CALC)
            self._f_total.setText(f"+{total:,.0f}")
        elif total < 0:
            self._f_total.setStyleSheet(_INPUT_CALC_NEG)
            self._f_total.setText(f"{total:,.0f}")
        else:
            self._f_total.setStyleSheet(_INPUT_CALC_ZER)
            self._f_total.setText("")

    def _update_mnq_hint(self) -> None:
        mnq = getattr(self, "_mnq_price", 0.0)
        margin = _parse_float(self._f_amt_fut.text())
        if margin <= 0:
            self._lbl_mnq_hint.setText(
                f"💡 MNQ $2/pt · 1계약≈${_MNQ_MARGIN:,} 증거금")
            return
        contracts = margin / _MNQ_MARGIN
        ppl = contracts * _MNQ_MULT
        if mnq > 0:
            one_pct = mnq * 0.01 * ppl
            self._lbl_mnq_hint.setText(
                f"💡 {contracts:.1f}계약 · 1%이동({mnq*0.01:.0f}pt) → ${one_pct:+,.0f}")
        else:
            self._lbl_mnq_hint.setText(f"💡 {contracts:.1f}계약 · ${ppl:.1f}/pt")

    # ══════════════════════════════════════════════════════════
    # 주가 + MNQ 조회 (비동기)
    # ══════════════════════════════════════════════════════════
    def _fetch_stock_price(self) -> None:
        if self._sel_date is None:
            return
        raw = self._f_stock.text().strip()
        symbols = []
        if raw:
            symbols = [
                re.sub(r"[^A-Za-z]", "", s).upper()
                for s in re.split(r"[,/ │|]+", raw)
            ]
            symbols = [s for s in symbols if s and s not in _FIXED_SYMBOLS]

        self._btn_fetch.setEnabled(False)
        self._btn_fetch.setText("조회중…")
        self._lbl_mnq_hint.setText("MNQ 조회 중…")

        self._fetch_thread = _PriceFetchThread(symbols, self._sel_date, self)
        self._fetch_thread.stock_done.connect(
            self._on_price_fetched, Qt.ConnectionType.QueuedConnection)
        self._fetch_thread.mnq_done.connect(
            self._on_mnq_fetched, Qt.ConnectionType.QueuedConnection)
        self._fetch_thread.error.connect(
            self._on_price_error, Qt.ConnectionType.QueuedConnection)
        self._fetch_thread.finished.connect(
            self._on_fetch_finished, Qt.ConnectionType.QueuedConnection)
        self._fetch_thread.start()

    @pyqtSlot(str)
    def _on_price_fetched(self, text: str) -> None:
        self._f_stock.setText(text)

    @pyqtSlot(str, float)
    def _on_mnq_fetched(self, text: str, mnq_val: float) -> None:
        self._f_futures.setText(text)
        self._mnq_price = mnq_val
        self._update_mnq_hint()

    @pyqtSlot()
    def _on_fetch_finished(self) -> None:
        self._btn_fetch.setEnabled(True)
        self._btn_fetch.setText("📈 조회")

    @pyqtSlot(str)
    def _on_price_error(self, err: str) -> None:
        log.warning("[Journal] 주가 조회 실패: %s", err)
        self._lbl_mnq_hint.setText(f"조회 실패: {err}")
        self._btn_fetch.setEnabled(True)
        self._btn_fetch.setText("📈 조회")

    # ══════════════════════════════════════════════════════════
    # 데이터 로드/저장
    # ══════════════════════════════════════════════════════════
    def _load_journal(self) -> None:
        try:
            if _JOURNAL_PATH.exists():
                self._journal = json.loads(
                    _JOURNAL_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("[Journal] 로드 실패: %s", e)

    def _save_journal(self) -> None:
        try:
            _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            _JOURNAL_PATH.write_text(
                json.dumps(self._journal, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:
            log.error("[Journal] 저장 실패: %s", e)

    def _load_tradier_cal(self) -> None:
        try:
            if _TRADIER_CAL.exists():
                raw = json.loads(_TRADIER_CAL.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    if isinstance(v, dict):
                        self._cal_data[k] = (
                            v.get("memo") or v.get("text") or
                            next(iter(v.values()), "") or "")
                    elif isinstance(v, str):
                        self._cal_data[k] = v
        except Exception as e:
            log.warning("[Journal] Tradier 캘린더 로드 실패: %s", e)

    def _entry_exp(self, entry: dict) -> dict[str, float]:
        """엔트리에서 기대수익 4개 + 합산 계산 (표시용)"""
        exp_stk   = _calc_exp(entry.get("amt_stk",   ""),
                               entry.get("rate_stk",  ""))
        exp_opt   = _calc_exp(entry.get("amt_opt",   ""),
                               entry.get("rate_opt",  ""))
        exp_fut   = _calc_exp(entry.get("amt_fut",   ""),
                               entry.get("rate_fut",  ""))
        exp_pivot = _calc_exp(entry.get("amt_pivot", ""),
                               entry.get("rate_pivot",""))
        return {
            "stk":   exp_stk,
            "opt":   exp_opt,
            "fut":   exp_fut,
            "pivot": exp_pivot,
            "total": exp_stk + exp_opt + exp_fut + exp_pivot,
        }

    # ══════════════════════════════════════════════════════════
    # 테이블 렌더링
    # ══════════════════════════════════════════════════════════
    def _refresh_table(self) -> None:
        days  = _trading_days(self._view_start, 5 * self._view_weeks)
        today = date.today()
        self._table.setRowCount(len(days))
        self._row_dates = days

        for r, d in enumerate(days):
            key      = d.isoformat()
            entry    = self._journal.get(key, {})
            is_today = (d == today)
            exps     = self._entry_exp(entry)

            bg = QColor(_BG_TODAY if is_today else
                        (_BG_ROW if r % 2 == 0 else _BG_ALT))

            opt_val    = entry.get("option", "")
            is_opt_chk = (opt_val == "옵션 분기 타진")

            values = [
                d.strftime("%m/%d"),
                _DOW_KR[d.weekday()],
                entry.get("econ",    self._cal_data.get(key, "")),
                entry.get("stock",   ""),
                opt_val,
                entry.get("futures", ""),
                _fmt_exp(exps["stk"])   or "",
                _fmt_exp(exps["opt"])   or "",
                _fmt_exp(exps["fut"])   or "",
                _fmt_exp(exps["pivot"]) or "",
                _fmt_exp(exps["total"]) or "",
                entry.get("memo",    ""),
            ]

            for c, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setBackground(bg)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter |
                    (Qt.AlignmentFlag.AlignCenter if c < 2
                     else Qt.AlignmentFlag.AlignLeft))

                if c == _COL_OPT and is_opt_chk:
                    item.setForeground(QColor(_OPT_CHECK))
                    f = item.font(); f.setBold(True); item.setFont(f)
                elif c in (6, 7, 8, 9, _COL_TOTAL):
                    txt = str(val)
                    if txt.startswith("+"):
                        item.setForeground(QColor(_UP))
                    elif txt.startswith("-"):
                        item.setForeground(QColor(_DOWN))
                    else:
                        item.setForeground(QColor(_DIM))
                    if c == _COL_TOTAL:
                        f = item.font(); f.setBold(True); item.setFont(f)
                else:
                    item.setForeground(QColor(_TEXT))

                if is_today and c == 0:
                    f = item.font(); f.setBold(True); item.setFont(f)
                self._table.setItem(r, c, item)

            self._table.setRowHeight(r, 26)

    # ══════════════════════════════════════════════════════════
    # 행 선택 / 편집 폼 채우기
    # ══════════════════════════════════════════════════════════
    def _on_row_clicked(self, row: int, _col: int) -> None:
        if row < len(self._row_dates):
            self._select_date(self._row_dates[row])

    def _select_date(self, d: date) -> None:
        self._sel_date = d
        key   = d.isoformat()
        entry = self._journal.get(key, {})

        self._form_title.setText(
            f"✏️  {d.strftime('%Y-%m-%d')} ({_DOW_KR[d.weekday()]}) 편집")

        self._f_econ.setText(entry.get("econ", self._cal_data.get(key, "")))
        self._f_stock.setText(entry.get("stock",   ""))
        self._f_option.setText(entry.get("option",  ""))
        self._f_futures.setText(entry.get("futures", ""))
        self._f_amt_stk.setText(entry.get("amt_stk",   ""))
        self._f_rate_stk.setText(entry.get("rate_stk",  ""))
        self._f_amt_opt.setText(entry.get("amt_opt",   ""))
        self._f_rate_opt.setText(entry.get("rate_opt",  ""))
        self._f_amt_fut.setText(entry.get("amt_fut",   ""))
        self._f_rate_fut.setText(entry.get("rate_fut",  ""))
        self._f_amt_pivot.setText(entry.get("amt_pivot", ""))
        self._f_rate_pivot.setText(entry.get("rate_pivot",""))
        self._f_memo.setText(entry.get("memo",    ""))
        # 기대수익/합산은 textChanged 시그널로 자동 갱신됨

        if hasattr(self, "_row_dates"):
            for r, rd in enumerate(self._row_dates):
                if rd == d:
                    self._table.selectRow(r); break

    @pyqtSlot()
    def _on_save(self) -> None:
        if self._sel_date is None:
            return
        entry = {
            "econ":       self._f_econ.text().strip(),
            "stock":      self._f_stock.text().strip(),
            "option":     self._f_option.text().strip(),
            "futures":    self._f_futures.text().strip(),
            "amt_stk":    self._f_amt_stk.text().strip(),
            "rate_stk":   self._f_rate_stk.text().strip(),
            "amt_opt":    self._f_amt_opt.text().strip(),
            "rate_opt":   self._f_rate_opt.text().strip(),
            "amt_fut":    self._f_amt_fut.text().strip(),
            "rate_fut":   self._f_rate_fut.text().strip(),
            "amt_pivot":  self._f_amt_pivot.text().strip(),
            "rate_pivot": self._f_rate_pivot.text().strip(),
            "memo":       self._f_memo.text().strip(),
        }
        self._journal[self._sel_date.isoformat()] = entry

        # D+N 옵션 분기 자동 기록
        for n_str in re.findall(r"[Dd]\+(\d+)", entry["option"]):
            n = int(n_str)
            if 1 <= n <= 30:
                target   = _nth_trading_day(self._sel_date, n)
                t_key    = target.isoformat()
                t_entry  = self._journal.get(t_key, {})
                existing = t_entry.get("option", "")
                if "옵션 분기 타진" not in existing:
                    t_entry["option"] = (
                        (existing + " / 옵션 분기 타진").strip(" /")
                        if existing else "옵션 분기 타진")
                    self._journal[t_key] = t_entry
                log.info("[Journal] D+%d → %s 옵션 분기 타진 기록",
                         n, target.isoformat())

        self._save_journal()
        self._refresh_table()
        self._select_date(self._sel_date)

    @pyqtSlot()
    def _on_cancel(self) -> None:
        if self._sel_date:
            self._select_date(self._sel_date)

    # ══════════════════════════════════════════════════════════
    # 뷰 이동
    # ══════════════════════════════════════════════════════════
    def _set_weeks(self, n: int) -> None:
        self._view_weeks = n
        self._btn_1w.setChecked(n == 1)
        self._btn_2w.setChecked(n == 2)
        self._btn_3w.setChecked(n == 3)
        self._btn_4w.setChecked(n == 4)
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

    def register_shortcuts(self) -> list:
        return super().register_shortcuts() + [("Ctrl+S", self._on_save)]
