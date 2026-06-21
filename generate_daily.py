from app.news import fetch_news
from app.markets import fetch_markets
from app.column import generate_column
from app.points import generate_points
from app.quiz import generate_quiz, parse_quiz_text
from app.storage import save_report


def main():
    news = fetch_news()
    markets = fetch_markets()
    column = generate_column(news, markets)
    points = generate_points(column)
    quiz = generate_quiz(column)
    report = {
        "column": column,
        "points": points,
        "quiz": quiz,
        "quiz_items": parse_quiz_text(quiz),
        "news": news,
        "markets": markets,
    }
    saved = save_report(report)
    print("Saved:", saved)


if __name__ == "__main__":
    main()
