# core/topics.py
# 토픽 네이밍: "레이어.대상.세부" 형식
# 문자열 하드코딩 금지 — 반드시 Topics 정적 메서드 사용


class Topics:
    # ── 시장 데이터 ──────────────────────────────────────
    @staticmethod
    def stock_price(symbol: str) -> str:
        return f"stock.price.{symbol}"

    @staticmethod
    def stock_orderbook(symbol: str) -> str:
        return f"stock.orderbook.{symbol}"

    @staticmethod
    def stock_trade(symbol: str) -> str:
        return f"stock.trade.{symbol}"

    # ── 지표 (이동평균/RSI 등) ────────────────────────────
    @staticmethod
    def indicator(symbol: str, name: str) -> str:
        return f"indicator.{name}.{symbol}"

    # ── 주문/체결 ─────────────────────────────────────────
    @staticmethod
    def order_status(order_id: str) -> str:
        return f"order.status.{order_id}"

    @staticmethod
    def order_fill(order_id: str) -> str:
        return f"order.fill.{order_id}"

    # ── 포지션/잔고 ───────────────────────────────────────
    @staticmethod
    def portfolio_position(symbol: str) -> str:
        return f"portfolio.position.{symbol}"

    PORTFOLIO_SUMMARY = "portfolio.summary"
    ACCOUNT_BALANCE   = "account.balance"

    # ── 리스크 ───────────────────────────────────────────
    RISK_KILL_SWITCH   = "risk.kill_switch"
    RISK_DAILY_PNL     = "risk.daily_pnl"
    RISK_WARNING       = "risk.warning"

    # ── 시스템 ───────────────────────────────────────────
    SYSTEM_STATUS      = "system.status"
    SYSTEM_ERROR       = "system.error"
    BROKER_CONNECTED   = "broker.connected"
    BROKER_DISCONNECTED = "broker.disconnected"
