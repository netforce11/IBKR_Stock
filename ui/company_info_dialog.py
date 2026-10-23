# ui/company_info_dialog.py
# 종목 기업 정보 창 (스캐너 결과 더블클릭)
#
# [표시 내용]
#   1. 시세 요약      — 스캐너 결과 행 (현재가/등락/거래량/시가총액)
#   2. 투자 지표      — IBKR Fundamental Ratios (PER/EPS/PBR/배당/베타/ROE/52주 고저)
#   3. 기업 정보      — IBKR 계약 상세 (회사명/업종 분류/거래소)
#                       + Yahoo Finance (섹터/산업/국가/직원 수/웹사이트)
#   4. 회사 개요      — Yahoo Finance 사업 설명 → 한국어 번역 (원문 전환 가능)
#
# Yahoo Finance 조회는 yfinance 패키지가 필요 (pip install yfinance).
# 한국어 번역은 deep-translator 패키지가 필요 (pip install deep-translator,
# Google 번역 무료 엔드포인트 사용 — API 키 불필요). 미설치 시 영문 원문 표시.
# 조회·번역은 백그라운드 스레드에서 수행.

from __future__ import annotations

import logging
import threading

from PyQt6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QTextBrowser, QPushButton, QWidget,
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal

log = logging.getLogger(__name__)

_GROUP_STYLE = (
    "QGroupBox { font-size:11px; font-weight:bold; color:#1565C0;"
    " border:1px solid #BFDBFE; border-radius:4px;"
    " margin-top:8px; padding-top:6px; background:#fff; }"
    "QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px; }"
)

# 심볼별 Yahoo 조회 결과 캐시 (앱 실행 중 유지)
_YAHOO_CACHE: dict[str, dict] = {}


def _fmt_big(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    if not v:
        return "-"
    a = abs(v)
    if a >= 1e12: return f"{v/1e12:.2f}T"
    if a >= 1e9:  return f"{v/1e9:.2f}B"
    if a >= 1e6:  return f"{v/1e6:.1f}M"
    if a >= 1e3:  return f"{v/1e3:.0f}K"
    return f"{v:,.0f}"


def _fmt_num(v, digits: int = 2, suffix: str = "") -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{v:,.{digits}f}{suffix}"


def _translate_ko(text: str) -> tuple[str, str]:
    """영문 → 한국어 번역. (번역문, 오류메시지) 반환. 백그라운드 스레드에서 호출."""
    if not text:
        return "", ""
    try:
        from deep_translator import GoogleTranslator   # type: ignore
    except ImportError:
        return "", "deep-translator 미설치 — 'pip install deep-translator' 시 한국어로 표시됩니다."
    # 요청당 5000자 제한 → 문장 단위로 4500자 이내 청크 분할
    chunks, cur = [], ""
    for sent in text.replace("\n", " ").split(". "):
        piece = sent if sent.endswith(".") else sent + ". "
        if len(cur) + len(piece) > 4500 and cur:
            chunks.append(cur)
            cur = ""
        cur += piece
    if cur:
        chunks.append(cur)
    try:
        tr = GoogleTranslator(source="auto", target="ko")
        return " ".join(tr.translate(c) or "" for c in chunks).strip(), ""
    except Exception as e:
        return "", f"번역 실패 ({e}) — 영문 원문을 표시합니다."


def _fetch_yahoo(symbol: str) -> dict:
    """yfinance 로 기업 개요 조회 (백그라운드 스레드에서 호출)"""
    if symbol in _YAHOO_CACHE:
        return _YAHOO_CACHE[symbol]
    try:
        import yfinance as yf   # type: ignore
    except ImportError:
        return {"error": "yfinance 미설치 — 'pip install yfinance' 후 재시작하면 "
                         "회사 개요가 표시됩니다."}
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as e:   # 네트워크 오류 / 차단 등
        return {"error": f"Yahoo Finance 조회 실패: {e}"}
    if not info or len(info) <= 2:
        return {"error": "Yahoo Finance 에 기업 정보가 없습니다."}
    data = {
        "name":      info.get("longName") or info.get("shortName", ""),
        "sector":    info.get("sector", ""),
        "industry":  info.get("industry", ""),
        "country":   info.get("country", ""),
        "city":      info.get("city", ""),
        "employees": info.get("fullTimeEmployees"),
        "website":   info.get("website", ""),
        "summary":   info.get("longBusinessSummary", ""),
        "market_cap": info.get("marketCap"),
        "pe":        info.get("trailingPE"),
        "fwd_pe":    info.get("forwardPE"),
        "eps":       info.get("trailingEps"),
        "pbr":       info.get("priceToBook"),
        "div_yield": info.get("dividendYield"),
        "beta":      info.get("beta"),
        "high_52w":  info.get("fiftyTwoWeekHigh"),
        "low_52w":   info.get("fiftyTwoWeekLow"),
        "revenue":   info.get("totalRevenue"),
    }
    data["summary_ko"], data["translate_error"] = _translate_ko(data["summary"])
    _YAHOO_CACHE[symbol] = data
    return data


class _Signals(QObject):
    yahoo_done = pyqtSignal(object)
    ibkr_done  = pyqtSignal(object, str)


class CompanyInfoDialog(QDialog):
    """종목 기업 정보 창 (비모달)"""

    def __init__(self, row: dict, broker=None, parent=None) -> None:
        super().__init__(parent)
        self._row    = row or {}
        self._symbol = self._row.get("symbol", "").upper()
        self._broker = broker
        self._sig    = _Signals(self)
        self._sig.yahoo_done.connect(self._on_yahoo)
        self._sig.ibkr_done.connect(self._on_ibkr)

        self.setWindowTitle(f"기업 정보 — {self._symbol}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(600, 720)
        self.setStyleSheet("QDialog { background:#F5F5F5; color:#111827; }"
                           "QLabel { font-size:12px; }")
        self._build_ui()
        self._fill_from_row()
        self._start_fetch()

    # ── UI ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # 헤더: 심볼 + 회사명 + 거래소
        self._lbl_sym = QLabel(self._symbol)
        self._lbl_sym.setStyleSheet(
            "font-size:20px; font-weight:bold; color:#1565C0;")
        self._lbl_name = QLabel(self._row.get("long_name") or "회사명 조회 중…")
        self._lbl_name.setStyleSheet("font-size:14px; color:#374151;")
        self._lbl_name.setWordWrap(True)
        hdr = QVBoxLayout()
        hdr.setSpacing(2)
        hdr.addWidget(self._lbl_sym)
        hdr.addWidget(self._lbl_name)
        lay.addLayout(hdr)

        # 시세 요약 / 투자 지표 / 기업 정보 그리드
        self._vals: dict[str, QLabel] = {}
        lay.addWidget(self._grid_group("시세", [
            ("price", "현재가"), ("change", "등락"),
            ("volume", "거래량"), ("avg_volume", "평균 거래량"),
            ("vol_ratio", "거래량비"), ("market_cap", "시가총액"),
        ]))
        lay.addWidget(self._grid_group("투자 지표", [
            ("pe", "PER"), ("eps", "EPS (TTM)"),
            ("pbr", "PBR"), ("div_yield", "배당수익률"),
            ("beta", "베타"), ("roe", "ROE"),
            ("high_52w", "52주 최고"), ("low_52w", "52주 최저"),
        ]))
        lay.addWidget(self._grid_group("기업 정보", [
            ("sector", "섹터"), ("industry", "산업"),
            ("ib_class", "IBKR 분류"), ("exchange", "거래소"),
            ("country", "국가"), ("employees", "직원 수"),
            ("website", "웹사이트"), ("revenue", "매출 (TTM)"),
        ]))

        grp = QGroupBox("회사 개요")
        grp.setStyleSheet(_GROUP_STYLE)
        g = QVBoxLayout(grp)
        g.setContentsMargins(8, 14, 8, 8)
        self._summary = QTextBrowser()
        self._summary.setOpenExternalLinks(True)
        self._summary.setStyleSheet(
            "QTextBrowser { background:#fff; border:none; font-size:16px; }")
        self._summary.setPlainText("회사 개요 조회 중…")
        g.addWidget(self._summary)

        # 번역 / 원문 전환
        tr_row = QHBoxLayout()
        self._tr_lbl = QLabel("")
        self._tr_lbl.setStyleSheet("color:#888; font-size:10px;")
        tr_row.addWidget(self._tr_lbl, 1)
        self._btn_orig = QPushButton("원문 보기")
        self._btn_orig.setCheckable(True)
        self._btn_orig.setVisible(False)
        self._btn_orig.setStyleSheet(
            "background:#E8ECF0; color:#374151; font-size:11px; padding:2px 10px;"
            "border:1px solid #CDD0D5; border-radius:3px;")
        self._btn_orig.toggled.connect(self._on_toggle_original)
        tr_row.addWidget(self._btn_orig)
        g.addLayout(tr_row)
        self._summary_en = ""
        self._summary_ko = ""
        lay.addWidget(grp, 1)

        # 하단: 출처 표시 + 닫기
        bottom = QHBoxLayout()
        self._src_lbl = QLabel("")
        self._src_lbl.setStyleSheet("color:#888; font-size:10px;")
        bottom.addWidget(self._src_lbl, 1)
        btn = QPushButton("닫기")
        btn.setStyleSheet(
            "background:#E8ECF0; color:#374151; padding:4px 14px;"
            "border:1px solid #CDD0D5; border-radius:3px;")
        btn.clicked.connect(self.close)
        bottom.addWidget(btn)
        lay.addLayout(bottom)

    def _grid_group(self, title: str, fields: list) -> QGroupBox:
        grp = QGroupBox(title)
        grp.setStyleSheet(_GROUP_STYLE)
        grid = QGridLayout(grp)
        grid.setContentsMargins(8, 14, 8, 8)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        for i, (key, label) in enumerate(fields):
            r, col = divmod(i, 2)
            k = QLabel(label)
            k.setStyleSheet("color:#6B7280;")
            v = QLabel("-")
            v.setStyleSheet("font-weight:bold;")
            v.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByMouse)
            v.setOpenExternalLinks(True)
            grid.addWidget(k, r, col * 2)
            grid.addWidget(v, r, col * 2 + 1)
            self._vals[key] = v
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        return grp

    def _set(self, key: str, text: str, color: str | None = None,
             only_if_empty: bool = False) -> None:
        lbl = self._vals.get(key)
        if lbl is None or not text or text == "-":
            return
        if only_if_empty and lbl.text() not in ("", "-"):
            return
        lbl.setText(text)
        if color:
            lbl.setStyleSheet(f"font-weight:bold; color:{color};")

    # ── 스캐너 행 데이터 ────────────────────────────────────
    def _fill_from_row(self) -> None:
        r = self._row
        price = r.get("price") or 0
        pct   = r.get("change_pct") or 0
        chg   = r.get("change") or 0
        color = "#C62828" if pct < 0 else "#1565C0" if pct > 0 else None
        if price:
            self._set("price", f"${price:,.2f}")
            self._set("change",
                      f"{'+' if chg >= 0 else ''}{chg:,.2f} "
                      f"({'+' if pct >= 0 else ''}{pct:.2f}%)", color)
        vol, avg = r.get("volume") or 0, r.get("avg_volume") or 0
        self._set("volume", _fmt_big(vol))
        self._set("avg_volume", _fmt_big(avg))
        if vol and avg:
            self._set("vol_ratio", f"{vol/avg:.2f}x")
        self._set("market_cap", _fmt_big(r.get("market_cap")))
        self._set("exchange", r.get("exchange", ""))

        f = r.get("fundamentals") or {}
        if f:
            self._set("pe",        _fmt_num(f.get("pe")))
            self._set("eps",       _fmt_num(f.get("eps"), 2, ""))
            self._set("pbr",       _fmt_num(f.get("pbr")))
            self._set("div_yield", _fmt_num(f.get("div_yield"), 2, "%"))
            self._set("beta",      _fmt_num(f.get("beta")))
            self._set("roe",       _fmt_num(f.get("roe"), 1, "%"))
            self._set("high_52w",  _fmt_num(f.get("high_52w")))
            self._set("low_52w",   _fmt_num(f.get("low_52w")))
            if f.get("revenue_ttm_m"):
                self._set("revenue", _fmt_big(f["revenue_ttm_m"] * 1e6))

    # ── 비동기 조회 ─────────────────────────────────────────
    def _start_fetch(self) -> None:
        if not self._symbol:
            return
        # IBKR 계약 상세 — 콜백은 drain_tick_queue(메인 스레드)에서 호출됨
        req = getattr(self._broker, "request_contract_details", None)
        sent = False
        if req:
            try:
                sent = req(self._symbol, self._sig.ibkr_done.emit,
                           con_id=self._row.get("con_id", 0))
            except Exception:
                log.exception("request_contract_details 실패")
        if not sent and not self._row.get("long_name"):
            self._lbl_name.setText("")

        sig = self._sig
        sym = self._symbol

        def _worker() -> None:
            data = _fetch_yahoo(sym)
            try:
                sig.yahoo_done.emit(data)
            except RuntimeError:
                pass   # 창이 이미 닫힘
        threading.Thread(target=_worker, daemon=True,
                         name=f"yahoo-{sym}").start()

    def _on_ibkr(self, info, err: str) -> None:
        if not info:
            log.debug("계약 상세 조회 실패 %s: %s", self._symbol, err)
            return
        if info.get("long_name"):
            self._lbl_name.setText(info["long_name"])
        cls = " / ".join(x for x in (info.get("industry"), info.get("category"),
                                     info.get("subcategory")) if x)
        self._set("ib_class", cls)
        self._set("exchange", info.get("primary_exchange", ""))
        self._vals["ib_class"].setWordWrap(True)
        self._add_source("IBKR")

    def _on_yahoo(self, d: dict) -> None:
        if d.get("error"):
            self._summary.setPlainText(d["error"])
            return
        if d.get("name") and self._lbl_name.text() in ("", "회사명 조회 중…"):
            self._lbl_name.setText(d["name"])
        self._set("sector",   d.get("sector", ""))
        self._set("industry", d.get("industry", ""))
        country = ", ".join(x for x in (d.get("city"), d.get("country")) if x)
        self._set("country", country)
        if d.get("employees"):
            self._set("employees", f"{int(d['employees']):,}명")
        site = d.get("website", "")
        if site:
            self._set("website", f'<a href="{site}">{site.split("//")[-1]}</a>')
        self._set("revenue", _fmt_big(d.get("revenue")), only_if_empty=True)
        # IBKR 지표가 없을 때만 Yahoo 값으로 채움
        self._set("market_cap", _fmt_big(d.get("market_cap")), only_if_empty=True)
        self._set("pe",   _fmt_num(d.get("pe")), only_if_empty=True)
        self._set("eps",  _fmt_num(d.get("eps")), only_if_empty=True)
        self._set("pbr",  _fmt_num(d.get("pbr")), only_if_empty=True)
        dy = d.get("div_yield")
        if dy is not None:
            # yfinance 는 버전에 따라 0.0123(비율) 또는 1.23(%) 로 반환
            dy = float(dy)
            dy = dy * 100 if dy < 0.5 else dy
            self._set("div_yield", f"{dy:.2f}%", only_if_empty=True)
        self._set("beta",     _fmt_num(d.get("beta")), only_if_empty=True)
        self._set("high_52w", _fmt_num(d.get("high_52w")), only_if_empty=True)
        self._set("low_52w",  _fmt_num(d.get("low_52w")), only_if_empty=True)
        self._summary_en = d.get("summary") or ""
        self._summary_ko = d.get("summary_ko") or ""
        if not self._summary_en:
            self._summary.setPlainText("사업 개요 정보가 없습니다.")
        elif self._summary_ko:
            self._summary.setPlainText(self._summary_ko)
            self._tr_lbl.setText("Google 번역 (자동 번역이라 일부 표현이 어색할 수 있음)")
            self._btn_orig.setVisible(True)
        else:
            self._summary.setPlainText(self._summary_en)
            self._tr_lbl.setText(d.get("translate_error", ""))
        self._add_source("Yahoo Finance")

    def _on_toggle_original(self, show_original: bool) -> None:
        if show_original:
            self._summary.setPlainText(self._summary_en)
            self._btn_orig.setText("번역 보기")
        else:
            self._summary.setPlainText(self._summary_ko)
            self._btn_orig.setText("원문 보기")

    def _add_source(self, name: str) -> None:
        cur = self._src_lbl.text()
        srcs = [s for s in cur.replace("출처: ", "").split(", ") if s]
        if name not in srcs:
            srcs.append(name)
        self._src_lbl.setText("출처: " + ", ".join(srcs))
