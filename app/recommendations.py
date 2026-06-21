from typing import Any
import math
from app.config import ENABLE_STOCK_SIGNALS
from app.price_data import fetch_price_series

WATCHLIST = {
    "SPY": {"name": "S&P500 ETF", "stooq": ["spy.us"]},
    "QQQ": {"name": "NASDAQ100 ETF", "stooq": ["qqq.us"]},
    "AAPL": {"name": "Apple", "stooq": ["aapl.us"]},
    "MSFT": {"name": "Microsoft", "stooq": ["msft.us"]},
    "NVDA": {"name": "NVIDIA", "stooq": ["nvda.us"]},
    "GOOGL": {"name": "Alphabet", "stooq": ["googl.us"]},
    "AMZN": {"name": "Amazon", "stooq": ["amzn.us"]},
    "META": {"name": "Meta Platforms", "stooq": ["meta.us"]},
    "TSLA": {"name": "Tesla", "stooq": ["tsla.us"]},
    "7203.T": {"name": "Toyota", "stooq": ["7203.jp", "7203.t"]},
    "6758.T": {"name": "Sony Group", "stooq": ["6758.jp", "6758.t"]},
    "9984.T": {"name": "SoftBank Group", "stooq": ["9984.jp", "9984.t"]},
    "8306.T": {"name": "Mitsubishi UFJ", "stooq": ["8306.jp", "8306.t"]},
    "9432.T": {"name": "NTT", "stooq": ["9432.jp", "9432.t"]},
}


ALIASES = {
    "トヨタ": "7203.T",
    "toyota": "7203.T",
    "ソニー": "6758.T",
    "sony": "6758.T",
    "ソフトバンク": "9984.T",
    "softbank": "9984.T",
    "三菱ufj": "8306.T",
    "三菱UFJ": "8306.T",
    "mufg": "8306.T",
    "ntt": "9432.T",
    "日本電信電話": "9432.T",
    "アップル": "AAPL",
    "apple": "AAPL",
    "マイクロソフト": "MSFT",
    "microsoft": "MSFT",
    "エヌビディア": "NVDA",
    "nvidia": "NVDA",
    "アマゾン": "AMZN",
    "amazon": "AMZN",
    "テスラ": "TSLA",
    "tesla": "TSLA",
    "メタ": "META",
    "meta": "META",
    "グーグル": "GOOGL",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "sp500": "SPY",
    "s&p500": "SPY",
    "s&p 500": "SPY",
    "nasdaq100": "QQQ",
    "nasdaq 100": "QQQ",
    "ナスダック": "QQQ",
}


def normalize_symbol(query: str) -> str:
    """検索入力をYahoo Finance用のティッカーに寄せる。日本株は4桁入力なら自動で .T を付ける。"""
    q = (query or "").strip()
    if not q:
        return ""
    q = q.replace("　", " ").strip()
    alias_key = q.lower()
    if alias_key in ALIASES:
        return ALIASES[alias_key]
    if q in ALIASES:
        return ALIASES[q]
    compact = q.replace(" ", "")
    if compact.lower() in ALIASES:
        return ALIASES[compact.lower()]
    if compact.isdigit() and len(compact) == 4:
        return compact + ".T"
    return compact.upper()


def _default_stooq_symbols(symbol: str) -> list[str]:
    if symbol.endswith(".T"):
        base = symbol.split(".")[0]
        return [base + ".jp", base + ".t"]
    if symbol.startswith("^") or "=" in symbol:
        return []
    return [symbol.lower() + ".us"]


def _meta_for_symbol(query: str) -> tuple[str, dict[str, Any]]:
    symbol = normalize_symbol(query)
    if not symbol:
        return "", {"name": "", "stooq": []}
    meta = WATCHLIST.get(symbol)
    if meta:
        return symbol, meta
    return symbol, {"name": symbol, "stooq": _default_stooq_symbols(symbol)}


def _safe_float(x) -> float | None:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _pct(a: float, b: float) -> float:
    return ((a - b) / b * 100) if b else 0.0


def _mean(values: list[float]) -> float | None:
    vals = [_safe_float(v) for v in values]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _pct_changes(values: list[float]) -> list[float]:
    changes: list[float] = []
    clean = [_safe_float(v) for v in values]
    clean = [v for v in clean if v is not None]
    for prev, cur in zip(clean, clean[1:]):
        if prev:
            changes.append((cur - prev) / prev * 100)
    return changes


def _round_price(value: float | None, symbol: str) -> float | int | None:
    v = _safe_float(value)
    if v is None:
        return None
    # 日本株は円建てなので整数表示、米国株・ETFは小数2桁表示にする。
    if symbol.endswith('.T'):
        return int(round(v))
    return round(v, 2)


def _atr_proxy(points: list[Any], closes: list[float], last: float) -> float:
    """ATRに近い値幅を作る。高値/安値が無いデータでも終値変動から推定する。"""
    ranges: list[float] = []
    recent_points = points[-20:] if points else []
    for p in recent_points:
        high = _safe_float(getattr(p, "high", None))
        low = _safe_float(getattr(p, "low", None))
        if high is not None and low is not None and high > low:
            ranges.append(high - low)

    if ranges:
        atr = _mean(ranges) or 0.0
    else:
        recent = closes[-30:] if len(closes) >= 30 else closes
        changes = _pct_changes(recent)
        avg_abs_change = _mean([abs(x) for x in changes]) or 1.2
        atr = last * avg_abs_change / 100

    # 値幅が小さすぎると現在値と同じ価格になるため、最低幅を持たせる。
    min_width = last * (0.006 if last >= 50 else 0.01)
    return max(atr, min_width)


def _ensure_price_away(value: float, last: float, symbol: str, direction: str) -> float:
    """丸め後に現在値と同じ表示にならないよう、最低限離す。"""
    if symbol.endswith('.T'):
        step = max(1.0, last * 0.003)
    else:
        step = max(0.01, last * 0.003)
    if direction == 'below' and value >= last:
        return last - step
    if direction == 'above' and value <= last:
        return last + step
    return value


def _trade_plan(symbol: str, last: float, closes: list[float], signal: str, points: list[Any] | None = None) -> dict[str, Any]:
    """
    実戦向けの売買目安。
    現在値に飛びつくのではなく、押し目・戻り・ボラティリティから指値を作る。
    ただし利益を保証するものではなく、教育目的の機械計算。
    """
    clean = [_safe_float(v) for v in closes]
    clean = [v for v in clean if v is not None]
    if not clean:
        clean = [last]

    recent20 = clean[-20:] if len(clean) >= 20 else clean
    recent50 = clean[-50:] if len(clean) >= 50 else clean
    ma20 = _mean(recent20) or last
    ma50 = _mean(recent50) or ma20
    support20 = min(recent20) if recent20 else last
    resistance20 = max(recent20) if recent20 else last
    high3m = max(clean) if clean else last
    low3m = min(clean) if clean else last
    atr = _atr_proxy(points or [], clean, last)

    if signal == 'buy':
        # 上昇トレンド中は「今すぐ成行」ではなく、20日線か直近値幅の押し目を狙う。
        pullback_entry = min(last - 0.55 * atr, ma20 + 0.25 * atr)
        # ただし下げすぎた指値は刺さらない/弱すぎるため、直近支持線を割り込みすぎない位置にする。
        lower_bound = support20 + 0.35 * atr
        entry = max(pullback_entry, lower_bound)
        entry = _ensure_price_away(entry, last, symbol, 'below')

        # 損切りは支持線割れまたはATR割れ。最大損失幅が極端にならないよう調整。
        stop_by_support = support20 - 0.45 * atr
        stop_by_atr = entry - 1.25 * atr
        stop_loss = min(stop_by_support, stop_by_atr)
        max_risk = entry * 0.075
        min_risk = entry * 0.025
        risk = entry - stop_loss
        if risk > max_risk:
            stop_loss = entry - max_risk
        elif risk < min_risk:
            stop_loss = entry - min_risk

        # 利確はリスクリワード2.0倍以上を優先し、直近高値を超える目標も見る。
        risk = max(entry - stop_loss, entry * 0.02)
        rr_target = entry + risk * 2.1
        breakout_target = max(resistance20 + 0.35 * atr, high3m + 0.15 * atr)
        take_profit = max(rr_target, breakout_target)
        # 目標が遠すぎる場合は一旦現実的な範囲に収める。
        take_profit = min(take_profit, entry * 1.18)

        entry_r = _round_price(entry, symbol)
        tp_r = _round_price(take_profit, symbol)
        sl_r = _round_price(stop_loss, symbol)
        risk_pct = ((entry - stop_loss) / entry * 100) if entry else None
        reward_pct = ((take_profit - entry) / entry * 100) if entry else None
        rr = ((take_profit - entry) / (entry - stop_loss)) if (entry - stop_loss) else None
        return {
            'entry_price': entry_r,
            'take_profit_price': tp_r,
            'stop_loss_price': sl_r,
            'take_profit_pct': round(reward_pct, 1) if reward_pct is not None else None,
            'stop_loss_pct': round(risk_pct, 1) if risk_pct is not None else None,
            'risk_reward': round(rr, 2) if rr is not None else None,
            'trade_plan_note': '現在値ではなく、押し目指値＋支持線割れ損切＋リスクリワード約2倍以上で機械計算。',
        }

    # 売り候補は、現在値で慌てて売るのではなく、戻り売り/反発売却目安を出す。
    if last < ma20:
        sell_price = min(ma20 - 0.15 * atr, last + 0.85 * atr)
    else:
        sell_price = last + 0.45 * atr
    sell_price = min(sell_price, resistance20 - 0.15 * atr) if resistance20 > last else sell_price
    sell_price = _ensure_price_away(sell_price, last, symbol, 'above')

    return {
        'sell_price': _round_price(sell_price, symbol),
        'sell_price_pct_from_now': round(((sell_price - last) / last * 100), 1) if last else None,
        'trade_plan_note': '現在値ではなく、戻り売りを想定した売却目安。急落継続時は成行より指値管理向け。',
    }

def _mentioned_bonus(symbol: str, name: str, column: str) -> tuple[int, str]:
    text = column.lower()
    pairs = {
        "NVDA": ["nvidia", "エヌビディア", "半導体", "生成ai", "ai", "データセンター"],
        "TSLA": ["tesla", "テスラ", "ev", "ロボタクシー", "自動運転"],
        "QQQ": ["nasdaq", "ナスダック", "ハイテク", "成長株", "ai"],
        "SPY": ["s&p", "米国株", "米株", "frb", "利下げ", "利上げ"],
        "7203.T": ["トヨタ", "toyota", "自動車", "円安", "円高"],
        "8306.T": ["銀行", "金利", "利上げ", "金利上昇", "金融株"],
        "9432.T": ["ntt", "通信", "ディフェンシブ"],
        "9984.T": ["ソフトバンク", "softbank", "ai", "arm"],
    }
    words = pairs.get(symbol, []) + [name.lower()]
    if any(w.lower() in text for w in words):
        return 1, "今日のコラム内テーマとの関連が強い"
    return 0, "価格トレンドを中心に機械判定"


def _build_raw_signal(symbol: str, meta: dict[str, Any], column: str) -> dict[str, Any] | None:
    name = meta["name"]
    series = fetch_price_series(symbol, stooq_symbols=meta.get("stooq", []), period="3mo")
    if series is None:
        return None

    closes = series.closes
    if len(closes) < 25:
        return None

    last = _safe_float(closes[-1])
    prev5 = _safe_float(closes[-6]) if len(closes) >= 6 else _safe_float(closes[0])
    ma20 = _mean(closes[-20:])
    ma50 = _mean(closes[-50:] if len(closes) >= 50 else closes)
    high_3m = max(closes) if closes else None

    if last is None or prev5 is None or ma20 is None or ma50 is None or high_3m is None:
        return None

    mom5 = _pct(last, prev5)
    drawdown = _pct(last, high_3m)

    score = 0
    reasons: list[str] = []

    if last > ma20:
        score += 1
        reasons.append("20日平均線を上回る")
    else:
        score -= 1
        reasons.append("20日平均線を下回る")

    if last > ma50:
        score += 1
        reasons.append("中期トレンドは上向き")
    else:
        score -= 1
        reasons.append("中期トレンドは弱い")

    if mom5 > 2:
        score += 1
        reasons.append("5日モメンタムが強い")
    elif mom5 < -2:
        score -= 1
        reasons.append("5日モメンタムが弱い")

    if drawdown < -12:
        score -= 1
        reasons.append("直近高値からの下落が大きい")
    elif -4 <= drawdown <= 0:
        score += 1
        reasons.append("高値圏を維持")

    bonus, mention_reason = _mentioned_bonus(symbol, name, column)
    score += bonus
    reasons.append(mention_reason)

    return {
        "symbol": symbol,
        "name": name,
        "last": _round_price(last, symbol),
        "raw_last": last,
        "closes": closes,
        "points": series.points,
        "change_5d_pct": round(mom5, 2),
        "drawdown_3m_pct": round(drawdown, 2),
        "score": score,
        "data_source": series.source,
        "reason": "、".join(reasons[:4]) + "。",
        "notice": "教育目的の機械判定です。個別の投資助言・売買指示ではありません。",
    }


def generate_stock_signals(column: str) -> list[dict[str, Any]]:
    """教育目的の機械的シグナル。データ取得できた銘柄から買い候補・売り候補だけを返す。"""
    if not ENABLE_STOCK_SIGNALS:
        return []

    raw: list[dict[str, Any]] = []
    for symbol, meta in WATCHLIST.items():
        try:
            idea = _build_raw_signal(symbol, meta, column)
            if idea is not None:
                raw.append(idea)
        except Exception:
            continue

    if not raw:
        return []

    raw = sorted(raw, key=lambda x: x.get("score", 0), reverse=True)

    # 「様子見」は返さない。データが取れた銘柄の中から、強い上位と弱い下位だけを候補にする。
    buy_raw = [x for x in raw if x.get("score", 0) >= 2]
    sell_raw = [x for x in raw if x.get("score", 0) <= -2]

    # 閾値が厳しすぎて空になる日でも、価格データが取れているなら上位・下位を候補として出す。
    # ただし全銘柄がほぼ横ばいの場合の無理な表示を避けるため、最低限の差は見る。
    if not buy_raw and raw[0].get("score", 0) > 0:
        buy_raw = [raw[0]]
    if not sell_raw and raw[-1].get("score", 0) < 0:
        sell_raw = [raw[-1]]

    ideas: list[dict[str, Any]] = []
    for item in buy_raw[:4]:
        item = dict(item)
        item["signal"] = "買い候補"
        item["action_label"] = "買った方がいい候補"
        item.update(_trade_plan(item["symbol"], item.get("raw_last") or float(item["last"]), item.get("closes") or [], "buy", item.get("points") or []))
        item.pop("closes", None)
        item.pop("points", None)
        item.pop("raw_last", None)
        ideas.append(item)
    for item in sell_raw[:4]:
        item = dict(item)
        item["signal"] = "売り候補"
        item["action_label"] = "売った方がいい候補"
        item.update(_trade_plan(item["symbol"], item.get("raw_last") or float(item["last"]), item.get("closes") or [], "sell", item.get("points") or []))
        item.pop("closes", None)
        item.pop("points", None)
        item.pop("raw_last", None)
        ideas.append(item)

    # 買い候補→売り候補の順に整える。
    return sorted(ideas, key=lambda x: (0 if x.get("signal") == "買い候補" else 1, -x.get("score", 0)))


def format_stock_signals(ideas: list[dict[str, Any]]) -> str:
    lines = [
        "# 株式シグナル",
        "",
        "※これは教育目的の機械的シグナルであり、個別の投資助言・売買指示ではありません。最終判断は決算、開示資料、リスク許容度を確認して行ってください。",
        "",
    ]

    buy = [i for i in ideas if i.get("signal") == "買い候補"]
    sell = [i for i in ideas if i.get("signal") == "売り候補"]

    if not buy and not sell:
        lines.append("現在、表示条件に合う買い候補・売り候補はありません。価格データ取得先に接続できない場合は、時間を置いて再実行してください。")
        return "\n".join(lines)

    lines.append("## 買った方がいい候補")
    if buy:
        for i in buy:
            lines.append(f"### {i['symbol']} / {i['name']}")
            lines.append(f"- 現在価格: {i.get('last')}")
            lines.append(f"- エントリー価格: {i.get('entry_price')}")
            lines.append(f"- 利確価格: {i.get('take_profit_price')}（+{i.get('take_profit_pct')}%目安）")
            lines.append(f"- 損切価格: {i.get('stop_loss_price')}（-{i.get('stop_loss_pct')}%目安）")
            lines.append(f"- リスクリワード: {i.get('risk_reward')}倍")
            lines.append(f"- 5日変化率: {i.get('change_5d_pct')}%")
            lines.append(f"- 3か月高値からの下落率: {i.get('drawdown_3m_pct')}%")
            lines.append(f"- データ取得元: {i.get('data_source')}")
            lines.append(f"- 理由: {i.get('reason')}")
            lines.append("")
    else:
        lines.append("- 該当なし")
        lines.append("")

    lines.append("## 売った方がいい候補")
    if sell:
        for i in sell:
            lines.append(f"### {i['symbol']} / {i['name']}")
            lines.append(f"- 現在価格: {i.get('last')}")
            lines.append(f"- 売却価格: {i.get('sell_price')}（現在値から+{i.get('sell_price_pct_from_now')}%目安）")
            lines.append(f"- 5日変化率: {i.get('change_5d_pct')}%")
            lines.append(f"- 3か月高値からの下落率: {i.get('drawdown_3m_pct')}%")
            lines.append(f"- データ取得元: {i.get('data_source')}")
            lines.append(f"- 理由: {i.get('reason')}")
            lines.append("")
    else:
        lines.append("- 該当なし")
        lines.append("")

    return "\n".join(lines)



def generate_single_stock_signal(query: str, column: str = "") -> dict[str, Any] | None:
    """検索された単一銘柄のシグナルを返す。買い/売り/様子見を1件表示する。"""
    if not ENABLE_STOCK_SIGNALS:
        return None

    symbol, meta = _meta_for_symbol(query)
    if not symbol:
        return None

    idea = _build_raw_signal(symbol, meta, column or "")
    if idea is None:
        return {
            "symbol": symbol,
            "name": meta.get("name") or symbol,
            "signal": "データ取得失敗",
            "action_label": "データ取得失敗",
            "reason": "価格データを取得できませんでした。ティッカーの入力を確認するか、時間を置いて再実行してください。日本株は 7203 または 7203.T のように入力できます。",
            "notice": "教育目的の機械判定です。個別の投資助言・売買指示ではありません。",
        }

    item = dict(idea)
    score = int(item.get("score", 0))
    raw_last = item.get("raw_last") or float(item["last"])
    closes = item.get("closes") or []
    points = item.get("points") or []

    if score >= 2:
        item["signal"] = "買い候補"
        item["action_label"] = "買った方がいい候補"
        item.update(_trade_plan(item["symbol"], raw_last, closes, "buy", points))
    elif score <= -2:
        item["signal"] = "売り候補"
        item["action_label"] = "売った方がいい候補"
        item.update(_trade_plan(item["symbol"], raw_last, closes, "sell", points))
    else:
        item["signal"] = "様子見"
        item["action_label"] = "様子見"
        item["trade_plan_note"] = "買い・売りの判定が強く出ていないため、現在は様子見として表示。"

    item.pop("closes", None)
    item.pop("points", None)
    item.pop("raw_last", None)
    item["searched_input"] = query
    return item
