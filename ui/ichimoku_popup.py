# ui/ichimoku_popup.py
# 일목균형표 팝업 차트 — 일봉 기준
#
# 5선 구성:
#   전환선  (Tenkan)  빨강    9일 중심
#   기준선  (Kijun)   파랑   26일 중심
#   선행스팬A (SpanA)  초록   구름 위쪽 경계
#   선행스팬B (SpanB)  주황   구름 아래쪽 경계
#   후행스팬 (Chikou)  보라   현재가 26봉 뒤
#
# 구름(구모): SpanA > SpanB → 녹색 구름 (상승)
#             SpanA < SpanB → 적색 구름 (하락)

from __future__ import annotations
import random
import math
import logging
from datetime import date, timedelta

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QWidget,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui  import QFont

log = logging.getLogger(__name__)

# ── 색상 ─────────────────────────────────────────────────
_BG       = "#0F172A"
_TENKAN   = "#F87171"   # 빨강 — 전환선
_KIJUN    = "#60A5FA"   # 파랑 — 기준선
_SPANA    = "#34D399"   # 녹색 — 선행스팬A
_SPANB    = "#FB923C"   # 주황 — 선행스팬B
_CHIKOU   = "#C084FC"   # 보라 — 후행스팬
_UP_CLOUD = "#1A3A2A"   # 상승 구름 배경
_DN_CLOUD = "#3A1A1A"   # 하락 구름 배경
_UP_CANDLE = "#26A69A"
_DN_CANDLE = "#EF5350"
_GRID      = "#1E293B"
_TEXT      = "#94A3B8"


def _gen_mock_bars(symbol: str, n: int = 120,
                   seed_price: float = 200.0) -> list[tuple]:
    """
    가짜 일봉 OHLC 생성.
    반환: [(date_str, open, high, low, close), ...]
    """
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

    n = len(bars)
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
            # 선행스팬은 26봉 앞에 배치 — 인덱스 범위 안에서만
            fi = i + 26
            if fi < n:
                spanA[fi] = r["spanA"]
                spanB[fi] = r["spanB"]
        # 후행스팬: 현재 종가를 26봉 뒤에
        bi = i - 26
        if bi >= 0:
            chikou[bi] = c

    return {
        "tenkan": tenkan, "kijun":  kijun,
        "spanA":  spanA,  "spanB":  spanB,
        "chikou": chikou,
    }


class IchimokuPopup(QDialog):
    """일목균형표 팝업 다이얼로그 (pyqtgraph 기반)"""

    PERIODS = {"3개월 (60일)": 60, "6개월 (120일)": 120, "1년 (250일)": 250}

    def __init__(self, symbol: str, current_price: float = 200.0, parent=None):
        super().__init__(parent)
        self._symbol = symbol.upper()
        self._price  = current_price
        self.setWindowTitle(f"일목균형표 — {self._symbol}")
        self.setMinimumSize(900, 580)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(f"background:{_BG}; color:#F1F5F9;")
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

        # 기간 선택
        period_lbl = QLabel("기간:")
        period_lbl.setStyleSheet("color:#94A3B8; font-size:12px;")
        bar.addWidget(period_lbl)

        self._period_cb = QComboBox()
        self._period_cb.addItems(list(self.PERIODS.keys()))
        self._period_cb.setCurrentIndex(1)   # 기본: 6개월
        self._period_cb.setStyleSheet(
            "QComboBox { background:#1E293B; color:#F1F5F9; border:1px solid #334155;"
            " border-radius:3px; padding:3px 8px; font-size:12px; min-width:120px; }"
            "QComboBox::drop-down { border:none; }"
            "QComboBox QAbstractItemView { background:#1E293B; color:#F1F5F9; }")
        self._period_cb.currentIndexChanged.connect(self._refresh)
        bar.addWidget(self._period_cb)

        # 닫기 버튼
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
        legend.setSpacing(16)
        for label, color in [
            ("전환선 (9)", _TENKAN), ("기준선 (26)", _KIJUN),
            ("선행A", _SPANA), ("선행B", _SPANB), ("후행스팬", _CHIKOU),
        ]:
            lbl = QLabel(f"━  {label}")
            lbl.setStyleSheet(
                f"color:{color}; font-size:11px; font-weight:bold;")
            legend.addWidget(lbl)
        legend.addStretch()
        lay.addLayout(legend)

        # ── 차트 영역 ─────────────────────────────────────
        self._chart_container = QWidget()
        self._chart_container.setStyleSheet(f"background:{_BG};")
        chart_lay = QVBoxLayout(self._chart_container)
        chart_lay.setContentsMargins(0, 0, 0, 0)
        self._chart_lay = chart_lay
        lay.addWidget(self._chart_container, 1)

        self._gfx = None
        self._refresh()

    def _refresh(self) -> None:
        """기간 선택에 따라 차트 재렌더링"""
        period_key = self._period_cb.currentText()
        n = self.PERIODS[period_key]

        # 기존 차트 제거
        if self._gfx is not None:
            self._chart_lay.removeWidget(self._gfx)
            self._gfx.deleteLater()
            self._gfx = None

        try:
            self._build_chart(n)
        except Exception as e:
            log.exception("일목균형표 차트 렌더링 오류")
            err = QLabel(f"차트 오류: {e}")
            err.setStyleSheet("color:#EF4444; font-size:12px;")
            self._chart_lay.addWidget(err)

    def _build_chart(self, n: int) -> None:
        import pyqtgraph as pg
        from pyqtgraph import PlotWidget, FillBetweenItem

        pg.setConfigOptions(antialias=True, useOpenGL=False)

        bars = _gen_mock_bars(self._symbol, n=n + 30, seed_price=self._price)
        bars = bars[-n:]   # 최근 n봉
        ichi = _calc_ichimoku(bars)
        xs   = list(range(len(bars)))

        # ── GraphicsLayoutWidget ──────────────────────────
        gfx = pg.GraphicsLayoutWidget()
        gfx.setBackground(_BG)
        self._gfx = gfx
        self._chart_lay.addWidget(gfx)

        # ── 캔들 + 일목선 플롯 ───────────────────────────
        p = gfx.addPlot(row=0, col=0)
        p.setMenuEnabled(False)
        p.showGrid(x=True, y=True, alpha=0.15)
        p.getAxis("bottom").setStyle(tickFont=QFont("monospace", 8))
        p.getAxis("left").setStyle(tickFont=QFont("monospace", 9))
        for ax in ("top", "right"):
            p.showAxis(ax, False)

        # x축 날짜 레이블
        date_labels = {i: bars[i][0] for i in range(0, len(bars), max(1, len(bars)//10))}
        p.getAxis("bottom").setTicks([list(date_labels.items())])

        # 캔들스틱 — pyqtgraph CandleStickItem (간단 버전)
        for i, (_, o, h, l, c) in enumerate(bars):
            color = _UP_CANDLE if c >= o else _DN_CANDLE
            pen   = pg.mkPen(color, width=1)
            # 몸통
            body_h = abs(c - o) or 0.01
            rect = pg.QtWidgets.QGraphicsRectItem(i - 0.3, min(o, c), 0.6, body_h)
            rect.setPen(pg.mkPen(color, width=0.5))
            rect.setBrush(pg.mkBrush(color))
            p.addItem(rect)
            # 꼬리
            p.plot([i, i], [l, min(o, c)], pen=pen)
            p.plot([i, i], [max(o, c), h], pen=pen)

        # ── 구름 채우기 (FillBetweenItem) ────────────────
        sa_xs, sa_ys, sb_ys = [], [], []
        for i in xs:
            if ichi["spanA"][i] is not None and ichi["spanB"][i] is not None:
                sa_xs.append(i)
                sa_ys.append(ichi["spanA"][i])
                sb_ys.append(ichi["spanB"][i])

        if sa_xs:
            # 상승/하락 구간별로 색 분리
            import numpy as np
            sa_arr = np.array(sa_ys)
            sb_arr = np.array(sb_ys)
            x_arr  = np.array(sa_xs, dtype=float)

            curve_a = p.plot(x_arr, sa_arr,
                             pen=pg.mkPen(_SPANA, width=1.5))
            curve_b = p.plot(x_arr, sb_arr,
                             pen=pg.mkPen(_SPANB, width=1.5))

            # 구름 채우기: 상승(A>B)=녹색, 하락(A<B)=적색
            fill_up = pg.FillBetweenItem(
                curve_a, curve_b,
                brush=pg.mkBrush(34, 197, 94, 40))    # 녹색 반투명
            fill_dn = pg.FillBetweenItem(
                curve_b, curve_a,
                brush=pg.mkBrush(239, 68, 68, 40))    # 적색 반투명
            p.addItem(fill_up)
            p.addItem(fill_dn)

        # ── 일목 5선 ─────────────────────────────────────
        def _plot_line(key: str, color: str, width: float = 1.5) -> None:
            pts = [(i, v) for i, v in enumerate(ichi[key]) if v is not None]
            if len(pts) < 2:
                return
            xi, yi = zip(*pts)
            p.plot(list(xi), list(yi),
                   pen=pg.mkPen(color, width=width))

        _plot_line("tenkan", _TENKAN, 1.5)
        _plot_line("kijun",  _KIJUN,  2.0)
        _plot_line("chikou", _CHIKOU, 1.2)

        # 현재가 수평선
        p.addItem(pg.InfiniteLine(
            pos=self._price, angle=0,
            pen=pg.mkPen("#FBBF24", width=1, style=Qt.PenStyle.DashLine),
            label=f" ${self._price:,.2f}",
            labelOpts={"color": "#FBBF24", "position": 0.02,
                       "fill": pg.mkBrush(_BG)}))

        p.setXRange(0, len(bars) - 1, padding=0.02)
