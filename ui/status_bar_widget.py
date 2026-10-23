"""
ui/status_bar_widget.py
───────────────────────
우측 하단 상태바 위젯 — IBKR 연결/구독/레이턴시/장구분 표시

레이아웃 (우→좌 순, addPermanentWidget):
  [ 🕐 장구분 ]  [ ⬤ Mock / IBKR ]  [ 📡 구독 0개 ]  [ ⚡ 0ms ]

토픽 구독:
  BROKER_CONNECTED    → 연결 표시등 녹색
  BROKER_DISCONNECTED → 연결 표시등 빨강
  stock.price.*       → 구독 카운터 + 레이턴시 갱신 (서버 ts 이용)
  RISK_KILL_SWITCH    → 킬스위치 경고 표시

장 구분 (ET 기준):
  프리마켓  04:00 – 09:30
  정규장    09:30 – 16:00
  애프터    16:00 – 20:00
  장마감    그 외
"""
from __future__ import annotations
import time
import logging
from datetime import datetime, timezone, timedelta

from PyQt6.QtWidgets import QStatusBar, QLabel, QWidget, QHBoxLayout, QFrame
from PyQt6.QtCore    import Qt, QTimer, pyqtSlot
from PyQt6.QtGui     import QColor, QPalette

from shared.core.bus    import DataBus
from shared.core.topics import Topics

log = logging.getLogger(__name__)

# ── 색상 ────────────────────────────────────────────────
_C_OK      = "#22C55E"   # 연결 (녹색)
_C_WARN    = "#F59E0B"   # 경고 (황색)
_C_ERR     = "#EF4444"   # 오류/단절 (빨강)
_C_MOCK    = "#A78BFA"   # Mock 모드 (보라)
_C_DIM     = "#9CA3AF"   # 비활성 텍스트
_C_TEXT    = "#F9FAFB"   # 밝은 텍스트 (다크 상태바용)
_C_BG      = "#1E293B"   # 위젯 배경

_LED_QSS = (
    "QLabel {{ min-width:10px; max-width:10px;"
    " min-height:10px; max-height:10px;"
    " border-radius:5px; background:{color}; }}"
)
_LBL_QSS = "QLabel {{ color:{color}; font-size:11px; font-family:monospace; }}"


def _lbl(text: str, color: str = _C_TEXT) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(_LBL_QSS.format(color=color))
    return w


def _sep() -> QLabel:
    s = QLabel("│")
    s.setStyleSheet(f"color:#334155; font-size:12px;")
    return s


class ConnectionWidget(QWidget):
    """● 상태등 + 브로커 이름 텍스트"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(4)

        self._led = QLabel()
        self._led.setFixedSize(10, 10)
        self._txt = QLabel("Mock")
        self._txt.setStyleSheet(_LBL_QSS.format(color=_C_MOCK))

        lay.addWidget(self._led)
        lay.addWidget(self._txt)
        self._set_state("mock")

    def _set_state(self, state: str, name: str = "") -> None:
        """state: 'mock' | 'connected' | 'disconnected' | 'kill'"""
        if state == "mock":
            color, text = _C_MOCK, "⬤ Mock"
        elif state == "connected":
            color, text = _C_OK, f"⬤ {name or 'IBKR'}"
        elif state == "kill":
            color, text = _C_ERR, "⬤ KILL"
        else:
            color, text = _C_ERR, "⬤ 단절"

        self._led.setStyleSheet(
            f"background:{color}; border-radius:5px; min-width:10px; max-width:10px;"
            f" min-height:10px; max-height:10px;")
        self._txt.setText(text)
        self._txt.setStyleSheet(_LBL_QSS.format(color=color))

    def on_connected(self, broker: str) -> None:
        if broker == "mock":
            self._set_state("mock")
        else:
            self._set_state("connected", broker.upper())

    def on_disconnected(self) -> None:
        self._set_state("disconnected")

    def on_kill(self) -> None:
        self._set_state("kill")


class LatencyWidget(QWidget):
    """⚡ 0ms — 마지막 tick의 서버→수신 레이턴시"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(3)

        icon = _lbl("⚡", _C_DIM)
        self._val = _lbl("--ms", _C_DIM)
        lay.addWidget(icon)
        lay.addWidget(self._val)

        self._samples: list[float] = []

    def update_latency(self, server_ts_ms: float) -> None:
        """server_ts_ms: 서버에서 찍힌 Unix ms 타임스탬프"""
        now_ms = time.time() * 1000
        lat = now_ms - server_ts_ms
        if lat < 0 or lat > 60_000:
            return   # 이상값 제거
        self._samples.append(lat)
        if len(self._samples) > 20:
            self._samples.pop(0)
        avg = sum(self._samples) / len(self._samples)

        color = _C_OK if avg < 100 else (_C_WARN if avg < 500 else _C_ERR)
        self._val.setText(f"{avg:.0f}ms")
        self._val.setStyleSheet(_LBL_QSS.format(color=color))


def _get_market_session() -> tuple[str, str, str]:
    """
    현재 ET 시간 기준 장 구분 반환.
    반환: (세션명, 이모지, 색상 hex)
    """
    ET = timezone(timedelta(hours=-5))   # EST (DST 미적용 — 근사치)
    now_et = datetime.now(ET)
    # 주말 체크
    if now_et.weekday() >= 5:
        return ("주말 휴장", "🔴", "#EF4444")
    h, m = now_et.hour, now_et.minute
    mins = h * 60 + m
    if mins < 4 * 60:           # 00:00 ~ 04:00
        return ("장 마감", "🔴", "#EF4444")
    elif mins < 9 * 60 + 30:    # 04:00 ~ 09:30
        return ("프리마켓", "🟡", "#F59E0B")
    elif mins < 16 * 60:        # 09:30 ~ 16:00
        return ("정규장", "🟢", "#22C55E")
    elif mins < 20 * 60:        # 16:00 ~ 20:00
        return ("애프터마켓", "🟠", "#F97316")
    else:                        # 20:00 ~
        return ("장 마감", "🔴", "#EF4444")


class MarketSessionWidget(QWidget):
    """장 구분 표시 위젯 (1분마다 자동 갱신)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(4)

        self._icon_lbl = QLabel()
        self._text_lbl = QLabel()
        self._text_lbl.setStyleSheet(_LBL_QSS.format(color=_C_TEXT))
        lay.addWidget(self._icon_lbl)
        lay.addWidget(self._text_lbl)

        self._refresh()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(30_000)   # 30초마다 갱신

    def _refresh(self) -> None:
        session, emoji, color = _get_market_session()
        ET = timezone(timedelta(hours=-5))
        now_et = datetime.now(ET)
        time_str = now_et.strftime("%H:%M")
        self._icon_lbl.setText(emoji)
        self._icon_lbl.setStyleSheet(f"font-size:11px;")
        self._text_lbl.setText(f"{session}  ET {time_str}")
        self._text_lbl.setStyleSheet(_LBL_QSS.format(color=color))


class SubscriptionWidget(QWidget):
    """📡 구독 N개"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(3)

        icon = _lbl("📡", _C_DIM)
        self._val = _lbl("0개", _C_DIM)
        lay.addWidget(icon)
        lay.addWidget(self._val)
        self._count = 0

    def set_count(self, n: int) -> None:
        self._count = n
        color = _C_OK if n > 0 else _C_DIM
        self._val.setText(f"{n}개")
        self._val.setStyleSheet(_LBL_QSS.format(color=color))

    def set_lines(self, snap: dict) -> None:
        """IBKR 시세 라인 집계(내 앱 + 같은 PC 의 다른 앱) 표시"""
        local, others = snap.get("local", 0), snap.get("others", 0)
        total, max_ = snap.get("total", local + others), snap.get("max", 95)
        text = (f"내 {local} + 타앱 {others} = {total} / {max_}" if others
                else f"{total} / {max_}")
        if snap.get("limit_hit"):
            text += " ⚠한도"
        ratio = total / max_ if max_ else 0.0
        color = "#F85149" if (ratio >= 1.0 or snap.get("limit_hit")) else \
                "#D29922" if ratio >= 0.8 else (_C_OK if total > 0 else _C_DIM)
        self._count = total
        self._val.setText(text)
        self._val.setStyleSheet(_LBL_QSS.format(color=color))
        det = snap.get("others_detail") or {}
        tags = snap.get("by_tag") or {}
        self._val.setToolTip(
            "IBKR 시세 라인(계정 공유 풀) 사용량\n"
            f"내 앱: {local}  ({', '.join(f'{k} {v}' for k, v in tags.items()) or '-'})\n"
            f"다른 앱: {others}  ({', '.join(f'{k} {v}' for k, v in det.items()) or '-'})\n"
            f"한도(안전 여유 포함): {max_}")


class HtsStatusBar:
    """
    상태바에 영구 위젯 4개 추가.
    main_window._status_bar에 install() 호출 후 사용.
    """

    def __init__(self, status_bar: QStatusBar, bus: DataBus,
                 feed=None) -> None:
        self._bar  = status_bar
        self._bus  = bus
        self._feed = feed   # IBKRFeed — 구독 수 조회용

        # ── 위젯 생성 ─────────────────────────────────
        self._market  = MarketSessionWidget()
        self._conn    = ConnectionWidget()
        self._subs    = SubscriptionWidget()
        self._lat     = LatencyWidget()

        # 상태바 배경 (다크)
        for w in (self._market, self._conn, self._subs, self._lat):
            w.setStyleSheet(f"background:{_C_BG}; border-left:1px solid #334155;")

        # 우측에서부터 영구 위젯 등록 (addPermanentWidget은 우측 정렬)
        self._bar.addPermanentWidget(self._market)
        self._bar.addPermanentWidget(_sep_widget())
        self._bar.addPermanentWidget(self._conn)
        self._bar.addPermanentWidget(_sep_widget())
        self._bar.addPermanentWidget(self._subs)
        self._bar.addPermanentWidget(_sep_widget())
        self._bar.addPermanentWidget(self._lat)

        # ── DataBus 구독 ──────────────────────────────
        bus.subscribe(Topics.BROKER_CONNECTED,    self,
                      lambda d: self._on_connected(d))
        bus.subscribe(Topics.BROKER_DISCONNECTED, self,
                      lambda d: self._conn.on_disconnected())
        bus.subscribe(Topics.RISK_KILL_SWITCH,    self,
                      lambda d: self._on_kill())
        bus.subscribe("stock.price.*", self,
                      lambda d: self._on_tick(d))

        # 구독 수 갱신 타이머 (1초마다)
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh_subs)
        self._timer.start(1000)

    def _on_connected(self, data: dict) -> None:
        broker = data.get("broker", "") if isinstance(data, dict) else ""
        self._conn.on_connected(broker)
        self._refresh_subs()

    def _on_kill(self) -> None:
        self._conn.on_kill()

    def _on_tick(self, data: dict) -> None:
        """가격 tick 수신 → 레이턴시 갱신 (ts는 Unix 초 단위 float)"""
        ts = data.get("ts") or data.get("timestamp")
        if ts:
            ts_ms = float(ts) * 1000   # 초 → ms 변환
            self._lat.update_latency(ts_ms)

    def _refresh_subs(self) -> None:
        """구독 수 갱신 — 라인 집계(LineRegistry)가 있으면 그것을 우선 사용"""
        try:
            from shared.core.line_registry import get_line_registry
            _reg = get_line_registry()
            if _reg is not None:
                self._subs.set_lines(_reg.snapshot())
                return
        except Exception:
            pass
        if self._feed and hasattr(self._feed, "_subs"):
            n = len(self._feed._subs)
        else:
            n = 0
        self._subs.set_count(n)


def _sep_widget() -> QLabel:
    s = QLabel("│")
    s.setStyleSheet(f"color:#334155; font-size:14px; background:{_C_BG};")
    return s
