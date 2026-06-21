from datetime import datetime
from app.config import OPENAI_API_KEY, OPENAI_MODEL


def _market_text(markets: list[dict]) -> str:
    lines = []
    for m in markets:
        if m.get("last") is None:
            lines.append(f"- {m.get('name')}: 取得失敗")
        else:
            lines.append(f"- {m.get('name')}: {m.get('last')} ({m.get('change_pct')}%)")
    return "\n".join(lines)


def _news_text(news: list[dict]) -> str:
    lines = []
    for n in news:
        summary = n.get("summary") or ""
        if summary:
            lines.append(f"- [{n.get('source')}] {n.get('title')} / 要約: {summary} / URL: {n.get('link')}")
        else:
            lines.append(f"- [{n.get('source')}] {n.get('title')} / URL: {n.get('link')}")
    return "\n".join(lines)


def build_prompt(news: list[dict], markets: list[dict]) -> str:
    return f"""
あなたは日本の個人投資家向けに、毎朝読める金融コラムを書く編集者です。
元外資系金融のプロが一般投資家に説明するように、切れ味はありつつ、初心者にもわかりやすく書いてください。
ただし、特定の実在人物の文体をそのまま模倣せず、オリジナルの表現にしてください。

重要ルール:
- 入力された「見出し・短いスニペット・市場データ」だけを材料にする。
- 記事本文を読んだように断定しない。
- 出典媒体名を適度に示す。
- 投資助言ではなく、教育目的の市場解説として書く。
- 買い煽り・売り煽りをしない。
- 最後に「投資判断は自己責任で、一次情報とリスクを確認」と入れる。

今日の日付: {datetime.now().strftime('%Y年%m月%d日')}

市場データ:
{_market_text(markets)}

ニュース見出し:
{_news_text(news)}

出力形式:
# 今日の金融コラム
## 1. 今日の市場テーマ
## 2. 世界のニュース整理
## 3. 金利・為替・株式への影響
## 4. 日本株・米国株で見るべきポイント
## 5. 個人投資家への示唆
## 6. 今日の一言
""".strip()


def fallback_column(news: list[dict], markets: list[dict]) -> str:
    top_news = [n for n in news if not str(n.get("title", "")).startswith("取得失敗")][:8]
    market_lines = []
    for m in markets:
        if m.get("last") is not None:
            sign = "上昇" if (m.get("change_pct") or 0) >= 0 else "下落"
            market_lines.append(f"{m['name']}は{m['change_pct']}%の{sign}")

    news_lines = "\n".join([f"- {n['source']}: {n['title']}" for n in top_news]) or "- ニュース取得に失敗しました。"
    market_summary = "、".join(market_lines[:8]) or "主要市場データの取得に失敗しました。"

    return f"""# 今日の金融コラム

## 1. 今日の市場テーマ
今日の市場を見るうえで大切なのは、ニュースの細部よりも、投資家が何をリスクとして見ているかです。足元の市場データでは、{market_summary}となっています。

## 2. 世界のニュース整理
取得できた主な見出しは以下です。
{news_lines}

## 3. 金利・為替・株式への影響
金利が上がる局面では、将来利益への期待で買われやすい成長株のバリュエーションに圧力がかかりやすくなります。逆に金利が落ち着けば、ハイテク株やグロース株には追い風になりやすいです。ドル円は日本株の輸出企業や外貨建て資産の評価額にも影響します。

## 4. 日本株・米国株で見るべきポイント
個別銘柄だけを見る前に、まずは「米国金利」「ドル円」「原油」「NASDAQ」「日経平均」の方向感を確認することが重要です。ニュースが良く見えても、金利や為替が逆風なら株価は素直に上がらないことがあります。

## 5. 個人投資家への示唆
今日の見出しは、相場のテーマを把握する入口です。記事本文や企業決算、公式資料を確認したうえで、短期の値動きと長期の成長ストーリーを分けて考える必要があります。

## 6. 今日の一言
ニュースは「当てるため」ではなく、「市場が何に反応しているか」を理解するために読むものです。投資判断は自己責任で、一次情報とリスクを必ず確認してください。
"""


def generate_column(news: list[dict], markets: list[dict]) -> str:
    if not OPENAI_API_KEY:
        return fallback_column(news, markets)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        prompt = build_prompt(news, markets)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "あなたは金融教育に強い日本語コラム編集者です。断定しすぎず、初心者にもわかりやすく、かつ読み応えのある文章を書きます。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.65,
        )
        return response.choices[0].message.content or "生成に失敗しました。"
    except Exception as exc:
        return fallback_column(news, markets) + f"\n\n---\nOpenAI APIでの生成に失敗したため、テンプレート版を表示しています。理由: {exc}\n"
