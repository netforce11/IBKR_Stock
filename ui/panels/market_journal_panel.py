# ui/panels/market_journal_panel.py
# 시장 추적 관리 일지 패널
#
# 기능:
#   - 1주 / 2주 단위 테이블 뷰 (날짜=행, 항목=열)
#   - 행 클릭 → 하단 편집 폼 로드
#   - 경제지표: Tradier_Working 캘린더 JSON 자동 로드
#   - 주식분기점: 종목명 입력 후 📈 버튼 → 해당일 종가 자동조회 (yfinance)
#   - 기대수익 합산 손익: 3개 합 실시간 계산
#   - 옵션분기점 D+N: 저장 시 N 거래일 후 날짜에 "옵션 분기 타진" 자동 기록
#   - 데이터 저장: ~/trading_terminal/ibkr_stock/data/market_journal.json

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
    QSplitter,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
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
_OPT_CHECK = "#7C3AED"   # 옵션 분기 타진 색상 (보라)

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
_INPUT_TOTAL_POS = (
    "QLineEdit { background:#EFF6FF; color:#1565C0; font-weight:bold;"
    " border:1px solid #93C5FD; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
)
_INPUT_TOTAL_NEG = (
    "QLineEdit { background:#FEF2F2; color:#C62828; font-weight:bold;"
    " border:1px solid #FCA5A5; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
)
_INPUT_TOTAL_ZER = (
    "QLineEdit { background:#F9FAFB; color:#555E6B; font-weight:bold;"
    " border:1px solid #D0D7DE; border-radius:3px;"
    " padding:2px 6px; font-size:11px; }"
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
    ("합산손익\n($)", 80),
    ("메모",    160),
]
_COL_OPT   = 4    # 옵션분기점 컬럼 인덱스
_COL_TOTAL = 10   # 합산손익 컬럼 인덱스
_DOW_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _trading_days(start: date, n: int) -> list[date]:
    """start 날짜부터 n 거래일(월~금) 반환"""
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _nth_trading_day(from_date: date, n: int) -> date:
    """from_date 로부터 n 거래일 후 날짜"""
    count, d = 0, from_date
    while count < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d


def _week_start(d: date) -> date:
    """해당 날의 월요일 반환"""
    return d - timedelta(days=d.weekday())


# ── 비동기 주가 조회 스레드 ───────────────────────────────────
class _PriceFetchThread(QThread):
    """yfinance 로 해당일 종가 조회 (메인 스레드 블로킹 방지)"""
    done  = pyqtSignal(str)   # "TQQQ 123.45 / SQQQ 45.67" 형태
    error = pyqtSignal(str)

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
        try:
            from datetime import datetime as _dt
            start = self._date
            end   = self._date + timedelta(days=4)   # 주말/공휴일 대비 여유
            parts = []
            for sym in self._symbols:
                ticker = yf.Ticker(sym)
                hist   = ticker.history(start=start.isoformat(),
                                        end=end.isoformat(),
                                        auto_adjust=True)
                if hist.empty:
                    parts.append(f"{sym} N/A")
                    continue
                # start 날짜 이후 첫 행 사용
                row = hist.iloc[0]
                close = float(row["Close"])
                parts.append(f"{sym} {close:.2f}")
            self.done.emit(" / ".join(parts))
        except Exception as e:
            self.error.emit(str(e))


class MarketJournalPanel(BasePanel):
    PANEL_ID     = "market_journal"
    TITLE        = "시장 일지"
    DEFAULT_SIZE = (1140, 640)

    def __init__(self, bus: DataBus, parent=None) -> None:
        self._journal: dict = {}        # {"2026-10-19": {...}}
        self._cal_data: dict = {}       # Tradier 캘린더
        self._view_weeks = 1            # 1 or 2
        self._sel_date: date | None = None
        self._fetch_thread = None       # 주가 조회 스레드
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

        self._btn_prev = QPushButton("◀ 이전")
        self._btn_next = QPushButton("다음 ▶")
        self._btn_prev.setStyleSheet(_BTN)
        self._btn_next.setStyleSheet(_BTN)
        self._btn_prev.setFixedWidth(64)
        self._btn_next.setFixedWidth(64)
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_next.clicked.connect(self._go_next)

        self._btn_1w = QPushButton("1주")
        self._btn_2w = QPushButton("2주")
        for b in (self._btn_1w, self._btn_2w):
            b.setCheckable(True)
            b.setFixedWidth(48)
            b.setStyleSheet(_BTN)
        self._btn_1w.setChecked(True)
        self._btn_1w.clicked.connect(lambda: self._set_weeks(1))
        self._btn_2w.clicked.connect(lambda: self._set_weeks(2))

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
        # 메모 열 자동 확장 (인덱스 11)
        self._table.horizontalHeader().setSectionResizeMode(
            11, QHeaderView.ResizeMode.Stretch)
        self._table.cellClicked.connect(self._on_row_clicked)
        splitter.addWidget(self._table)

        splitter.addWidget(self._build_edit_form())
        splitter.setSizes([380, 260])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        self._layout.addWidget(splitter, 1)
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

        # ── 행 1: 경제지표 / 주식분기점(+조회버튼) / 옵션분기점 / 선물분기점 ──
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        def _field(label: str, width: int | None = None) -> tuple:
            vb  = QVBoxLayout()
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

        # 주식분기점 + 📈 조회 버튼
        stk_vb = QVBoxLayout()
        stk_vb.setSpacing(2)
        stk_lbl = QLabel("주식분기점 — 종목 입력 후 📈 조회")
        stk_lbl.setStyleSheet(_LABEL)
        stk_inner = QHBoxLayout()
        stk_inner.setSpacing(4)
        self._f_stock = QLineEdit()
        self._f_stock.setStyleSheet(_INPUT)
        self._f_stock.setPlaceholderText("TQQQ,SQQQ  또는 직접 입력")
        self._f_stock.setFixedWidth(170)
        self._btn_fetch = QPushButton("📈 조회")
        self._btn_fetch.setStyleSheet(_BTN_FETCH)
        self._btn_fetch.setFixedWidth(60)
        self._btn_fetch.setToolTip(
            "종목명을 콤마로 구분 입력 후 클릭\n"
            "예: TQQQ,SQQQ\n"
            "해당 날짜의 종가를 자동으로 가져옵니다."
        )
        self._btn_fetch.clicked.connect(self._fetch_stock_price)
        stk_inner.addWidget(self._f_stock)
        stk_inner.addWidget(self._btn_fetch)
        stk_vb.addWidget(stk_lbl)
        stk_vb.addLayout(stk_inner)
        row1.addLayout(stk_vb)

        # 옵션분기점 (D+N 힌트 포함)
        opt_vb = QVBoxLayout()
        opt_vb.setSpacing(2)
        opt_lbl = QLabel("옵션분기점 (D+1~D+5 입력 → 해당일 자동 기록)")
        opt_lbl.setStyleSheet(_LABEL)
        self._f_option = QLineEdit()
        self._f_option.setStyleSheet(_INPUT)
        self._f_option.setPlaceholderText("예: D+2  또는 직접 입력")
        self._f_option.setFixedWidth(180)
        opt_vb.addWidget(opt_lbl)
        opt_vb.addWidget(self._f_option)
        row1.addLayout(opt_vb)

        vb, self._f_futures = _field("선물분기점 MNQ (상단/하단)", 150)
        row1.addLayout(vb)
        root.addLayout(row1)

        # ── 행 2: 증거금 / 기대수익 3개 / 합산손익 / 메모 ────
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        vb, self._f_margin   = _field("증거금 ($)", 80)
        row2.addLayout(vb)
        vb, self._f_exp_stk  = _field("기대수익 주식 ($)", 90)
        row2.addLayout(vb)
        vb, self._f_exp_opt  = _field("기대수익 옵션 ($)", 90)
        row2.addLayout(vb)
        vb, self._f_exp_fut  = _field("기대수익 선물 ($)", 90)
        row2.addLayout(vb)

        # 합산 손익 (읽기 전용)
        tot_vb = QVBoxLayout()
        tot_vb.setSpacing(2)
        tot_lbl = QLabel("합산 손익 ($)")
        tot_lbl.setStyleSheet("QLabel { color:#1565C0; font-size:11px; font-weight:bold; }")
        self._f_total = QLineEdit()
        self._f_total.setReadOnly(True)
        self._f_total.setStyleSheet(_INPUT_TOTAL_ZER)
        self._f_total.setFixedWidth(90)
        self._f_total.setAlignment(Qt.AlignmentFlag.AlignRight)
        tot_vb.addWidget(tot_lbl)
        tot_vb.addWidget(self._f_total)
        row2.addLayout(tot_vb)

        # 메모
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

        # 합산 손익 실시간 계산 연결
        for f in (self._f_exp_stk, self._f_exp_opt, self._f_exp_fut):
            f.textChanged.connect(self._update_total)

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
    # 주가 조회 (비동기)
    # ══════════════════════════════════════════════════════════
    def _fetch_stock_price(self) -> None:
        """📈 버튼 클릭 → 종목명 파싱 → yfinance 비동기 조회"""
        if self._sel_date is None:
            return
        raw = self._f_stock.text().strip()
        if not raw:
            self._f_stock.setPlaceholderText("종목명을 입력하세요 (예: TQQQ,SQQQ)")
            return

        # 콤마/슬래시/공백으로 분리, 가격 숫자 제거 → 순수 종목코드만
        symbols = [
            re.sub(r"[^A-Za-z]", "", s)
            for s in re.split(r"[,/ ]+", raw)
        ]
        symbols = [s.upper() for s in symbols if s]
        if not symbols:
            return

        self._btn_fetch.setEnabled(False)
        self._btn_fetch.setText("조회중…")

        self._fetch_thread = _PriceFetchThread(symbols, self._sel_date, self)
        self._fetch_thread.done.connect(
            self._on_price_fetched, Qt.ConnectionType.QueuedConnection)
        self._fetch_thread.error.connect(
            self._on_price_error, Qt.ConnectionType.QueuedConnection)
        self._fetch_thread.start()

    @pyqtSlot(str)
    def _on_price_fetched(self, text: str) -> None:
        self._f_stock.setText(text)
        self._btn_fetch.setEnabled(True)
        self._btn_fetch.setText("📈 조회")

    @pyqtSlot(str)
    def _on_price_error(self, err: str) -> None:
        log.warning("[Journal] 주가 조회 실패: %s", err)
        self._f_stock.setPlaceholderText(f"조회 실패: {err}")
        self._btn_fetch.setEnabled(True)
        self._btn_fetch.setText("📈 조회")

    # ══════════════════════════════════════════════════════════
    # 합산 손익 실시간 계산
    # ══════════════════════════════════════════════════════════
    def _update_total(self) -> None:
        total = 0.0
        for f in (self._f_exp_stk, self._f_exp_opt, self._f_exp_fut):
            try:
                val = f.text().replace(",", "").replace("$", "").strip()
                total += float(val) if val else 0.0
            except ValueError:
                pass
        if total > 0:
            self._f_total.setStyleSheet(_INPUT_TOTAL_POS)
            self._f_total.setText(f"+{total:,.2f}")
        elif total < 0:
            self._f_total.setStyleSheet(_INPUT_TOTAL_NEG)
            self._f_total.setText(f"{total:,.2f}")
        else:
            self._f_total.setStyleSheet(_INPUT_TOTAL_ZER)
            self._f_total.setText("")

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
                for k, v in raw.items():
                    if isinstance(v, dict):
                        self._cal_data[k] = (
                            v.get("memo") or v.get("text") or
                            next(iter(v.values()), "") or ""
                        )
                    elif isinstance(v, str):
                        self._cal_data[k] = v
        except Exception as e:
            log.warning("[Journal] Tradier 캘린더 로드 실패: %s", e)

    def _get_entry(self, d: date) -> dict:
        return self._journal.get(d.isoformat(), {})

    def _set_entry(self, d: date, entry: dict) -> None:
        self._journal[d.isoformat()] = entry

    def _calc_total_str(self, entry: dict) -> str:
        """엔트리에서 합산손익 문자열 계산"""
        total = 0.0
        for k in ("exp_stk", "exp_opt", "exp_fut"):
            try:
                v = entry.get(k, "").replace(",", "").replace("$", "").strip()
                total += float(v) if v else 0.0
            except ValueError:
                pass
        if total > 0:
            return f"+{total:,.2f}"
        elif total < 0:
            return f"{total:,.2f}"
        return ""

    # ══════════════════════════════════════════════════════════
    # 테이블 렌더링
    # ══════════════════════════════════════════════════════════
    def _refresh_table(self) -> None:
        n_days = 5 * self._view_weeks
        days   = _trading_days(self._view_start, n_days)
        today  = date.today()

        self._table.setRowCount(len(days))
        self._row_dates = days

        for r, d in enumerate(days):
            key      = d.isoformat()
            entry    = self._journal.get(key, {})
            dow      = _DOW_KR[d.weekday()]
            is_today = (d == today)

            if is_today:
                bg = QColor(_BG_TODAY)
            elif r % 2 == 0:
                bg = QColor(_BG_ROW)
            else:
                bg = QColor(_BG_ALT)

            opt_val   = entry.get("option", "")
            is_opt_chk = (opt_val == "옵션 분기 타진")

            values = [
                d.strftime("%m/%d"),
                dow,
                entry.get("econ",    self._cal_data.get(key, "")),
                entry.get("stock",   ""),
                opt_val,
                entry.get("futures", ""),
                entry.get("margin",  ""),
                entry.get("exp_stk", ""),
                entry.get("exp_opt", ""),
                entry.get("exp_fut", ""),
                self._calc_total_str(entry),
                entry.get("memo",    ""),
            ]

            for c, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setBackground(bg)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter |
                    (Qt.AlignmentFlag.AlignCenter if c < 2
                     else Qt.AlignmentFlag.AlignLeft)
                )

                # 옵션 분기 타진 강조 (보라색)
                if c == _COL_OPT and is_opt_chk:
                    item.setForeground(QColor(_OPT_CHECK))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                # 합산 손익 색상
                elif c == _COL_TOTAL:
                    txt = str(val)
                    if txt.startswith("+"):
                        item.setForeground(QColor(_UP))
                    elif txt.startswith("-"):
                        item.setForeground(QColor(_DOWN))
                    else:
                        item.setForeground(QColor(_DIM))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                else:
                    item.setForeground(QColor(_TEXT))

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
        # 합산손익은 textChanged 시그널로 자동 업데이트됨

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

        # ── D+N 옵션 분기 자동 기록 ───────────────────────
        # "D+1", "D+2" ... "D+5" 패턴 감지 → 해당 거래일에 "옵션 분기 타진" 기록
        opt_text = entry["option"]
        matches  = re.findall(r"[Dd]\+(\d+)", opt_text)
        for n_str in matches:
            n = int(n_str)
            if 1 <= n <= 30:   # 합리적 범위만 처리
                target = _nth_trading_day(self._sel_date, n)
                t_key  = target.isoformat()
                t_entry = self._journal.get(t_key, {})
                # 이미 다른 내용이 있으면 덮어쓰지 않고 추가
                existing_opt = t_entry.get("option", "")
                if "옵션 분기 타진" not in existing_opt:
                    t_entry["option"] = (
                        (existing_opt + " / 옵션 분기 타진").strip(" /")
                        if existing_opt else "옵션 분기 타진"
                    )
                    self._journal[t_key] = t_entry
                log.info(
                    "[Journal] D+%d → %s 옵션 분기 타진 기록",
                    n, target.isoformat()
                )

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
