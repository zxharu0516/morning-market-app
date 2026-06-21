import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
MAX_NEWS_ITEMS = int(os.getenv("MAX_NEWS_ITEMS", "35"))
NEWS_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "2"))
MAX_NEWS_PER_SOURCE = int(os.getenv("MAX_NEWS_PER_SOURCE", "8"))
ENABLE_STOCK_SIGNALS = os.getenv("ENABLE_STOCK_SIGNALS", "true").lower() in {"1", "true", "yes", "on"}
REPORTS_DIR = os.getenv("REPORTS_DIR", "reports")

APP_OPERATOR_NAME = os.getenv("APP_OPERATOR_NAME", "[運営者名]").strip() or "[運営者名]"
APP_CONTACT_EMAIL = os.getenv("APP_CONTACT_EMAIL", "[連絡先メールアドレス]").strip() or "[連絡先メールアドレス]"
