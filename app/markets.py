from typing import Any
from app.price_data import fetch_price_series

TICKERS = {
    "S&P500 ETF": {"symbol": "SPY", "stooq": ["spy.us"]},
    "NASDAQ100 ETF": {"symbol": "QQQ", "stooq": ["qqq.us"]},
    "Dow ETF": {"symbol": "DIA", "stooq": ["dia.us"]},
    "Nikkei225 ETF": {"symbol": "1321.T", "stooq": ["1321.jp", "1321.t"]},
    "TOPIX ETF": {"symbol": "1306.T", "stooq": ["1306.jp", "1306.t"]},
    "USDJPY": {"symbol": "JPY=X", "stooq": []},
    "EURUSD": {"symbol": "EURUSD=X", "stooq": []},
    "US10Y": {"symbol": "^TNX", "stooq": []},
    "Gold": {"symbol": "GC=F", "stooq": []},
    "WTI Oil": {"symbol": "CL=F", "stooq": []},
    "Bitcoin": {"symbol": "BTC-USD", "stooq": []},
}


def _change_pct(closes: list[float]) -> float:
    if len(closes) < 2 or not closes[-2]:
        return 0.0
    return (closes[-1] - closes[-2]) / closes[-2] * 100


def fetch_markets() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, meta in TICKERS.items():
        symbol = meta["symbol"]
        try:
            series = fetch_price_series(symbol, stooq_symbols=meta.get("stooq", []), period="5d")
            if series is None:
                raise ValueError("price data unavailable")
            closes = series.closes
            if len(closes) < 1:
                raise ValueError("price data unavailable")
            last = closes[-1]
            results.append({
                "name": name,
                "ticker": symbol,
                "last": round(last, 4),
                "change_pct": round(_change_pct(closes), 2),
                "source": series.source,
            })
        except Exception as exc:
            results.append({
                "name": name,
                "ticker": symbol,
                "last": None,
                "change_pct": None,
                "error": str(exc),
            })
    return results
