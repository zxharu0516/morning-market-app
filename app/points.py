from app.config import OPENAI_API_KEY, OPENAI_MODEL


def build_points_prompt(column: str) -> str:
    return f"""
以下の金融コラムを読んだ人が、確認問題に進む前に要点をつかめるように、重要ポイントを日本語で3つだけ作ってください。

条件:
- 3つだけ。
- 1つにつき1〜2文で短くわかりやすく。
- 初心者にも理解できる表現にする。
- 投資判断ではなく、金融ニュース理解・市場理解を助ける内容にする。
- 絵文字を各ポイントの先頭に1つ入れる。

金融コラム:
{column}

出力形式:
# 重要ポイント3つ
1. 📌 **ポイント名**：説明文
2. 📌 **ポイント名**：説明文
3. 📌 **ポイント名**：説明文
""".strip()


def fallback_points(column: str) -> str:
    text = column or ""

    # コラム内に含まれやすいテーマから、確認問題前に読みやすい3点を作る。
    point1 = "📌 **市場テーマを先に見る**：個別銘柄より先に、今日は市場が金利・為替・ニュースのどれに反応しているかを確認することが大切です。"

    if "金利" in text or "NASDAQ" in text or "成長株" in text or "グロース" in text:
        point2 = "💰 **金利は株価の重しになりやすい**：金利が上がると、将来の成長期待で買われるハイテク株や成長株は評価が下がりやすくなります。"
    else:
        point2 = "💰 **市場データはニュースの受け止め方を変える**：同じニュースでも、金利・株価指数・原油・金などの動きによって市場の反応は変わります。"

    if "為替" in text or "ドル円" in text or "日本株" in text:
        point3 = "🌏 **為替は日本株にも影響する**：ドル円の動きは輸出企業の収益や外貨建て資産の評価額に関わるため、日本株を見るときも重要です。"
    else:
        point3 = "🧭 **見出しだけで判断しない**：ニュースは相場を理解する入口なので、実際の投資判断では決算・公式資料・リスク確認が必要です。"

    return "# 重要ポイント3つ\n" + "\n".join([f"1. {point1}", f"2. {point2}", f"3. {point3}"])


def generate_points(column: str) -> str:
    if not OPENAI_API_KEY:
        return fallback_points(column)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "あなたは金融教育アプリの要点整理を作る編集者です。短く、読みやすく、初心者にやさしくまとめます。"},
                {"role": "user", "content": build_points_prompt(column)},
            ],
            temperature=0.35,
        )
        return response.choices[0].message.content or fallback_points(column)
    except Exception as exc:
        return fallback_points(column) + f"\n\n---\nOpenAI APIでの重要ポイント生成に失敗したため、テンプレート版を表示しています。理由: {exc}\n"
