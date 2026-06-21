from dataclasses import dataclass
from urllib.parse import quote_plus
from app.config import NEWS_LOOKBACK_DAYS


@dataclass(frozen=True)
class NewsSource:
    name: str
    url: str
    category: str
    note: str = ""


def google_news_search(query: str) -> str:
    """Google News RSS経由で、特定媒体の公開見出しを取得する。本文スクレイピングはしない。"""
    q = f"({query}) when:{NEWS_LOOKBACK_DAYS}d"
    return "https://news.google.com/rss/search?q=" + quote_plus(q) + "&hl=ja&gl=JP&ceid=JP:ja"


# 重要:
# - Bloomberg/FT/Reuters/日経/WSJの記事本文を無断取得する設計ではありません。
# - 公開RSS/Google News RSSの「見出し・短いスニペット・リンク」だけを使います。
# - 商用利用・再配信をする場合は各社の利用規約やニュースAPI契約を確認してください。
SOURCES = [
    NewsSource(
        "Reuters",
        google_news_search("site:reuters.com markets OR business OR economy OR stocks OR bonds OR currencies"),
        "global",
        "Google News RSSからReutersの公開見出しを取得",
    ),
    NewsSource(
        "Bloomberg",
        google_news_search("site:bloomberg.com markets OR economics OR stocks OR bonds OR currencies OR central banks"),
        "global",
        "Google News RSSからBloombergの公開見出しを取得",
    ),
    NewsSource(
        "Financial Times",
        google_news_search("site:ft.com markets OR economy OR companies OR central banks OR stocks"),
        "global",
        "Google News RSSからFTの公開見出しを取得",
    ),
    NewsSource(
        "Wall Street Journal",
        google_news_search("site:wsj.com markets OR economy OR business OR stocks OR bonds"),
        "global",
        "Google News RSSからWSJの公開見出しを取得",
    ),
    NewsSource(
        "Nikkei",
        google_news_search("site:nikkei.com 株 OR 為替 OR 金利 OR 日経平均 OR 経済 OR 金融"),
        "japan",
        "Google News RSSから日本経済新聞の公開見出しを取得",
    ),
    NewsSource(
        "Nikkei Asia",
        "https://asia.nikkei.com/rss/feed/nar",
        "asia",
        "Nikkei Asia公開RSS",
    ),
    NewsSource(
        "WSJ Markets RSS",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "markets",
        "WSJ公開RSS",
    ),
    NewsSource(
        "Reuters Agency Business",
        "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
        "global",
        "Reuters Agency公開フィード",
    ),
]
