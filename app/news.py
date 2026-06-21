from datetime import datetime, timezone
from typing import Any
import html
import re
import feedparser
from urllib.request import Request, urlopen
from app.config import MAX_NEWS_ITEMS, MAX_NEWS_PER_SOURCE
from app.news_sources import SOURCES


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def clean_text(value: str, limit: int = 500) -> str:
    value = html.unescape(value or "")
    value = _TAG_RE.sub(" ", value)
    value = _SPACE_RE.sub(" ", value).strip()
    return value[:limit]


def normalize_title(title: str) -> str:
    title = clean_text(title, 300).lower()
    title = re.sub(r"\s+-\s+(reuters|bloomberg|financial times|wall street journal|the wall street journal|日本経済新聞|nikkei).*", "", title)
    return title.strip()


def _parse_feed_with_timeout(url: str, timeout: int = 6):
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            "Accept": "application/rss+xml,application/xml,text/xml,*/*",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Connection": "close",
        },
    )
    with urlopen(req, timeout=timeout) as res:
        data = res.read()
    return feedparser.parse(data)


def fetch_news() -> list[dict[str, Any]]:
    """公開RSS/Google News RSSから見出し・リンク・短いスニペットだけ取得する。

    取得が長引くとブラウザ上では「生成されない」ように見えるため、
    各RSSは短めのタイムアウトで処理します。
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source in SOURCES:
        try:
            feed = _parse_feed_with_timeout(source.url)
            if getattr(feed, "bozo", 0) and not getattr(feed, "entries", []):
                raise ValueError(str(getattr(feed, "bozo_exception", "RSS parse error")))

            for entry in feed.entries[:MAX_NEWS_PER_SOURCE]:
                title = clean_text(getattr(entry, "title", ""), 250)
                link = getattr(entry, "link", "").strip()
                if not title or not link:
                    continue

                key = normalize_title(title)
                if not key or key in seen:
                    continue
                seen.add(key)

                published = getattr(entry, "published", "") or getattr(entry, "updated", "")
                summary = clean_text(getattr(entry, "summary", "") or getattr(entry, "description", ""), 500)

                items.append({
                    "source": source.name,
                    "category": source.category,
                    "title": title,
                    "link": link,
                    "published": published,
                    "summary": summary,
                    "note": source.note,
                })
        except Exception as exc:
            items.append({
                "source": source.name,
                "category": source.category,
                "title": f"取得失敗: {source.name}",
                "link": "",
                "published": datetime.now(timezone.utc).isoformat(),
                "summary": str(exc),
                "note": source.note,
            })

    return items[:MAX_NEWS_ITEMS]
