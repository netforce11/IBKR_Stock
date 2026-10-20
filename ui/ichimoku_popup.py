# ui/ichimoku_popup.py
# 일목균형표 팝업 차트 — 일봉 기준 (matplotlib embed)
#
# 5선 구성:
#   전환선  (Tenkan)  빨강    9일 중심
#   기준선  (Kijun)   파랑   26일 중심
#   선행스팬A (SpanA)  초록   구름 위쪽 경계
#   선행스팬B (SpanB)  주황   구름 아래쪽 경계
#   후행스팬 (Chikou)  보라   현재가 26봉 뒤
#
# 구름(구모): SpanA > SpanB → 녹색 구름
#             SpanA < SpanB → 적색 구름

from __future__ import annotations
import random
import logging
from datetime import date, timedelta

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui  import QFont

log = logging.getLogger(__name__)

# ── 색상 ─────────────────────────────────────────────────
_BG       = "#0F172A"
_BG_FLOAT = (0.059, 0.090, 0.165)   # matplotlib용 (0~1)
_GRID     = (0.118, 0.161, 0.239)
_TENKAN   = "#F87171"
_KIJUN    = "#60A5FA"
_SPANA    = "#34D399"
_SPANB    = "#FB923C"
_CHIKOU   = "#C084FC"
_UP_C     = "#26A69A"
_DN_C     = "#EF5350"


def _gen_mock_bars(symbol: str, n: int = 150,
                   seed_price: float = 200.0) -> list[tuple]:
    """가짜 일봉 OHLC 생성. 반환: [(date_str, open, high, low, close), ...]"""
    random.seed(hash(symbol) % 9999)
    price = seed_price
    bars  = []
    today = date.today()

    for i in range(n, 0, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        o = price
        price += random.gauss(0, price * 0.012)
        price = max(price, 1.0)
        c = round(price, 2)
        h = round(max(o, c) * random.uniform(1.000, 1.015), 2)
        l = round(min(o, c) * random.uniform(0.985, 1.000), 2)
        bars.append((d.strftime("%m/%d"), round(o, 2), h, l, c))
    return bars


def _calc_ichimoku(bars: list[tuple]) -> dict:
    """bars = [(date, o, h, l, c)] → 각 선의 시리즈 반환"""
    from shared.calc.indicators import Ichimoku

    n      = len(bars)
    tenkan = [None] * n
    kijun  = [None] * n
    spanA  = [None] * n
    spanB  = [None] * n
    chikou = [None] * n

    ichi = Ichimoku()
    for i, (_, o, h, l, c) in enumerate(bars):
        r = ichi.update((h, l, c))
        if r:
            tenkan[i] = r["tenkan"]
            kijun[i]  = r["kijun"]
            fi = i + 26
            if fi < n:
                spanA[fi] = r["spanA"]
                spanB[fi] = r["spanB"]
        bi = i - 26
        if bi >= 0:
            chikou[bi] = c

    return {"tenkan": tenkan, "kijun": kijun,
            "spanA": spanA,  "spanB": spanB, "chikou": chikou}


class IchimokuPopup(QDialog):
    """일목균형표 팝업 다이얼로그 (matplotlib embed)"""

    PERIODS = {"3개월 (60일)": 60, "6개월 (120일)": 120, "1년 (250일)": 250}

    def __init__(self, symbol: str, current_price: float = 200.0, parent=None):
        super().__init__(parent)
        self._symbol = symbol.upper()
        self._price  = current_price
        self.setWindowTitle(f"일목균형표 — {self._symbol}")
        self.setMinimumSize(920, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(f"background:{_BG}; color:#F1F5F9;")
        self._canvas = None
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # ── 상단 툴바 ─────────────────────────────────────
        bar = QHBoxLayout()

        title = QLabel(f"📊  {self._symbol}  일목균형표")
        title.setStyleSheet("font-size:14px; font-weight:bold; color:#F1F5F9;")
        bar.addWidget(title)
        bar.addStretch()

        period_lbl = QLabel("기간:")
        period_lbl.setStyleSheet("color:#94A3B8; font-size:12px;")
        bar.addWidget(period_lbl)

        self._period_cb = QComboBox()
        self._period_cb.addItems(list(self.PERIODS.keys()))
        self._period_cb.setCurrentIndex(1)
        self._period_cb.setStyleSheet(
            "QComboBox { background:#1E293B; color:#F1F5F9; border:1px solid #334155;"
            " border-radius:3px; padding:3px 8px; font-size:12px; min-width:130px; }"
            "QComboBox::drop-down { border:none; }"
            "QComboBox QAbstractItemView { background:#1E293B; color:#F1F5F9; }")
        self._period_cb.currentIndexChanged.connect(self._refresh)
        bar.addWidget(self._period_cb)

        close_btn = QPushButton("✕ 닫기")
        close_btn.setStyleSheet(
            "QPushButton { background:#334155; color:#F1F5F9; border:none;"
            " border-radius:3px; padding:4px 12px; font-size:12px; }"
            "QPushButton:hover { background:#475569; }")
        close_btn.clicked.connect(self.close)
        bar.addWidget(close_btn)
        lay.addLayout(bar)

        # ── 범례 ─────────────────────────────────────────
        legend = QHBoxLayout()
        legend.setSpacing(20)
        for label, color in [
            ("전환선 (9)", _TENKAN), ("기준선 (26)", _KIJUN),
            ("선행A", _SPANA), ("선행B", _SPANB), ("후행스팬", _CHIKOU),
        ]:
            lbl = QLabel(f"━  {label}")
            lbl.setStyleSheet(f"color:{color}; font-size:11px; font-weight:bold;")
            legend.addWidget(lbl)
        legend.addStretch()
        lay.addLayout(legend)

        # ── 차트 컨테이너 ─────────────────────────────────
        self._chart_container = QWidget()
        self._chart_container.setStyleSheet(f"background:{_BG};")
        self._chart_lay = QVBoxLayout(self._chart_container)
        self._chart_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._chart_container, 1)

        self._refresh()

    def _refresh(self) -> None:
        period_key = self._period_cb.currentText()
        n = self.PERIODS[period_key]

        # 기존 캔버스 제거
        if self._canvas is not None:
            self._chart_lay.removeWidget(self._canvas)
            self._canvas.deleteLater()
            self._canvas = None

        try:
            self._build_chart(n)
        except Exception as e:
            log.exception("일목균형표 차트 렌더링 오류: %s", e)
            err = QLabel(f"차트 오류: {e}")
            err.setStyleSheet("color:#EF4444; font-size:12px; padding:20px;")
            self._chart_lay.addWidget(err)

    def _build_chart(self, n: int) -> None:
        import matplotlib
        matplotlib.use("QtAgg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        import numpy as np

        bars = _gen_mock_bars(self._symbol, n=n + 60, seed_price=self._price)
        bars = bars[-n:]
        ichi = _calc_ichimoku(bars)
        xs   = list(range(len(bars)))

        fig, (ax_main, ax_candle) = plt.subplots(
            2, 1, figsize=(12, 7), gridspec_kw={"height_ratios": [3, 1]},
            facecolor=_BG_FLOAT)
        fig.subplots_adjust(hspace=0.05, left=0.06, right=0.98,
                            top=0.97, bottom=0.08)

        # ── 공통 스타일 적용 ──────────────────────────────
        for ax in (ax_main, ax_candle):
            ax.set_facecolor(_BG_FLOAT)
            ax.tick_params(colors="#64748B", labelsize=8)
            ax.spines[:].set_color(_GRID)
            for spine in ax.spines.values():
                spine.set_color((0.118, 0.161, 0.239))
            ax.grid(True, color=_GRID, alpha=0.4, linewidth=0.5)
            ax.yaxis.tick_right()

        # ── 캔들스틱 (ax_candle) ─────────────────────────
        for i, (_, o, h, l, c) in enumerate(bars):
            color = _UP_C if c >= o else _DN_C
            # 꼬리
            ax_candle.plot([i, i], [l, h], color=color, linewidth=0.8)
            # 몸통
            body_bot = min(o, c)
            body_h   = max(abs(c - o), 0.01)
            rect = mpatches.Rectangle(
                (i - 0.3, body_bot), 0.6, body_h,
                facecolor=color, edgecolor=color, linewidth=0)
            ax_candle.add_patch(rect)
        ax_candle.set_xlim(-1, len(bars))
        ax_candle.set_ylabel("가격", color="#64748B", fontsize=8)

        # ── 일목 5선 + 구름 (ax_main) ────────────────────
        def plot_line(key, color, lw=1.5, label=""):
            pts = [(i, v) for i, v in enumerate(ichi[key]) if v is not None]
            if len(pts) < 2:
                return
            xi, yi = zip(*pts)
            ax_main.plot(xi, yi, color=color, linewidth=lw,
                         label=label, alpha=0.9)

        plot_line("tenkan", _TENKAN, 1.4)
        plot_line("kijun",  _KIJUN,  1.8)
        plot_line("chikou", _CHIKOU, 1.2)

        # 구름 채우기
        sa_xs = [i for i in xs if ichi["spanA"][i] is not None
                 and ichi["spanB"][i] is not None]
        if sa_xs:
            sa_ys = np.array([ichi["spanA"][i] for i in sa_xs])
            sb_ys = np.array([ichi["spanB"][i] for i in sa_xs])
            ax_main.plot(sa_xs, sa_ys, color=_SPANA, linewidth=1.4, alpha=0.9)
            ax_main.plot(sa_xs, sb_ys, color=_SPANB, linewidth=1.4, alpha=0.9)
            ax_main.fill_between(sa_xs, sa_ys, sb_ys,
                                 where=(sa_ys >= sb_ys),
                                 facecolor="#22C55E", alpha=0.15, interpolate=True)
            ax_main.fill_between(sa_xs, sa_ys, sb_ys,
                                 where=(sa_ys < sb_ys),
                                 facecolor="#EF4444", alpha=0.15, interpolate=True)

        # 현재가 수평선
        ax_main.axhline(y=self._price, color="#FBBF24",
                        linewidth=1.0, linestyle="--", alpha=0.8)
        ax_main.text(len(bars) * 0.99, self._price,
                     f" ${self._price:,.2f}",
                     color="#FBBF24", fontsize=8, va="bottom", ha="right")

        ax_main.set_xlim(-1, len(bars))
        ax_main.set_ylabel("가격", color="#64748B", fontsize=8)

        # x축: 날짜 레이블 (10개 표시)
        step = max(1, len(bars) // 10)
        tick_xs  = list(range(0, len(bars), step))
        tick_lbl = [bars[i][0] for i in tick_xs]
        ax_main.set_xticks([])          # 상단 차트엔 x눈금 숨김
        ax_candle.set_xticks(tick_xs)
        ax_candle.set_xticklabels(tick_lbl, rotation=30,
                                  ha="right", fontsize=7, color="#64748B")

        # ── Canvas 삽입 ───────────────────────────────────
        canvas = FigureCanvasQTAgg(fig)
        canvas.setStyleSheet(f"background:{_BG};")
        self._canvas = canvas
        self._chart_lay.addWidget(canvas)
        plt.close(fig)
