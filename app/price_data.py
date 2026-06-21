from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import csv
import io
import json
import math
import time
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass
class PricePoint:
    date: str
    close: float
    high: float | None = None
    low: float | None = None


@dataclass
class PriceSeries:
    symbol: str
    source: str
    points: list[PricePoint]

    @property
    def closes(self) -> list[float]:
        return [p.close for p in self.points if p.close is not None]


def _is_number(value: Any) -> bool:
    try:
        v = float(value)
        return not math.isnan(v) and not math.isinf(v)
    except Exception:
        return False


def _request_text(url: str, timeout: int = 5) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            "Accept": "application/json,text/csv,text/plain,*/*",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Connection": "close",
        },
    )
    with urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", errors="replace")


def fetch_yahoo_chart(symbol: str, range_: str = "3mo", interval: str = "1d") -> PriceSeries | None:
    """Yahoo Chart APIを直接読む。yfinanceがJSONDecodeErrorになるPCでも通ることがある。"""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + quote(symbol, safe="")
        + f"?range={quote(range_)}&interval={quote(interval)}&includePrePost=false&events=div%2Csplits"
    )
    text = _request_text(url)
    if not text.strip():
        return None
    data = json.loads(text)
    result = data.get("chart", {}).get("result") or []
    if not result:
        return None
    item = result[0]
    timestamps = item.get("timestamp") or []
    quote_data = (item.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote_data.get("close") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []
    points: list[PricePoint] = []
    for idx, (ts, close) in enumerate(zip(timestamps, closes)):
        if _is_number(close):
            date = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
            high = float(highs[idx]) if idx < len(highs) and _is_number(highs[idx]) else None
            low = float(lows[idx]) if idx < len(lows) and _is_number(lows[idx]) else None
            points.append(PricePoint(date=date, close=float(close), high=high, low=low))
    if len(points) < 2:
        return None
    return PriceSeries(symbol=symbol, source="Yahoo Chart API", points=points)


def fetch_stooq_csv(stooq_symbol: str, days: int = 130) -> PriceSeries | None:
    """無料のStooq CSVを読む。主に米国株・ETFのフォールバック用。"""
    today = datetime.now().date()
    start = today - timedelta(days=days)
    d1 = start.strftime("%Y%m%d")
    d2 = today.strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={quote(stooq_symbol.lower())}&d1={d1}&d2={d2}&i=d"
    text = _request_text(url)
    if not text.strip() or "No data" in text[:80]:
        return None
    reader = csv.DictReader(io.StringIO(text))
    points: list[PricePoint] = []
    for row in reader:
        close = row.get("Close") or row.get("close")
        high = row.get("High") or row.get("high")
        low = row.get("Low") or row.get("low")
        date = row.get("Date") or row.get("date")
        if date and _is_number(close):
            points.append(PricePoint(date=date, close=float(close), high=float(high) if _is_number(high) else None, low=float(low) if _is_number(low) else None))
    if len(points) < 2:
        return None
    return PriceSeries(symbol=stooq_symbol, source="Stooq CSV", points=points)


def fetch_yfinance_history(symbol: str, period: str = "3mo") -> PriceSeries | None:
    """最後の保険。yfinanceは環境によって失敗するので最後にだけ使う。"""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        data = yf.download(symbol, period=period, progress=False, auto_adjust=True, threads=False, timeout=5)
        if data is None or data.empty:
            return None
        close = data["Close"]
        high_series = data["High"] if "High" in data else None
        low_series = data["Low"] if "Low" in data else None
        if hasattr(close, "iloc") and len(getattr(close, "shape", [])) > 1:
            close = close.iloc[:, 0]
            if high_series is not None: high_series = high_series.iloc[:, 0]
            if low_series is not None: low_series = low_series.iloc[:, 0]
        points: list[PricePoint] = []
        for idx, value in close.items():
            if _is_number(value):
                try:
                    date = idx.date().isoformat()
                except Exception:
                    date = str(idx)[:10]
                high = high_series.loc[idx] if high_series is not None and idx in high_series.index else None
                low = low_series.loc[idx] if low_series is not None and idx in low_series.index else None
                points.append(PricePoint(date=date, close=float(value), high=float(high) if _is_number(high) else None, low=float(low) if _is_number(low) else None))
        if len(points) < 2:
            return None
        return PriceSeries(symbol=symbol, source="yfinance", points=points)
    except Exception:
        return None


def fetch_price_series(symbol: str, stooq_symbols: list[str] | None = None, period: str = "3mo") -> PriceSeries | None:
    """複数ソースを順番に試して、価格時系列を返す。"""
    # Yahooを最初にする。日本株・指数・為替・商品・BTCにも対応しやすい。
    try:
        result = fetch_yahoo_chart(symbol, range_=period)
        if result is not None:
            return result
    except Exception:
        pass

    # StooqはUS株・ETFでかなり安定する。
    for stooq_symbol in stooq_symbols or []:
        try:
            result = fetch_stooq_csv(stooq_symbol)
            if result is not None:
                return result
        except Exception:
            pass

    # 少し待ってYahooをもう一度試す。短時間の接続失敗対策。
    try:
        time.sleep(0.25)
        result = fetch_yahoo_chart(symbol, range_=period)
        if result is not None:
            return result
    except Exception:
        pass

    # 最後にyfinance。
    return fetch_yfinance_history(symbol, period=period)
