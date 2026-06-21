from app.config import OPENAI_API_KEY, OPENAI_MODEL


def build_quiz_prompt(column: str) -> str:
    return f"""
以下の金融コラムを読んだ人が内容を復習できるように、日本語で10問の確認問題を作ってください。

条件:
- 問題は4択形式。
- 各問に「正解」と「解説」を付ける。
- 投資判断ではなく、金融ニュース理解・用語理解を中心にする。
- 初心者でも学べる難易度にする。

金融コラム:
{column}

出力形式:
# 確認問題10問
## 問1. ...
A. ...
B. ...
C. ...
D. ...
正解: ...
解説: ...
""".strip()


def fallback_quiz(column: str) -> str:
    return """# 確認問題10問

## 問1. 金利上昇時に一般的に圧力を受けやすい株はどれか。
A. 成長株
B. 現金
C. 普通預金
D. 短期国債
正解: A
解説: 成長株は将来利益への期待で買われるため、金利上昇で現在価値が下がりやすいです。

## 問2. ドル円が日本株に影響する主な理由はどれか。
A. 輸出企業の収益や外貨建て資産に影響するため
B. 日本の消費税率が変わるため
C. 株式市場が休場になるため
D. 企業の株式数が自動的に増えるため
正解: A
解説: 為替は輸出企業の円換算収益や投資家のリスク選好に影響します。

## 問3. 原油価格の上昇が市場で注目される理由はどれか。
A. インフレや企業コストに影響しやすいため
B. 必ず株価を上げるため
C. 為替が固定されるため
D. 金利がゼロになるため
正解: A
解説: エネルギー価格は物価や企業コスト、消費者心理に影響します。

## 問4. ニュース見出しだけで投資判断を断定してはいけない理由はどれか。
A. 本文・決算・公式資料を確認しないと背景が不十分だから
B. 見出しは必ず嘘だから
C. 株価はニュースと無関係だから
D. 市場は毎日休みだから
正解: A
解説: 見出しは入口であり、投資判断には一次情報の確認が必要です。

## 問5. NASDAQが特に影響を受けやすい要素として適切なのはどれか。
A. 米国金利
B. 日本の梅雨入り
C. 郵便料金
D. 高校野球の結果
正解: A
解説: NASDAQはハイテク・成長株の比率が高く、金利変動の影響を受けやすいです。

## 問6. 「市場テーマ」を見る目的として正しいものはどれか。
A. 投資家が何に反応しているかを理解するため
B. 必ず明日の株価を当てるため
C. ニュースを読まないため
D. 企業決算を無視するため
正解: A
解説: 市場テーマを把握すると、ニュースと値動きの関係が見えやすくなります。

## 問7. 個別株を見る前に確認したいマクロ要因として不適切なのはどれか。
A. 金利
B. 為替
C. 原油
D. 好きな服の色
正解: D
解説: 金利・為替・資源価格は株式市場に影響しますが、服の色は関係ありません。

## 問8. 「投資判断は自己責任」と書く理由はどれか。
A. 投資には損失リスクがあり、最終判断は本人が行う必要があるから
B. 株式投資は絶対に儲かるから
C. ニュースは不要だから
D. 証券口座が不要だから
正解: A
解説: 投資には価格変動リスクがあり、判断には自分で情報確認する姿勢が必要です。

## 問9. 株価が良いニュースに反応しないことがある理由として適切なのはどれか。
A. 金利・為替・需給など別の要因が逆風になる場合があるから
B. 良いニュースは市場に存在しないから
C. 株価は常に固定だから
D. 投資家はニュースを見ないから
正解: A
解説: 株価は複数要因で動くため、単一ニュースだけでは判断できません。

## 問10. 金融コラムを読む目的として最も適切なのはどれか。
A. 相場が何に反応しているかを理解すること
B. 必ず短期売買で勝つこと
C. すべての銘柄を買うこと
D. 公式資料を読まないこと
正解: A
解説: 金融コラムは市場の構造やテーマを理解する助けになります。
"""


def generate_quiz(column: str) -> str:
    if not OPENAI_API_KEY:
        return fallback_quiz(column)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "あなたは金融教育教材を作る編集者です。"},
                {"role": "user", "content": build_quiz_prompt(column)},
            ],
            temperature=0.4,
        )
        return response.choices[0].message.content or fallback_quiz(column)
    except Exception as exc:
        return fallback_quiz(column) + f"\n\n---\nOpenAI APIでの問題生成に失敗したため、テンプレート問題を表示しています。理由: {exc}\n"


def parse_quiz_text(quiz_text: str) -> list[dict]:
    """Markdown風の4択問題を、ブラウザで押せるクイズ用JSONに変換する。"""
    import re

    text = quiz_text.replace("\r\n", "\n")
    blocks = re.split(r"\n(?=##\s*問\d+\.|問\d+\.)", text)
    questions: list[dict] = []

    for block in blocks:
        if not re.search(r"問\d+", block):
            continue

        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        # 問題文
        q_line = lines[0]
        q_line = re.sub(r"^##\s*", "", q_line)
        q_line = re.sub(r"^問(\d+)\.\s*", r"問\1. ", q_line)

        choices = []
        correct = ""
        explanation = ""
        for line in lines[1:]:
            m = re.match(r"^([ABCD])\.\s*(.+)$", line)
            if m:
                choices.append({"key": m.group(1), "text": m.group(2)})
                continue
            m = re.match(r"^正解[:：]\s*([ABCD])", line)
            if m:
                correct = m.group(1)
                continue
            m = re.match(r"^解説[:：]\s*(.+)$", line)
            if m:
                explanation = m.group(1)
                continue

        if q_line and len(choices) >= 2 and correct:
            questions.append({
                "question": q_line,
                "choices": choices,
                "correct": correct,
                "explanation": explanation,
            })

    return questions[:10]
