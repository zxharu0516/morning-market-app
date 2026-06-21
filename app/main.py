from datetime import datetime
from pathlib import Path
import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import unicodedata
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.news import fetch_news
from app.markets import fetch_markets
from app.column import generate_column
from app.points import generate_points
from app.quiz import generate_quiz, parse_quiz_text
from app.storage import save_report
from app.config import APP_OPERATOR_NAME, APP_CONTACT_EMAIL

app = FastAPI(title="Morning Market Column AI Pro")

MEDIA_DIR = Path("media_uploads")
MEDIA_DIR.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

SOCIAL_DATA_PATH = Path("social_posts.json")
USERS_DATA_PATH = Path("social_users.json")
SESSIONS_DATA_PATH = Path("social_sessions.json")
REPORTS_DATA_PATH = Path("social_reports.json")
NOTIFICATIONS_DATA_PATH = Path("social_notifications.json")
SESSION_COOKIE = "mmc_session"
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,24}$")
MEDIA_MAX_FILES = 4
MEDIA_MAX_BYTES = 50 * 1024 * 1024
ALLOWED_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}


class PostCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=280)


class PostUpdate(BaseModel):
    text: str = Field(..., min_length=1, max_length=280)


class CommentCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=180)


class RegisterCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=24)
    display_name: str = Field(..., min_length=1, max_length=30)
    password: str = Field(..., min_length=6, max_length=128)


class LoginCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=24)
    password: str = Field(..., min_length=1, max_length=128)


class ProfileUpdate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=30)
    bio: str = Field("", max_length=160)
    avatar: str = Field("M", max_length=2)


class ReportCreate(BaseModel):
    reason: str = Field(..., min_length=1, max_length=40)
    detail: str = Field("", max_length=240)


class AdminReportStatusUpdate(BaseModel):
    status: str = Field(..., min_length=1, max_length=20)


class AccountDeleteCreate(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)


SAFETY_PATTERNS = [
    (re.compile(r"(死ね|しね|殺す|ころす|消えろ|きえろ)"), "攻撃的・暴力的な表現"),
    (re.compile(r"(絶対儲かる|必ず儲かる|確実に儲かる|元本保証|ノーリスクで稼げる)"), "誤解を招く金融勧誘表現"),
    (re.compile(r"(薬物売買|違法薬物|闇バイト|口座売買|個人情報売ります)"), "違法行為を助長する表現"),
    (re.compile(r"(裸画像|わいせつ画像|児童ポルノ)"), "性的または違法なコンテンツを示す表現"),
]


def _normalize_text_for_safety(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower().replace(" ", "").replace("　", "")


def _check_text_safety(text: str, label: str = "投稿") -> None:
    normalized = _normalize_text_for_safety(text)
    if not normalized:
        return
    for pattern, reason in SAFETY_PATTERNS:
        if pattern.search(normalized):
            raise HTTPException(status_code=400, detail=f"{label}に不適切な表現が含まれる可能性があります：{reason}")


def _clean_user_text(text: str, max_len: int) -> str:
    return str(text or "").strip()[:max_len]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return data


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    following = user.get("following", []) if isinstance(user.get("following"), list) else []
    blocked_users = user.get("blocked_users", []) if isinstance(user.get("blocked_users"), list) else []
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "display_name": user.get("display_name") or user.get("username"),
        "bio": user.get("bio", ""),
        "avatar": (user.get("avatar") or "M")[:2],
        "created_at": user.get("created_at"),
        "is_admin": bool(user.get("is_admin")),
        "following_count": len(set(following)),
        "blocked_count": len(set(blocked_users)),
    }


def _load_users() -> list[dict]:
    data = _read_json(USERS_DATA_PATH, [])
    users = data if isinstance(data, list) else data.get("users", [])
    if not isinstance(users, list):
        return []
    for user in users:
        if isinstance(user, dict):
            if not isinstance(user.get("following"), list):
                user["following"] = []
            if not isinstance(user.get("blocked_users"), list):
                user["blocked_users"] = []
            user["is_admin"] = bool(user.get("is_admin"))
    return users


def _save_users(users: list[dict]) -> None:
    _write_json(USERS_DATA_PATH, users)


def _find_user_by_username(users: list[dict], username: str) -> dict | None:
    key = username.strip().lower()
    for user in users:
        if str(user.get("username", "")).lower() == key:
            return user
    return None


def _find_user_by_id(users: list[dict], user_id: str | None) -> dict | None:
    if not user_id:
        return None
    for user in users:
        if user.get("id") == user_id:
            return user
    return None


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 160_000)
    return salt, digest.hex()


def _verify_password(password: str, user: dict) -> bool:
    salt = user.get("salt")
    expected = user.get("password_hash")
    if not salt or not expected:
        return False
    _, actual = _hash_password(password, salt)
    return hmac.compare_digest(actual, expected)


def _load_sessions() -> dict:
    data = _read_json(SESSIONS_DATA_PATH, {})
    return data if isinstance(data, dict) else {}


def _save_sessions(sessions: dict) -> None:
    _write_json(SESSIONS_DATA_PATH, sessions)


def _current_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sessions = _load_sessions()
    session = sessions.get(token)
    if not isinstance(session, dict):
        return None
    return _find_user_by_id(_load_users(), session.get("user_id"))


def _require_user(request: Request) -> dict:
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="ログインが必要です")
    return user


def _is_admin(user: dict | None) -> bool:
    return bool(user and user.get("is_admin"))


def _require_admin(request: Request) -> dict:
    user = _require_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="管理者権限が必要です")
    return user


def _public_user_with_social(user: dict, current_user: dict | None, users: list[dict]) -> dict:
    view = _public_user(user) or {}
    uid = user.get("id")
    following = user.get("following", []) if isinstance(user.get("following"), list) else []
    follower_count = sum(1 for u in users if uid in (u.get("following", []) if isinstance(u.get("following"), list) else []))
    current_following = current_user.get("following", []) if current_user and isinstance(current_user.get("following"), list) else []
    current_blocked = current_user.get("blocked_users", []) if current_user and isinstance(current_user.get("blocked_users"), list) else []
    target_blocked = user.get("blocked_users", []) if isinstance(user.get("blocked_users"), list) else []
    view.update({
        "follower_count": follower_count,
        "following_count": len(set(following)),
        "is_following": bool(current_user and uid in current_following),
        "is_me": bool(current_user and uid == current_user.get("id")),
        "is_blocked_by_me": bool(current_user and uid in current_blocked),
        "blocks_me": bool(current_user and current_user.get("id") in target_blocked),
    })
    return view


def _load_notifications() -> list[dict]:
    data = _read_json(NOTIFICATIONS_DATA_PATH, [])
    notifications = data if isinstance(data, list) else data.get("notifications", [])
    return notifications if isinstance(notifications, list) else []


def _save_notifications(notifications: list[dict]) -> None:
    _write_json(NOTIFICATIONS_DATA_PATH, notifications)


def _add_notification(user_id: str | None, kind: str, message: str, actor_id: str | None = None, post_id: str | None = None) -> None:
    if not user_id or (actor_id and actor_id == user_id):
        return
    notifications = _load_notifications()
    notifications.insert(0, {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "actor_id": actor_id,
        "post_id": post_id,
        "kind": kind,
        "message": message[:180],
        "created_at": _now_iso(),
        "read": False,
    })
    _save_notifications(notifications[:500])


def _is_blocked_between(current_user: dict | None, other_user_id: str | None, users: list[dict]) -> bool:
    if not current_user or not other_user_id:
        return False
    current_id = current_user.get("id")
    if not current_id or current_id == other_user_id:
        return False
    current_blocked = current_user.get("blocked_users", []) if isinstance(current_user.get("blocked_users"), list) else []
    if other_user_id in current_blocked:
        return True
    other = _find_user_by_id(users, other_user_id)
    other_blocked = other.get("blocked_users", []) if other and isinstance(other.get("blocked_users"), list) else []
    return current_id in other_blocked


def _ensure_can_interact(current_user: dict, target_user_id: str | None, users: list[dict]) -> None:
    if _is_blocked_between(current_user, target_user_id, users):
        raise HTTPException(status_code=403, detail="ブロック中または相手からブロックされているため操作できません")


def _normalize_comment(comment: dict, users: list[dict]) -> dict:
    user = _find_user_by_id(users, comment.get("user_id"))
    if user:
        author = _public_user(user)
    else:
        author = {
            "id": comment.get("user_id"),
            "username": comment.get("username") or "guest",
            "display_name": comment.get("display_name") or "Guest",
            "avatar": comment.get("avatar") or "G",
        }
    return {
        "id": comment.get("id") or str(uuid.uuid4()),
        "text": str(comment.get("text", ""))[:180],
        "created_at": comment.get("created_at") or _now_iso(),
        "user_id": author.get("id"),
        "author": author,
    }


def _load_posts() -> list[dict]:
    raw = _read_json(SOCIAL_DATA_PATH, [])
    posts = raw if isinstance(raw, list) else raw.get("posts", [])
    if not isinstance(posts, list):
        return []
    users = _load_users()
    normalized = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        post.setdefault("id", str(uuid.uuid4()))
        post.setdefault("text", "")
        post.setdefault("created_at", _now_iso())
        if "liked_by" not in post:
            post["liked_by"] = []
        if "saved_by" not in post:
            post["saved_by"] = []
        if "reports" not in post:
            post["reports"] = []
        if not isinstance(post.get("liked_by"), list):
            post["liked_by"] = []
        if not isinstance(post.get("saved_by"), list):
            post["saved_by"] = []
        if not isinstance(post.get("reports"), list):
            post["reports"] = []
        if not isinstance(post.get("comments"), list):
            post["comments"] = []
        post["media"] = _normalize_media(post.get("media", []))
        if not post.get("user_id"):
            post["user_id"] = None
            post["legacy_author"] = {
                "id": None,
                "username": post.get("username") or "market_user",
                "display_name": post.get("display_name") or "Market User",
                "avatar": post.get("avatar") or "M",
                "bio": "",
            }
        post["comments"] = [_normalize_comment(c, users) for c in post.get("comments", []) if isinstance(c, dict)]
        normalized.append(post)
    return sorted(normalized, key=lambda x: x.get("created_at", ""), reverse=True)


def _save_posts(posts: list[dict]) -> None:
    _write_json(SOCIAL_DATA_PATH, posts)


def _post_view(post: dict, current_user: dict | None, users: list[dict]) -> dict:
    author = _find_user_by_id(users, post.get("user_id"))
    public_author = _public_user(author) or post.get("legacy_author") or {
        "id": None,
        "username": "market_user",
        "display_name": "Market User",
        "avatar": "M",
        "bio": "",
    }
    uid = current_user.get("id") if current_user else None
    liked_by = post.get("liked_by", []) if isinstance(post.get("liked_by"), list) else []
    saved_by = post.get("saved_by", []) if isinstance(post.get("saved_by"), list) else []
    reports = post.get("reports", []) if isinstance(post.get("reports"), list) else []
    return {
        "id": post.get("id"),
        "text": post.get("text", ""),
        "created_at": post.get("created_at"),
        "updated_at": post.get("updated_at"),
        "media": _normalize_media(post.get("media", [])),
        "author": public_author,
        "like_count": len(set(liked_by)),
        "comment_count": len([c for c in post.get("comments", []) if not _is_blocked_between(current_user, c.get("user_id"), users)]),
        "liked": bool(uid and uid in liked_by),
        "saved": bool(uid and uid in saved_by),
        "reported": bool(uid and any(r.get("user_id") == uid for r in reports if isinstance(r, dict))),
        "comments": [c for c in post.get("comments", []) if not _is_blocked_between(current_user, c.get("user_id"), users)],
        "is_mine": bool(uid and uid == post.get("user_id")),
        "can_block_author": bool(current_user and public_author.get("id") and public_author.get("id") != current_user.get("id")),
    }


def _posts_response(posts: list[dict], current_user: dict | None) -> dict:
    users = _load_users()
    visible_posts = []
    for post in posts:
        if _is_blocked_between(current_user, post.get("user_id"), users):
            continue
        visible_posts.append(post)
    return {"posts": [_post_view(post, current_user, users) for post in visible_posts], "me": _public_user(current_user)}


def _find_post(posts: list[dict], post_id: str) -> dict:
    for post in posts:
        if post.get("id") == post_id:
            return post
    raise HTTPException(status_code=404, detail="投稿が見つかりません")


def _load_reports() -> list[dict]:
    data = _read_json(REPORTS_DATA_PATH, [])
    reports = data if isinstance(data, list) else data.get("reports", [])
    return reports if isinstance(reports, list) else []


def _save_reports(reports: list[dict]) -> None:
    _write_json(REPORTS_DATA_PATH, reports)


def _media_type(content_type: str) -> str:
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    return "file"


def _normalize_media(media) -> list[dict]:
    if not isinstance(media, list):
        return []
    normalized = []
    for item in media:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))
        media_type = str(item.get("type", ""))
        if not url or media_type not in {"image", "video"}:
            continue
        normalized.append({
            "id": item.get("id") or str(uuid.uuid4()),
            "type": media_type,
            "url": url,
            "filename": str(item.get("filename", "media"))[:160],
            "content_type": str(item.get("content_type", ""))[:80],
            "size": int(item.get("size", 0) or 0),
        })
    return normalized


async def _save_media_files(files: list[UploadFile]) -> list[dict]:
    valid_files = [f for f in (files or []) if f and f.filename]
    if len(valid_files) > MEDIA_MAX_FILES:
        raise HTTPException(status_code=400, detail=f"画像・動画は最大{MEDIA_MAX_FILES}件までです")

    saved = []
    for upload in valid_files:
        content_type = (upload.content_type or "").lower()
        if content_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(status_code=400, detail="対応形式は JPG / PNG / GIF / WebP / MP4 / WebM / MOV です")
        content = await upload.read()
        if len(content) > MEDIA_MAX_BYTES:
            raise HTTPException(status_code=400, detail="画像・動画は1件50MBまでです")
        ext = ALLOWED_MEDIA_TYPES.get(content_type) or mimetypes.guess_extension(content_type) or Path(upload.filename).suffix.lower() or ".bin"
        safe_name = f"{uuid.uuid4().hex}{ext}"
        path = MEDIA_DIR / safe_name
        path.write_bytes(content)
        saved.append({
            "id": str(uuid.uuid4()),
            "type": _media_type(content_type),
            "url": f"/media/{safe_name}",
            "filename": Path(upload.filename).name[:160],
            "content_type": content_type,
            "size": len(content),
        })
    return saved


def _delete_media_files(media: list[dict]) -> None:
    for item in media or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))
        if not url.startswith("/media/"):
            continue
        name = Path(url).name
        try:
            (MEDIA_DIR / name).unlink(missing_ok=True)
        except Exception:
            pass


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    html = """
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>今日のマーケットを学ぶ</title>
  <style>
    :root { --bg:#f7f7f8; --card:#ffffff; --text:#111827; --muted:#6b7280; --line:#e5e7eb; --main:#111827; --accent:#f59e0b; --accent-bg:#fffbeb; --green:#10b981; --red:#ef4444; }
    * { box-sizing: border-box; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 980px; margin: 0 auto; padding: 24px 16px 98px; line-height: 1.75; background: var(--bg); color: var(--text); }
    h1 { font-size: 30px; margin: 4px 0 10px; letter-spacing: -.03em; }
    h2 { margin: 0 0 12px; font-size: 21px; }
    h3 { margin: 0 0 8px; }
    .hero { background: linear-gradient(135deg, #111827, #374151); color: white; border-radius: 22px; padding: 22px; margin-bottom: 16px; box-shadow: 0 12px 30px rgba(17,24,39,.16); }
    .hero p { margin: 0; color: #d1d5db; }
    .actions { display:flex; flex-wrap:wrap; gap:10px; margin-top: 16px; }
    button { padding: 12px 18px; border: 0; border-radius: 14px; cursor: pointer; font-weight: 900; background: var(--main); color: white; font-size: 15px; touch-action: manipulation; }
    button.secondary { background: #e5e7eb; color: #111827; }
    button.ghost { background: transparent; color: #111827; border: 1px solid var(--line); }
    button.danger { background:#fee2e2; color:#991b1b; }
    button:disabled { opacity: .58; cursor: wait; }
    input, textarea, select { width:100%; border:1px solid var(--line); border-radius:14px; padding:13px 14px; font: inherit; background:#fff; }
    textarea { min-height:120px; resize: vertical; }
    label { display:block; font-weight:900; margin: 12px 0 6px; }
    pre { white-space: pre-wrap; background: #fbfbfb; padding: 16px; border-radius: 14px; border: 1px solid var(--line); overflow-x: auto; margin: 0; }
    .muted { color: var(--muted); font-size: 14px; }
    .grid { display: grid; grid-template-columns: 1fr; gap: 16px; }
    .two { display:grid; grid-template-columns: 1fr 1fr; gap:14px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 18px; box-shadow: 0 1px 2px rgba(0,0,0,.03); }
    .section { display: none; }
    .section.active { display: block; }
    .status { display: none; margin: 14px 0 16px; padding: 14px 15px; border-radius: 15px; background: var(--accent-bg); border: 1px solid #fde68a; color: #92400e; font-weight: 900; }
    .status.show { display: flex; align-items: center; gap: 10px; }
    .status.error { background: #fff0f0; border-color: var(--red); color: #991b1b; }
    .status.done { background: #ecfdf5; border-color: var(--green); color: #065f46; }
    .spinner { width: 17px; height: 17px; flex: 0 0 auto; border: 3px solid rgba(146, 64, 14, .24); border-top-color: #92400e; border-radius: 50%; animation: spin .8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .points-list { display: grid; gap: 12px; }
    .point-item { background: var(--accent-bg); border: 1px solid #fde68a; border-radius: 16px; padding: 14px 16px; font-weight: 800; }
    .point-item strong { color: #92400e; }
    .quiz-card { border: 1px solid var(--line); border-radius: 16px; padding: 16px; margin: 14px 0; background: #fff; box-shadow: 0 1px 0 rgba(0,0,0,.03); }
    .quiz-title { font-weight: 900; margin-bottom: 10px; }
    .choice { display: block; width: 100%; text-align: left; background: #f6f6f6; color: #111; border: 1px solid #e2e2e2; margin: 8px 0; border-radius: 14px; padding: 13px 14px; font-weight: 700; }
    .choice.correct { background: #e9fff0; border-color: var(--green); color: #064e3b; }
    .choice.wrong { background: #fff0f0; border-color: var(--red); color: #7f1d1d; }
    .judge { display: none; margin-top: 12px; padding: 14px; border-radius: 14px; font-weight: 900; }
    .judge.show { display: block; }
    .judge.ok { background: #e9fff0; color: #065f46; border: 1px solid var(--green); }
    .judge.ng { background: #fff0f0; color: #991b1b; border: 1px solid var(--red); }
    .score-box { background: #111; color: white; display: inline-block; border-radius: 999px; padding: 8px 14px; font-weight: 900; margin-bottom: 10px; }
    .bottom-nav { position: fixed; left: 0; right: 0; bottom: 0; background: rgba(255,255,255,.95); backdrop-filter: blur(12px); border-top: 1px solid var(--line); display: grid; grid-template-columns: repeat(3, 1fr); max-width: 980px; margin: 0 auto; padding: 8px 8px 10px; z-index: 10; }
    .nav-btn { background: transparent; color: #6b7280; border-radius: 14px; padding: 10px 4px; font-size: 12px; }
    .nav-btn.active { background: #111827; color: white; }
    .auth-grid { display:grid; grid-template-columns: 1fr 1fr; gap:14px; }
    .login-banner { background:#eff6ff; border:1px solid #bfdbfe; padding:13px; border-radius:15px; margin: 0 0 14px; font-weight:800; }
    .user-pill { display:inline-flex; align-items:center; gap:8px; background:#f3f4f6; border:1px solid var(--line); padding:8px 11px; border-radius:999px; font-weight:900; }
    .post-toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between; margin: 14px 0; }
    .filter-group { display:flex; gap:8px; flex-wrap:wrap; }
    .filter-btn { background:#e5e7eb; color:#111827; padding:9px 13px; border-radius:999px; font-size:13px; }
    .filter-btn.active { background:#111827; color:white; }
    .post { background: #fff; border: 1px solid var(--line); padding: 15px; border-radius: 18px; margin: 12px 0; box-shadow: 0 1px 2px rgba(0,0,0,.03); }
    .post-head { display:flex; justify-content:space-between; gap:10px; margin-bottom:8px; }
    .author-wrap { display:flex; gap:10px; min-width:0; flex:1; }
    .avatar { width:42px; height:42px; border-radius:999px; display:grid; place-items:center; background:#111827; color:white; font-weight:900; flex:0 0 auto; }
    .post-main { flex:1; min-width:0; }
    .post-name { font-weight:900; line-height:1.2; }
    .post-handle { color:var(--muted); font-size:12px; }
    .post-time { color:var(--muted); font-size:12px; }
    .post-text { white-space:pre-wrap; font-size:16px; margin: 10px 0 12px; }
    .media-input-wrap { margin-top: 10px; }
    .media-hint { font-size: 13px; color: var(--muted); margin-top: 6px; }
    .selected-media { margin-top: 8px; display: grid; gap: 6px; }
    .selected-media-item { background:#f9fafb; border:1px solid var(--line); border-radius:12px; padding:8px 10px; font-size:13px; color:#374151; }
    .post-media-grid { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:8px; margin: 10px 0 12px; }
    .post-media-grid.one { grid-template-columns: 1fr; }
    .post-media { width:100%; max-height:420px; object-fit:cover; border-radius:16px; border:1px solid var(--line); background:#111827; display:block; }
    video.post-media { object-fit:contain; }
    .post-actions-row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
    .owner-actions { display:flex; flex-wrap:wrap; gap:8px; margin: 8px 0 0; }
    .owner-btn { background:#fff7ed; color:#9a3412; border:1px solid #fed7aa; padding:8px 11px; border-radius:999px; font-size:13px; }
    .owner-btn.delete { background:#fee2e2; color:#991b1b; border-color:#fecaca; }
    .social-btn { background:#f3f4f6; color:#111827; padding:9px 12px; border-radius:999px; font-size:13px; }
    .social-btn.active { background:#111827; color:white; }
    .social-btn.reported { background:#fee2e2; color:#991b1b; }
    .comment-list { margin-top:12px; display:grid; gap:8px; }
    .comment { background:#f9fafb; border:1px solid var(--line); border-radius:14px; padding:10px 12px; }
    .comment-top { display:flex; gap:8px; align-items:center; font-size:13px; font-weight:900; margin-bottom:4px; }
    .mini-avatar { width:25px; height:25px; border-radius:999px; display:grid; place-items:center; background:#374151; color:white; font-size:12px; }
    .comment-text { white-space:pre-wrap; }
    .comment-time { color:var(--muted); font-size:12px; margin-top:3px; }
    .comment-form { display:flex; gap:8px; margin-top:12px; }
    .comment-form input { flex:1; min-width:0; border-radius:999px; padding:10px 13px; }
    .comment-form button { padding:10px 13px; border-radius:999px; font-size:13px; }
    .empty { color:var(--muted); text-align:center; padding:22px 10px; }
    .profile-stack { display:grid; gap:14px; }
    .profile-block { border:1px solid var(--line); border-radius:16px; padding:16px; background:#fff; }
    .profile-actions { display:flex; flex-wrap:wrap; gap:10px; margin: 12px 0; }
    .profile-actions button { padding:10px 14px; font-size:14px; }
    .settings-note { background:#f9fafb; border:1px solid var(--line); border-radius:14px; padding:13px; }
    .search-row { display:flex; gap:8px; align-items:center; margin: 10px 0; }
    .search-row input { flex:1; min-width:0; }
    .search-row button { border-radius:999px; padding:10px 14px; font-size:13px; }
    .user-result { display:flex; justify-content:space-between; align-items:center; gap:12px; border:1px solid var(--line); border-radius:15px; padding:12px; margin:8px 0; background:#fff; }
    .user-meta { display:flex; gap:10px; align-items:center; min-width:0; }
    .user-meta-text { min-width:0; }
    .user-stats { color:var(--muted); font-size:12px; }
    .notification { border:1px solid var(--line); border-radius:14px; padding:12px; margin:8px 0; background:#fff; }
    .notification.unread { background:#eff6ff; border-color:#bfdbfe; font-weight:800; }
    .admin-report { border:1px solid var(--line); border-radius:16px; padding:14px; margin:10px 0; background:#fff; }
    .admin-report.open { border-color:#f59e0b; background:#fffbeb; }
    .admin-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
    .admin-actions button { padding:9px 12px; border-radius:999px; font-size:13px; }
    .legal-box { max-height:280px; overflow:auto; background:#f9fafb; border:1px solid var(--line); border-radius:14px; padding:14px; white-space:pre-wrap; }
    details { border:1px solid var(--line); border-radius:14px; padding:12px 14px; background:#fff; }
    summary { cursor:pointer; font-weight:900; }
    @media (max-width: 760px) { .two, .auth-grid { grid-template-columns: 1fr; } body { padding-inline: 12px; } .bottom-nav { grid-template-columns: repeat(3, 1fr); } .nav-btn { font-size:11px; padding-inline:2px; } }
  </style>
</head>
<body>
  <div class="hero">
    <h1>今日のマーケットを学ぶ</h1>
    <p>金融コラム、重要ポイント3つ、確認問題10問で相場を学べます。</p>
    <div class="actions">
      <button id="reportBtn" type="button">AIコラムを生成</button>
      <button id="columnBtn" type="button" class="secondary">コラムだけ生成</button>
    </div>
  </div>

  <div id="status" class="status" aria-live="polite" role="status"></div>

  <main>
    <section id="homeSection" class="section active">
      <div class="grid">
        <div class="card"><h2>今日の金融コラム</h2><pre id="column">ここにコラムが表示されます。</pre></div>
        <div class="card"><h2>重要ポイント3つ</h2><div id="points" class="points-list">ここに重要ポイントが表示されます。</div></div>
        <div class="card"><h2>確認問題10問</h2><div id="quiz">ここに問題が表示されます。</div></div>
      </div>
    </section>

    <section id="postSection" class="section">
      <div class="card">
        <div class="post-toolbar">
          <div>
            <h2>みんなの投稿</h2>
          </div>
          <div id="mePill"></div>
        </div>
        <div id="authBox" class="auth-grid"></div>
        <div id="composerBox">
          <textarea id="postText" maxlength="280" placeholder="今日の相場について思ったことを書いてみよう。例：米国株は金利低下でグロースが強そう。"></textarea>
          <div class="media-input-wrap">
            <input id="postMedia" type="file" accept="image/*,video/*" multiple />
            <div class="media-hint">画像・動画を最大4件まで添付できます。対応：JPG / PNG / GIF / WebP / MP4 / WebM / MOV（1件50MBまで）</div>
            <div class="media-hint">App Store審査向けに、不適切表現・投資詐欺につながる表現は簡易フィルターで制限されます。</div>
            <div id="selectedMedia" class="selected-media"></div>
          </div>
          <div class="post-toolbar">
            <div class="filter-group">
              <button id="allPostsBtn" class="filter-btn active" type="button">すべて</button>
              <button id="myPostsBtn" class="filter-btn" type="button">自分</button>
              <button id="savedPostsBtn" class="filter-btn" type="button">保存済み</button>
            </div>
            <button id="postBtn" type="button">投稿する</button>
          </div>
        </div>
        <div id="postList" aria-live="polite"></div>
      </div>
    </section>

    <section id="profileSection" class="section">
      <div class="card">
        <h2>プロフィール</h2>
        <div id="profileBox"></div>
      </div>
    </section>
  </main>

  <nav class="bottom-nav" aria-label="下メニュー">
    <button class="nav-btn active" type="button" data-tab="home">ホーム</button>
    <button class="nav-btn" type="button" data-tab="post">投稿</button>
    <button class="nav-btn" type="button" data-tab="profile">プロフィール</button>
  </nav>

  <script>

    (() => {
      let quizState = { answered: 0, correct: 0 };
      let currentPostFilter = 'all';
      let currentUser = null;
      const TERMS_TEXT = `【利用規約】
最終更新日：2026年6月20日

本利用規約（以下「本規約」といいます。）は、「今日のマーケットを学ぶ」（以下「本アプリ」といいます。）の利用条件を定めるものです。本アプリを利用した場合、利用者は本規約に同意したものとみなされます。

第1条（本アプリの目的）
本アプリは、金融ニュースや相場に関する学習用コンテンツ、AI生成コラム、重要ポイント、確認問題、投稿機能などを提供する金融学習サービスです。本アプリは学習と情報共有を目的としており、個別銘柄の売買推奨、投資助言、投資運用、金融商品の勧誘を目的とするものではありません。

第2条（会員登録とアカウント管理）
利用者は、登録時に正確な情報を入力するものとします。アカウント情報、ログイン状態、パスワード等の管理は利用者自身の責任で行うものとし、第三者による利用または不正利用により生じた損害について、運営者は責任を負いません。

第3条（投稿・コメント・画像・動画）
利用者は、投稿、コメント、プロフィール、画像、動画その他のコンテンツについて、自ら必要な権利を有していることを保証するものとします。利用者が本アプリに投稿したコンテンツの著作権は利用者に帰属しますが、利用者は運営者に対し、本アプリの表示、配信、保存、管理、改善、通報対応、プロモーションに必要な範囲で、当該コンテンツを無償で利用することを許諾します。

第4条（禁止事項）
利用者は、以下の行為をしてはなりません。
1. 法令または公序良俗に違反する行為
2. 他者への誹謗中傷、脅迫、嫌がらせ、差別的表現、過度に攻撃的な表現
3. 投資詐欺、無登録の投資勧誘、虚偽または著しく誤解を招く金融情報の投稿
4. 相場操縦、風説の流布、インサイダー取引を助長する行為
5. 他者の個人情報、プライバシー、肖像権、著作権、商標権その他の権利を侵害する行為
6. なりすまし、スパム、広告宣伝、外部サービスへの不適切な誘導
7. わいせつ、暴力的、残虐、差別的、違法行為を助長する画像・動画・文章の投稿
8. 本アプリのサーバー、ネットワーク、システムに過度な負荷をかける行為
9. 不正アクセス、リバースエンジニアリング、脆弱性探索、マルウェア送信等の行為
10. その他、運営者が不適切と判断する行為

第5条（通報・削除・利用制限）
運営者は、利用者からの通報、法令違反の疑い、規約違反の疑い、その他安全な運営のために必要と判断した場合、事前の通知なく、投稿・コメント・画像・動画の非表示、削除、アカウントの利用制限、停止、退会処理その他必要な措置を行うことができます。

第6条（金融情報・AI生成コンテンツに関する注意）
本アプリのAIコラム、重要ポイント、確認問題、投稿、コメント、通知、その他の情報は、正確性、完全性、最新性、有用性、特定目的への適合性を保証するものではありません。AI生成コンテンツには誤り、不完全な情報、古い情報が含まれる場合があります。投資判断は利用者自身の責任で行い、必要に応じて公的情報、証券会社、金融機関、専門家等に確認してください。

第7条（外部サービス）
本アプリは、AI生成、データ取得、配信、保存、分析、決済等のために第三者サービスを利用する場合があります。外部サービスの利用には、各サービス提供者の規約およびプライバシーポリシーが適用される場合があります。

第8条（サービス内容の変更・停止）
運営者は、必要に応じて、本アプリの全部または一部の内容を変更、追加、中断、停止、終了することができます。これにより利用者に損害が生じた場合でも、運営者は法令上必要な範囲を除き責任を負いません。

第9条（免責事項）
運営者は、本アプリに事実上または法律上の瑕疵がないことを保証しません。利用者間または利用者と第三者との間で生じたトラブル、投稿内容、投資判断、通信環境、端末環境、外部サービスの障害等により生じた損害について、運営者は法令上必要な範囲を除き責任を負いません。

第10条（損害賠償）
利用者が本規約に違反し、運営者または第三者に損害を与えた場合、利用者はその損害を賠償する責任を負うものとします。

第11条（未成年者の利用）
未成年者が本アプリを利用する場合、親権者など法定代理人の同意を得たうえで利用してください。

第12条（規約の変更）
運営者は、必要に応じて本規約を変更できます。重要な変更がある場合、本アプリ内での表示その他適切な方法により通知します。変更後に本アプリを利用した場合、利用者は変更後の規約に同意したものとみなされます。

第13条（準拠法・管轄）
本規約は日本法に準拠します。本アプリに関して紛争が生じた場合、運営者の所在地を管轄する裁判所を第一審の専属的合意管轄裁判所とします。

第14条（お問い合わせ）
本規約に関するお問い合わせは、以下の連絡先までお願いします。
運営者：__APP_OPERATOR_NAME__
連絡先：__APP_CONTACT_EMAIL__`;
      const PRIVACY_TEXT = `【プライバシーポリシー】
最終更新日：2026年6月20日

「今日のマーケットを学ぶ」（以下「本アプリ」といいます。）の運営者（以下「運営者」といいます。）は、本アプリにおける利用者情報の取扱いについて、以下のとおりプライバシーポリシーを定めます。

第1条（取得する情報）
本アプリは、以下の情報を取得または保存する場合があります。
1. アカウント情報：ユーザー名、表示名、プロフィール、アイコン文字、ログイン情報
2. 投稿情報：投稿本文、コメント、画像、動画、投稿日時、編集日時、削除状態
3. 利用情報：いいね、保存、フォロー、通知、通報、管理者対応履歴
4. 生成情報：AIコラム生成に必要な入力内容、生成結果、保存履歴
5. 技術情報：IPアドレス、ユーザーエージェント、端末情報、OS、ブラウザ、アクセス日時、エラーログ
6. 問い合わせ情報：問い合わせ本文、メールアドレス、対応履歴

第2条（利用目的）
運営者は、取得した情報を以下の目的で利用します。
1. 本アプリの提供、本人確認、ログイン状態の管理
2. 投稿、コメント、画像、動画、フォロー、通知などの機能提供
3. AIコラム、重要ポイント、確認問題などの生成と表示
4. 通報対応、不正利用防止、コミュニティの安全確保
5. 障害調査、品質改善、利用状況の分析、機能改善
6. 利用者からの問い合わせ対応
7. 利用規約違反、法令違反、セキュリティ上の問題への対応
8. 法令または行政機関、裁判所等の要請への対応

第3条（公開される情報）
利用者が投稿した本文、コメント、画像、動画、表示名、ユーザー名、プロフィールの一部は、本アプリ内の他の利用者に表示される場合があります。個人情報や第三者の情報を投稿しないよう注意してください。

第4条（AI処理・外部サービスの利用）
本アプリは、AI生成機能、データ取得、保存、配信、分析、障害管理等のために、OpenAI API、クラウドサーバー、ストレージ、分析ツール、エラー監視ツール等の第三者サービスを利用する場合があります。この場合、必要な範囲で情報が第三者サービスに送信または保存されることがあります。

第5条（第三者提供）
運営者は、以下の場合を除き、利用者の個人情報を第三者に提供しません。
1. 利用者の同意がある場合
2. 本アプリの運営に必要な範囲で外部サービスまたは委託先に提供する場合
3. 法令に基づく場合
4. 人の生命、身体または財産の保護のために必要な場合
5. 不正利用、権利侵害、規約違反への対応に必要な場合
6. 事業譲渡、合併、組織再編等に伴い必要な場合

第6条（広告・トラッキング）
本アプリは、現時点では広告目的で利用者を第三者のアプリやWebサイトを横断して追跡することを目的としたトラッキングを行いません。将来、広告配信やトラッキングを導入する場合は、必要な同意取得、App Store Connectでの申告、プライバシーポリシーの更新を行います。

第7条（保存期間）
運営者は、利用目的の達成に必要な期間、利用者情報を保存します。アカウント削除後も、不正利用防止、通報対応、法令対応、紛争対応のために必要な範囲で、一部の情報を一定期間保存する場合があります。

第8条（削除・訂正・利用停止）
利用者は、自己の登録情報の確認、訂正、削除、利用停止を求めることができます。希望する場合は、本ポリシー末尾の連絡先までお問い合わせください。法令または運営上の必要により、すぐに削除できない情報がある場合があります。

第9条（安全管理）
運営者は、取得した情報の漏えい、滅失、改ざん、不正アクセスを防ぐため、合理的な安全管理措置を講じます。OpenAI APIキーなどの機密情報は、アプリ画面やクライアントコードに表示せず、サーバー側の環境変数等で管理します。

第10条（未成年者の情報）
未成年者が本アプリを利用する場合、親権者など法定代理人の同意を得たうえで利用してください。未成年者の個人情報に関する削除等の相談は、法定代理人からも受け付けます。

第11条（App Storeのプライバシー表示）
本アプリをApp Storeで公開する場合、運営者はApp Store Connectにおいて、本アプリおよび利用する第三者サービスが収集するデータの種類、利用目的、利用者との関連付け、トラッキングの有無等を正確に申告します。

第12条（ポリシーの変更）
運営者は、必要に応じて本ポリシーを変更できます。重要な変更がある場合、本アプリ内での表示その他適切な方法により通知します。

第13条（お問い合わせ）
本ポリシーに関するお問い合わせは、以下の連絡先までお願いします。
運営者：__APP_OPERATOR_NAME__
連絡先：__APP_CONTACT_EMAIL__`;
      const CONTACT_OPERATOR = `__APP_OPERATOR_NAME__`;
      const CONTACT_EMAIL = `__APP_CONTACT_EMAIL__`;
      const $ = (id) => document.getElementById(id);
      const sleepFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

      function setStatus(text, type='loading') {
        const status = $('status');
        if (!status) return;
        status.className = type === 'error' ? 'status show error' : (type === 'done' ? 'status show done' : 'status show');
        status.textContent = '';
        if (type === 'loading') {
          const spinner = document.createElement('span');
          spinner.className = 'spinner';
          status.appendChild(spinner);
        }
        const message = document.createElement('span');
        message.textContent = text;
        status.appendChild(message);
      }

      function clearStatusSoon() {
        setTimeout(() => {
          const status = $('status');
          if (status && status.classList.contains('done')) {
            status.className = 'status';
            status.textContent = '';
          }
        }, 1800);
      }

      function setLoading(isLoading) {
        const reportBtn = $('reportBtn');
        const columnBtn = $('columnBtn');
        if (reportBtn) {
          reportBtn.disabled = isLoading;
          reportBtn.textContent = isLoading ? '生成中…' : 'AIコラムを生成';
        }
        if (columnBtn) columnBtn.disabled = isLoading;
      }

      function setText(id, text) {
        const el = $(id);
        if (el) el.textContent = text;
      }

      function clearNode(node) {
        if (!node) return;
        while (node.firstChild) node.removeChild(node.firstChild);
      }

      function el(tag, attrs={}, children=[]) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => {
          if (v === null || v === undefined) return;
          if (k === 'class') node.className = v;
          else if (k === 'text') node.textContent = v;
          else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
          else node.setAttribute(k, v);
        });
        (Array.isArray(children) ? children : [children]).forEach(child => {
          if (child === null || child === undefined) return;
          node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
        });
        return node;
      }

      function renderInlineMarkdown(text) {
        const fragment = document.createDocumentFragment();
        const parts = String(text || '').split(/(\\*\\*[^*]+\\*\\*)/g);
        parts.forEach(part => {
          if (part.startsWith('**') && part.endsWith('**')) {
            const strong = document.createElement('strong');
            strong.textContent = part.slice(2, -2);
            fragment.appendChild(strong);
          } else {
            fragment.appendChild(document.createTextNode(part));
          }
        });
        return fragment;
      }

      function renderPoints(pointsText) {
        const root = $('points');
        clearNode(root);
        if (!root) return;
        const lines = String(pointsText || '')
          .replace(/\\r\\n/g, '\\n')
          .split('\\n')
          .map(line => line.trim())
          .filter(line => line && !line.startsWith('#'))
          .map(line => line.replace(/^[-・]\\s*/, '').replace(/^\\d+[.)．]\\s*/, ''))
          .filter(Boolean)
          .slice(0, 3);
        if (lines.length === 0) {
          root.textContent = pointsText || '重要ポイントを取得できませんでした。';
          return;
        }
        lines.forEach((line) => {
          const item = document.createElement('div');
          item.className = 'point-item';
          item.appendChild(renderInlineMarkdown(line));
          root.appendChild(item);
        });
      }

      function renderQuiz(items, rawText) {
        const root = $('quiz');
        clearNode(root);
        if (!root) return;
        quizState = { answered: 0, correct: 0 };
        if (!items || items.length === 0) {
          const pre = document.createElement('pre');
          pre.textContent = rawText || '問題を取得できませんでした。';
          root.appendChild(pre);
          return;
        }
        const score = document.createElement('div');
        score.className = 'score-box';
        score.textContent = `0 / ${items.length} 正解`;
        root.appendChild(score);
        items.forEach((item, idx) => {
          const card = document.createElement('div');
          card.className = 'quiz-card';
          const title = document.createElement('div');
          title.className = 'quiz-title';
          title.textContent = item.question || `問${idx + 1}`;
          card.appendChild(title);
          const judge = document.createElement('div');
          judge.className = 'judge';
          (item.choices || []).forEach(choice => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'choice';
            btn.textContent = `${choice.key}. ${choice.text}`;
            btn.addEventListener('click', () => {
              if (card.dataset.answered === 'true') return;
              card.dataset.answered = 'true';
              quizState.answered += 1;
              const ok = choice.key === item.correct;
              if (ok) quizState.correct += 1;
              [...card.querySelectorAll('button.choice')].forEach(b => {
                b.disabled = true;
                if (b.textContent.startsWith(item.correct + '.')) b.classList.add('correct');
              });
              if (!ok) btn.classList.add('wrong');
              judge.className = ok ? 'judge show ok' : 'judge show ng';
              const mark = ok ? '○ 正解！' : '× 不正解';
              judge.textContent = `${mark}　正解：${item.correct}　${item.explanation || ''}`;
              score.textContent = `${quizState.correct} / ${items.length} 正解（回答済み ${quizState.answered}/${items.length}）`;
            });
            card.appendChild(btn);
          });
          card.appendChild(judge);
          root.appendChild(card);
        });
      }

      async function fetchJson(url) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 120000);
        try {
          const res = await fetch(url, { cache: 'no-store', signal: controller.signal, credentials: 'same-origin' });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
          return data;
        } finally {
          clearTimeout(timer);
        }
      }

      async function postJson(url, body=null) {
        const options = { method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store', credentials: 'same-origin' };
        if (body) options.body = JSON.stringify(body);
        const res = await fetch(url, options);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
      }

      async function loadReport(save=false) {
        setLoading(true);
        setStatus('生成中… ニュース取得→市場データ→コラム→重要ポイント→確認問題');
        setText('column', '生成中…');
        renderPoints('生成中…');
        const quizRoot = $('quiz');
        if (quizRoot) quizRoot.textContent = '生成中…';
        await sleepFrame();
        try {
          const data = await fetchJson('/api/report?save=' + (save ? 'true' : 'false') + '&t=' + Date.now());
          setText('column', data.column || 'なし');
          renderPoints(data.points || 'なし');
          renderQuiz(data.quiz_items, data.quiz || 'なし');
          setStatus('生成完了！', 'done');
          clearStatusSoon();
        } catch (err) {
          const message = err && err.name === 'AbortError' ? '生成が長すぎたため中断しました。もう一度押してください。' : `生成に失敗しました：${err.message || err}`;
          setStatus(message, 'error');
          console.error(err);
        } finally {
          setLoading(false);
        }
      }

      async function loadColumn() {
        setLoading(true);
        setStatus('コラム生成中…');
        setText('column', '生成中…');
        renderPoints('生成中…');
        await sleepFrame();
        try {
          const data = await fetchJson('/api/column?t=' + Date.now());
          setText('column', data.column || 'なし');
          renderPoints(data.points || 'なし');
          setStatus('生成完了！', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus(`コラム生成に失敗しました：${err.message || err}`, 'error');
          console.error(err);
        } finally {
          setLoading(false);
        }
      }

      function formatDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return iso;
        return d.toLocaleString('ja-JP', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
      }

      function renderAuthBox() {
        const root = $('authBox');
        clearNode(root);
        if (!root) return;
        if (currentUser) {
          root.style.display = 'none';
          return;
        }
        root.style.display = 'grid';
        const login = el('div', { class:'card' }, [
          el('h3', { text:'ログイン' }),
          el('label', { text:'ユーザー名' }),
          el('input', { id:'loginUsername', autocomplete:'username', placeholder:'例：haruki' }),
          el('label', { text:'パスワード' }),
          el('input', { id:'loginPassword', type:'password', autocomplete:'current-password', placeholder:'6文字以上' }),
          el('div', { class:'actions' }, el('button', { type:'button', id:'loginBtn', text:'ログイン', onclick: loginUser }))
        ]);
        const register = el('div', { class:'card' }, [
          el('h3', { text:'新規登録' }),
          el('label', { text:'ユーザー名（英数字と_のみ）' }),
          el('input', { id:'regUsername', autocomplete:'username', placeholder:'例：market_haru' }),
          el('label', { text:'表示名' }),
          el('input', { id:'regDisplayName', placeholder:'例：はるき' }),
          el('label', { text:'パスワード' }),
          el('input', { id:'regPassword', type:'password', autocomplete:'new-password', placeholder:'6文字以上' }),
          el('div', { class:'actions' }, el('button', { type:'button', id:'registerBtn', text:'登録する', onclick: registerUser }))
        ]);
        root.appendChild(login);
        root.appendChild(register);
      }

      function renderMePill() {
        const root = $('mePill');
        clearNode(root);
        if (!root) return;
        if (!currentUser) {
          root.appendChild(el('span', { class:'user-pill', text:'未ログイン' }));
          return;
        }
        root.appendChild(el('span', { class:'user-pill' }, `${currentUser.avatar || 'M'} ${currentUser.display_name} @${currentUser.username}`));
      }

      function renderProfile() {
        const root = $('profileBox');
        clearNode(root);
        if (!root) return;

        if (!currentUser) {
          root.appendChild(el('div', { class:'login-banner', text:'プロフィール機能にはログインが必要です。投稿ページからログインまたは登録してください。' }));
          root.appendChild(el('div', { class:'profile-actions' }, [
            el('button', { type:'button', class:'secondary', text:'投稿ページでログイン', onclick: () => switchTab('post') })
          ]));
          root.appendChild(renderSafetyBlock());
          root.appendChild(renderContactBlock());
          root.appendChild(renderLegalBlock());
          return;
        }

        const quickButtons = [
          el('button', { type:'button', class:'ghost', text:'自分の投稿', onclick: () => { switchTab('post'); setPostFilter('mine'); } }),
          el('button', { type:'button', class:'ghost', text:'保存済み', onclick: () => { switchTab('post'); setPostFilter('saved'); } }),
          el('button', { type:'button', class:'ghost', text:'通知を更新', onclick: loadNotifications })
        ];
        if (currentUser.is_admin) {
          quickButtons.push(el('button', { type:'button', class:'secondary', text:'管理者画面を更新', onclick: loadAdminReports }));
        }

        const quickActions = el('div', { class:'profile-block' }, [
          el('div', { class:'user-pill' }, `${currentUser.avatar || 'M'} ${currentUser.display_name} @${currentUser.username}${currentUser.is_admin ? '  管理者' : ''}`),
          el('p', { class:'muted', text: currentUser.bio || '自己紹介はまだありません。' }),
          el('div', { class:'user-stats', text:`フォロー ${currentUser.following_count || 0}` }),
          el('div', { class:'profile-actions' }, quickButtons)
        ]);

        const searchBlock = el('div', { class:'profile-block' }, [
          el('h3', { text:'ユーザー検索・フォロー' }),
          el('p', { class:'muted', text:'ユーザー名や表示名で検索してフォローできます。' }),
          el('div', { class:'search-row' }, [
            el('input', { id:'userSearchInput', placeholder:'例：haru / market' }),
            el('button', { type:'button', text:'検索', onclick: searchUsers })
          ]),
          el('div', { id:'userSearchResults' })
        ]);

        const notificationBlock = el('div', { class:'profile-block' }, [
          el('h3', { text:'通知' }),
          el('p', { class:'muted', text:'フォロー、いいね、コメント、通報対応などを表示します。' }),
          el('div', { class:'profile-actions' }, [
            el('button', { type:'button', class:'ghost', text:'通知を読み込む', onclick: loadNotifications }),
            el('button', { type:'button', class:'secondary', text:'すべて既読にする', onclick: markNotificationsRead })
          ]),
          el('div', { id:'notificationList', class:'empty', text:'通知を読み込んでください。' })
        ]);

        const editBlock = el('div', { class:'profile-block' }, [
          el('h3', { text:'プロフィール編集' }),
          el('label', { text:'表示名' }),
          el('input', { id:'profileDisplayName', value: currentUser.display_name || '' }),
          el('label', { text:'アイコン文字' }),
          el('input', { id:'profileAvatar', value: currentUser.avatar || 'M', maxlength:'2', placeholder:'例：は' }),
          el('label', { text:'自己紹介' }),
          el('textarea', { id:'profileBio', maxlength:'160', placeholder:'投資スタイルや興味を書けます。' }, currentUser.bio || ''),
          el('div', { class:'actions' }, [
            el('button', { type:'button', text:'プロフィール保存', onclick: saveProfile })
          ])
        ]);

        const blocks = [quickActions, searchBlock, notificationBlock, editBlock, renderSafetyBlock(), renderContactBlock()];
        if (currentUser.is_admin) blocks.push(renderAdminBlock());
        blocks.push(renderLegalBlock());
        blocks.push(el('div', { class:'profile-block' }, [
          el('h3', { text:'アカウント削除' }),
          el('div', { class:'danger-zone' }, [
            el('p', { class:'muted', text:'アカウントを削除すると、自分の投稿・コメント・いいね・保存・フォロー情報が削除されます。この操作は元に戻せません。' }),
            el('button', { type:'button', class:'danger', text:'アカウントを削除する', onclick: deleteAccount })
          ])
        ]));
        blocks.push(el('div', { class:'profile-block' }, [
          el('h3', { text:'ログアウト' }),
          el('p', { class:'muted', text:'このPCでのログイン状態を解除します。' }),
          el('button', { type:'button', class:'danger', text:'ログアウト', onclick: logoutUser })
        ]));

        root.appendChild(el('div', { class:'profile-stack' }, blocks));
        searchUsers();
        loadNotifications();
        if (currentUser.is_admin) loadAdminReports();
      }

      function renderSafetyBlock() {
        return el('div', { class:'profile-block' }, [
          el('h3', { text:'安全対策' }),
          el('div', { class:'safety-box' }, [
            el('div', { text:'・不適切表現や危険な金融勧誘表現は投稿時に簡易チェックされます。' }),
            el('div', { text:'・不快なユーザーは検索画面や投稿からブロックできます。' }),
            el('div', { text:'・ブロックすると相手の投稿・コメントが表示されにくくなり、フォロー操作も制限されます。' })
          ])
        ]);
      }

      function renderContactBlock() {
        return el('div', { class:'profile-block' }, [
          el('h3', { text:'問い合わせ先' }),
          el('div', { class:'contact-box' }, [
            el('div', { text:'運営者：' + CONTACT_OPERATOR }),
            el('div', { text:'連絡先：' + CONTACT_EMAIL }),
          ])
        ]);
      }

      function renderLegalBlock() {
        return el('div', { class:'profile-block' }, [
          el('h3', { text:'利用規約 / プライバシーポリシー' }),
          el('details', {}, [
            el('summary', { text:'利用規約を表示' }),
            el('div', { class:'legal-box', text: TERMS_TEXT })
          ]),
          el('div', { style:'height:10px' }),
          el('details', {}, [
            el('summary', { text:'プライバシーポリシーを表示' }),
            el('div', { class:'legal-box', text: PRIVACY_TEXT })
          ])
        ]);
      }

      function renderAdminBlock() {
        return el('div', { class:'profile-block' }, [
          el('h3', { text:'管理者画面' }),
          el('p', { class:'muted', text:'通報一覧を確認し、投稿削除や対応済み処理ができます。' }),
          el('div', { class:'profile-actions' }, [
            el('button', { type:'button', class:'secondary', text:'通報一覧を更新', onclick: loadAdminReports })
          ]),
          el('div', { id:'adminReports', class:'empty', text:'通報一覧を読み込んでいます…' })
        ]);
      }

      function syncAuthUi() {
        renderAuthBox();
        renderMePill();
        renderProfile();
        const composer = $('composerBox');
        if (composer) {
          composer.style.display = currentUser ? 'block' : 'none';
        }
      }

      async function loadMe() {
        try {
          const data = await fetchJson('/api/me?t=' + Date.now());
          currentUser = data.user || null;
        } catch (_) {
          currentUser = null;
        }
        syncAuthUi();
      }

      async function registerUser() {
        const username = ($('regUsername')?.value || '').trim();
        const display_name = ($('regDisplayName')?.value || '').trim();
        const password = $('regPassword')?.value || '';
        try {
          const data = await postJson('/api/register', { username, display_name, password });
          currentUser = data.user;
          syncAuthUi();
          setStatus('登録してログインしました！', 'done');
          clearStatusSoon();
          loadPosts();
        } catch (err) {
          setStatus('登録に失敗しました：' + (err.message || err), 'error');
        }
      }

      async function loginUser() {
        const username = ($('loginUsername')?.value || '').trim();
        const password = $('loginPassword')?.value || '';
        try {
          const data = await postJson('/api/login', { username, password });
          currentUser = data.user;
          syncAuthUi();
          setStatus('ログインしました！', 'done');
          clearStatusSoon();
          loadPosts();
        } catch (err) {
          setStatus('ログインに失敗しました：' + (err.message || err), 'error');
        }
      }

      async function deleteAccount() {
        if (!currentUser) return;
        if (!confirm('本当にアカウントを削除しますか？この操作は元に戻せません。')) return;
        const password = prompt('本人確認のため、パスワードを入力してください');
        if (!password) return;
        try {
          await postJson('/api/account/delete', { password });
          currentUser = null;
          syncAuthUi();
          await loadPosts();
          setStatus('アカウントを削除しました。', 'done');
          clearStatusSoon();
          switchTab('home');
        } catch (err) {
          setStatus('アカウント削除に失敗しました：' + (err.message || err), 'error');
        }
      }

      async function logoutUser() {
        try { await postJson('/api/logout'); } catch (_) {}
        currentUser = null;
        syncAuthUi();
        loadPosts();
        setStatus('ログアウトしました。', 'done');
        clearStatusSoon();
      }

      async function saveProfile() {
        const display_name = ($('profileDisplayName')?.value || '').trim();
        const avatar = ($('profileAvatar')?.value || 'M').trim().slice(0, 2);
        const bio = ($('profileBio')?.value || '').trim();
        try {
          const data = await postJson('/api/profile', { display_name, avatar, bio });
          currentUser = data.user;
          syncAuthUi();
          setStatus('プロフィールを保存しました！', 'done');
          clearStatusSoon();
          loadPosts();
        } catch (err) {
          setStatus('プロフィール保存に失敗しました：' + (err.message || err), 'error');
        }
      }

      function renderUserResults(users) {
        const root = $('userSearchResults');
        clearNode(root);
        if (!root) return;
        if (!users || users.length === 0) {
          root.appendChild(el('div', { class:'empty', text:'ユーザーが見つかりません。' }));
          return;
        }
        users.forEach(user => {
          const isBlocked = !!user.is_blocked_by_me;
          const cannotFollow = user.is_me || isBlocked || user.blocks_me;
          const followText = user.is_me ? '自分' : (user.blocks_me ? '相手がブロック中' : (isBlocked ? 'ブロック中' : (user.is_following ? 'フォロー中' : 'フォロー')));
          const buttonClass = user.is_following ? 'secondary' : 'ghost';
          const blockText = isBlocked ? 'ブロック解除' : 'ブロック';
          const row = el('div', { class:'user-result' }, [
            el('div', { class:'user-meta' }, [
              el('span', { class:'avatar', text:user.avatar || 'M' }),
              el('div', { class:'user-meta-text' }, [
                el('div', { class:'post-name', text:`${user.display_name || user.username}${user.is_admin ? ' 管理者' : ''}` }),
                el('div', { class:'post-handle', text:'@' + (user.username || 'user') }),
                el('div', { class:'user-stats', text:`フォロワー ${user.follower_count || 0} / フォロー ${user.following_count || 0}${isBlocked ? ' / ブロック中' : ''}` })
              ])
            ]),
            el('div', { class:'profile-actions' }, [
              el('button', { type:'button', class: buttonClass, disabled: cannotFollow ? 'disabled' : null, text:followText, onclick: () => toggleFollow(user.id) }),
              el('button', { type:'button', class: isBlocked ? 'secondary' : 'danger', disabled: user.is_me ? 'disabled' : null, text:blockText, onclick: () => toggleBlock(user.id) })
            ])
          ]);
          root.appendChild(row);
        });
      }

      async function searchUsers() {
        if (!currentUser) return;
        const input = $('userSearchInput');
        const q = encodeURIComponent((input?.value || '').trim());
        const root = $('userSearchResults');
        if (root) root.textContent = 'ユーザーを読み込み中…';
        try {
          const data = await fetchJson('/api/users/search?q=' + q + '&t=' + Date.now());
          renderUserResults(data.users || []);
        } catch (err) {
          if (root) root.textContent = 'ユーザー検索に失敗しました：' + (err.message || err);
        }
      }

      async function toggleBlock(userId) {
        if (!currentUser) {
          setStatus('ブロックにはログインが必要です。', 'error');
          return;
        }
        try {
          const data = await postJson(`/api/users/${userId}/block`);
          currentUser = data.me || currentUser;
          syncAuthUi();
          await loadPosts();
          setStatus(data.blocked ? 'ユーザーをブロックしました。' : 'ブロックを解除しました。', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('ブロック操作に失敗しました：' + (err.message || err), 'error');
        }
      }

      async function toggleFollow(userId) {
        if (!currentUser) {
          setStatus('フォローにはログインが必要です。', 'error');
          return;
        }
        try {
          await postJson(`/api/users/${userId}/follow`);
          await loadMe();
          await searchUsers();
          setStatus('フォロー状態を更新しました。', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('フォロー操作に失敗しました：' + (err.message || err), 'error');
        }
      }

      function renderNotifications(notifications) {
        const root = $('notificationList');
        clearNode(root);
        if (!root) return;
        root.className = '';
        if (!notifications || notifications.length === 0) {
          root.className = 'empty';
          root.textContent = '通知はまだありません。';
          return;
        }
        notifications.forEach(n => {
          root.appendChild(el('div', { class: n.read ? 'notification' : 'notification unread' }, [
            el('div', { text:n.message || '通知があります。' }),
            el('div', { class:'comment-time', text:formatDate(n.created_at) })
          ]));
        });
      }

      async function loadNotifications() {
        if (!currentUser) return;
        const root = $('notificationList');
        if (root) root.textContent = '通知を読み込み中…';
        try {
          const data = await fetchJson('/api/notifications?t=' + Date.now());
          renderNotifications(data.notifications || []);
        } catch (err) {
          if (root) root.textContent = '通知の読み込みに失敗しました：' + (err.message || err);
        }
      }

      async function markNotificationsRead() {
        if (!currentUser) return;
        try {
          await postJson('/api/notifications/read');
          await loadNotifications();
          setStatus('通知を既読にしました。', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('通知の既読処理に失敗しました：' + (err.message || err), 'error');
        }
      }

      function renderAdminReports(reports) {
        const root = $('adminReports');
        clearNode(root);
        if (!root) return;
        root.className = '';
        if (!reports || reports.length === 0) {
          root.className = 'empty';
          root.textContent = '通報はまだありません。';
          return;
        }
        reports.forEach(report => {
          const reporter = report.reporter || {};
          const reported = report.reported_user || {};
          const status = report.status || 'open';
          root.appendChild(el('div', { class:'admin-report ' + status }, [
            el('div', { class:'post-name', text:`通報理由：${report.reason || '未入力'}　状態：${status}` }),
            el('div', { class:'muted', text:`通報者：${reporter.display_name || '不明'} @${reporter.username || 'unknown'} / 投稿者：${reported.display_name || '不明'} @${reported.username || 'unknown'}` }),
            el('div', { class:'post-text', text:report.post_text_current || report.post_text || '投稿本文なし' }),
            el('div', { class:'muted', text:`補足：${report.detail || 'なし'} / ${formatDate(report.created_at)} / 投稿：${report.post_exists ? '存在' : '削除済み'}` }),
            el('div', { class:'admin-actions' }, [
              el('button', { type:'button', class:'secondary', text:'対応済みにする', onclick: () => adminSetReportStatus(report.id, 'resolved') }),
              el('button', { type:'button', class:'ghost', text:'問題なしにする', onclick: () => adminSetReportStatus(report.id, 'ignored') }),
              el('button', { type:'button', class:'danger', text:'投稿を削除', onclick: () => adminDeletePost(report.post_id) })
            ])
          ]));
        });
      }

      async function loadAdminReports() {
        if (!currentUser || !currentUser.is_admin) return;
        const root = $('adminReports');
        if (root) root.textContent = '通報一覧を読み込み中…';
        try {
          const data = await fetchJson('/api/admin/reports?t=' + Date.now());
          renderAdminReports(data.reports || []);
        } catch (err) {
          if (root) root.textContent = '管理者画面の読み込みに失敗しました：' + (err.message || err);
        }
      }

      async function adminSetReportStatus(reportId, status) {
        try {
          await postJson(`/api/admin/reports/${reportId}/status`, { status });
          await loadAdminReports();
          setStatus('通報ステータスを更新しました。', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('通報ステータス更新に失敗しました：' + (err.message || err), 'error');
        }
      }

      async function adminDeletePost(postId) {
        if (!confirm('管理者としてこの投稿を削除しますか？添付メディアも削除されます。')) return;
        try {
          await postJson(`/api/admin/posts/${postId}/delete`);
          await loadAdminReports();
          await loadPosts();
          setStatus('投稿を削除しました。', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('投稿削除に失敗しました：' + (err.message || err), 'error');
        }
      }

      function switchTab(tab) {
        const map = { home: 'homeSection', post: 'postSection', profile:'profileSection' };
        Object.values(map).forEach(id => $(id)?.classList.remove('active'));
        $(map[tab] || 'homeSection')?.classList.add('active');
        document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
        window.scrollTo({ top: 0, behavior: 'smooth' });
        if (tab === 'post') loadPosts();
        if (tab === 'profile') renderProfile();
      }

      function setPostFilter(filter) {
        currentPostFilter = filter;
        $('allPostsBtn')?.classList.toggle('active', filter === 'all');
        $('myPostsBtn')?.classList.toggle('active', filter === 'mine');
        $('savedPostsBtn')?.classList.toggle('active', filter === 'saved');
        loadPosts();
      }

      function renderPosts(posts) {
        const list = $('postList');
        clearNode(list);
        if (!list) return;
        let visible = posts;
        if (currentPostFilter === 'saved') visible = posts.filter(p => p.saved);
        if (currentPostFilter === 'mine') visible = posts.filter(p => p.is_mine);
        if (visible.length === 0) {
          const msg = currentPostFilter === 'saved' ? '保存済みの投稿はまだありません。' : (currentPostFilter === 'mine' ? '自分の投稿はまだありません。' : 'まだ投稿がありません。最初の意見を投稿してみよう。');
          list.appendChild(el('div', { class:'empty', text: msg }));
          return;
        }
        visible.forEach(post => list.appendChild(renderPost(post)));
      }

      function formatBytes(size) {
        const n = Number(size || 0);
        if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'MB';
        if (n >= 1024) return (n / 1024).toFixed(1) + 'KB';
        return n + 'B';
      }

      function renderSelectedMedia() {
        const root = $('selectedMedia');
        const input = $('postMedia');
        clearNode(root);
        if (!root || !input) return;
        const files = Array.from(input.files || []);
        if (files.length > 4) {
          setStatus('画像・動画は最大4件までです。選び直してください。', 'error');
        }
        files.slice(0, 4).forEach(file => {
          root.appendChild(el('div', { class:'selected-media-item', text: `${file.type.startsWith('video/') ? '🎬' : '🖼️'} ${file.name}（${formatBytes(file.size)}）` }));
        });
      }

      function renderPostMedia(media) {
        const items = Array.isArray(media) ? media : [];
        if (!items.length) return null;
        const grid = el('div', { class: items.length === 1 ? 'post-media-grid one' : 'post-media-grid' });
        items.forEach(item => {
          if (item.type === 'video') {
            grid.appendChild(el('video', { class:'post-media', src:item.url, controls:'controls', preload:'metadata' }));
          } else if (item.type === 'image') {
            grid.appendChild(el('img', { class:'post-media', src:item.url, alt:item.filename || '投稿画像', loading:'lazy' }));
          }
        });
        return grid;
      }

      function renderPost(post) {
        const card = el('div', { class:'post' });
        card.dataset.id = post.id;
        const author = post.author || {};
        const avatar = el('div', { class:'avatar', text: author.avatar || 'M' });
        const authorText = el('div', { class:'post-main' }, [
          el('div', { class:'post-name', text: author.display_name || 'Market User' }),
          el('div', { class:'post-handle', text: '@' + (author.username || 'market_user') })
        ]);
        const head = el('div', { class:'post-head' }, [
          el('div', { class:'author-wrap' }, [avatar, authorText]),
          el('div', { class:'post-time', text: formatDate(post.created_at) })
        ]);
        card.appendChild(head);
        card.appendChild(el('div', { class:'post-text', text: post.text || '' }));
        const mediaNode = renderPostMedia(post.media || []);
        if (mediaNode) card.appendChild(mediaNode);
        if (post.is_mine) {
          const ownerActions = el('div', { class:'owner-actions' }, [
            el('button', { type:'button', class:'owner-btn', text:'✏️ 編集', onclick: () => editPost(post.id, post.text || '') }),
            el('button', { type:'button', class:'owner-btn delete', text:'🗑️ 削除', onclick: () => deletePost(post.id) })
          ]);
          card.appendChild(ownerActions);
        }

        const actions = el('div', { class:'post-actions-row' });
        const likeBtn = el('button', { type:'button', class: post.liked ? 'social-btn active' : 'social-btn', text:`♡ いいね ${post.like_count || 0}`, onclick: () => togglePost(post.id, 'like') });
        const commentJumpBtn = el('button', { type:'button', class:'social-btn', text:`💬 コメント ${post.comment_count || 0}`, onclick: () => card.querySelector('input')?.focus() });
        const saveBtn = el('button', { type:'button', class: post.saved ? 'social-btn active' : 'social-btn', text: post.saved ? '🔖 保存済み' : '🔖 保存', onclick: () => togglePost(post.id, 'save') });
        const reportBtn = el('button', { type:'button', class: post.reported ? 'social-btn reported' : 'social-btn', text: post.reported ? '🚩 通報済み' : '🚩 通報', onclick: () => reportPost(post.id) });
        actions.appendChild(likeBtn);
        actions.appendChild(commentJumpBtn);
        actions.appendChild(saveBtn);
        actions.appendChild(reportBtn);
        if (post.can_block_author && author.id) {
          actions.appendChild(el('button', { type:'button', class:'social-btn reported', text:'🙈 ブロック', onclick: () => toggleBlock(author.id) }));
        }
        card.appendChild(actions);

        const comments = el('div', { class:'comment-list' });
        (post.comments || []).forEach(comment => {
          const ca = comment.author || {};
          comments.appendChild(el('div', { class:'comment' }, [
            el('div', { class:'comment-top' }, [
              el('span', { class:'mini-avatar', text: ca.avatar || 'M' }),
              el('span', { text: (ca.display_name || 'User') + ' @' + (ca.username || 'user') })
            ]),
            el('div', { class:'comment-text', text: comment.text || '' }),
            el('div', { class:'comment-time', text: formatDate(comment.created_at) })
          ]));
        });
        card.appendChild(comments);

        if (currentUser) {
          const form = el('div', { class:'comment-form' });
          const input = el('input', { type:'text', maxlength:'180', placeholder:'コメントを書く' });
          const btn = el('button', { type:'button', text:'送信', onclick: () => addComment(post.id, input) });
          input.addEventListener('keydown', (e) => { if (e.key === 'Enter') addComment(post.id, input); });
          form.appendChild(input);
          form.appendChild(btn);
          card.appendChild(form);
        } else {
          card.appendChild(el('div', { class:'muted', text:'コメント・いいね・保存・通報にはログインが必要です。' }));
        }
        return card;
      }

      async function loadPosts() {
        const list = $('postList');
        if (list && !list.childElementCount) list.textContent = '投稿を読み込み中…';
        try {
          const data = await fetchJson('/api/posts?t=' + Date.now());
          currentUser = data.me || currentUser;
          syncAuthUi();
          renderPosts(data.posts || []);
        } catch (err) {
          if (list) list.textContent = '投稿の読み込みに失敗しました：' + (err.message || err);
        }
      }

      async function addPost() {
        const area = $('postText');
        const mediaInput = $('postMedia');
        const text = (area?.value || '').trim();
        const files = Array.from(mediaInput?.files || []);
        if (!currentUser) {
          setStatus('投稿するにはログインしてください。', 'error');
          return;
        }
        if (!text && files.length === 0) {
          setStatus('投稿内容、または画像・動画を追加してください。', 'error');
          return;
        }
        if (files.length > 4) {
          setStatus('画像・動画は最大4件までです。', 'error');
          return;
        }
        const btn = $('postBtn');
        if (btn) { btn.disabled = true; btn.textContent = '投稿中…'; }
        try {
          const form = new FormData();
          form.append('text', text);
          files.forEach(file => form.append('media_files', file));
          const res = await fetch('/api/posts', { method:'POST', body: form, cache:'no-store', credentials:'same-origin' });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
          if (area) area.value = '';
          if (mediaInput) mediaInput.value = '';
          renderSelectedMedia();
          renderPosts(data.posts || []);
          setStatus('投稿しました！', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('投稿に失敗しました：' + (err.message || err), 'error');
        } finally {
          if (btn) { btn.disabled = false; btn.textContent = '投稿する'; }
        }
      }

      async function editPost(postId, oldText) {
        if (!currentUser) {
          setStatus('編集するにはログインしてください。', 'error');
          return;
        }
        const nextText = prompt('投稿を編集してください', oldText || '');
        if (nextText === null) return;
        const text = nextText.trim();
        if (!text) {
          setStatus('投稿内容を空にはできません。', 'error');
          return;
        }
        try {
          const data = await postJson(`/api/posts/${postId}/edit`, { text });
          renderPosts(data.posts || []);
          setStatus('投稿を編集しました！', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('投稿編集に失敗しました：' + (err.message || err), 'error');
        }
      }

      async function deletePost(postId) {
        if (!currentUser) {
          setStatus('削除するにはログインしてください。', 'error');
          return;
        }
        if (!confirm('この投稿を削除しますか？コメント・いいね・保存も一緒に消えます。')) return;
        try {
          const data = await postJson(`/api/posts/${postId}/delete`);
          renderPosts(data.posts || []);
          setStatus('投稿を削除しました。', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('投稿削除に失敗しました：' + (err.message || err), 'error');
        }
      }

      async function togglePost(postId, action) {
        if (!currentUser) {
          setStatus('この操作にはログインが必要です。', 'error');
          return;
        }
        try {
          const data = await postJson(`/api/posts/${postId}/${action}`);
          renderPosts(data.posts || []);
        } catch (err) {
          setStatus('操作に失敗しました：' + (err.message || err), 'error');
        }
      }

      async function addComment(postId, input) {
        const text = (input?.value || '').trim();
        if (!text) return;
        input.disabled = true;
        try {
          const data = await postJson(`/api/posts/${postId}/comments`, { text });
          input.value = '';
          renderPosts(data.posts || []);
        } catch (err) {
          setStatus('コメントに失敗しました：' + (err.message || err), 'error');
        } finally {
          input.disabled = false;
        }
      }

      async function reportPost(postId) {
        if (!currentUser) {
          setStatus('通報にはログインが必要です。', 'error');
          return;
        }
        const reason = prompt('通報理由を入力してください（例：迷惑投稿、誹謗中傷、投資詐欺っぽい内容など）');
        if (!reason) return;
        const detail = prompt('補足があれば入力してください（空欄でもOK）') || '';
        try {
          const data = await postJson(`/api/posts/${postId}/report`, { reason, detail });
          renderPosts(data.posts || []);
          setStatus('通報を受け付けました。', 'done');
          clearStatusSoon();
        } catch (err) {
          setStatus('通報に失敗しました：' + (err.message || err), 'error');
        }
      }

      function init() {
        $('reportBtn')?.addEventListener('click', () => loadReport(true));
        $('columnBtn')?.addEventListener('click', () => loadColumn());
        $('postBtn')?.addEventListener('click', addPost);
        $('postMedia')?.addEventListener('change', renderSelectedMedia);
        $('allPostsBtn')?.addEventListener('click', () => setPostFilter('all'));
        $('myPostsBtn')?.addEventListener('click', () => setPostFilter('mine'));
        $('savedPostsBtn')?.addEventListener('click', () => setPostFilter('saved'));
        document.querySelectorAll('.nav-btn').forEach(btn => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));
        loadMe().then(() => loadPosts());
      }

      window.addEventListener('error', (event) => {
        setStatus('画面側のエラーが出ました：' + event.message, 'error');
      });

      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
      } else {
        init();
      }
    })();
  </script>
</body>
</html>
"""
    return (
        html.replace("__APP_OPERATOR_NAME__", APP_OPERATOR_NAME)
        .replace("__APP_CONTACT_EMAIL__", APP_CONTACT_EMAIL)
    )


@app.get("/api/me")
def api_me(request: Request):
    return {"user": _public_user(_current_user(request))}


@app.post("/api/register")
def api_register(payload: RegisterCreate, response: Response):
    username = payload.username.strip().lower()
    display_name = payload.display_name.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="ユーザー名は3〜24文字の英数字と_だけ使えます")
    if not display_name:
        raise HTTPException(status_code=400, detail="表示名を入力してください")
    _check_text_safety(display_name, "表示名")
    users = _load_users()
    if _find_user_by_username(users, username):
        raise HTTPException(status_code=400, detail="このユーザー名はすでに使われています")
    salt, password_hash = _hash_password(payload.password)
    user = {
        "id": str(uuid.uuid4()),
        "username": username,
        "display_name": display_name[:30],
        "bio": "",
        "avatar": display_name[:1] or username[:1].upper(),
        "following": [],
        "blocked_users": [],
        "is_admin": not any(bool(u.get("is_admin")) for u in users),
        "salt": salt,
        "password_hash": password_hash,
        "created_at": _now_iso(),
    }
    users.append(user)
    _save_users(users)
    token = secrets.token_urlsafe(32)
    sessions = _load_sessions()
    sessions[token] = {"user_id": user["id"], "created_at": _now_iso()}
    _save_sessions(sessions)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"user": _public_user(user)}


@app.post("/api/login")
def api_login(payload: LoginCreate, response: Response):
    users = _load_users()
    user = _find_user_by_username(users, payload.username)
    if not user or not _verify_password(payload.password, user):
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが違います")
    token = secrets.token_urlsafe(32)
    sessions = _load_sessions()
    sessions[token] = {"user_id": user["id"], "created_at": _now_iso()}
    _save_sessions(sessions)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"user": _public_user(user)}


@app.post("/api/logout")
def api_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sessions = _load_sessions()
        sessions.pop(token, None)
        _save_sessions(sessions)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.post("/api/account/delete")
def api_delete_account(payload: AccountDeleteCreate, request: Request, response: Response):
    user = _require_user(request)
    if not _verify_password(payload.password, user):
        raise HTTPException(status_code=401, detail="パスワードが違います")
    uid = user.get("id")
    posts = _load_posts()
    remaining_posts = []
    for post in posts:
        if post.get("user_id") == uid:
            _delete_media_files(post.get("media", []))
            continue
        post["liked_by"] = [x for x in post.get("liked_by", []) if x != uid]
        post["saved_by"] = [x for x in post.get("saved_by", []) if x != uid]
        post["comments"] = [c for c in post.get("comments", []) if not (isinstance(c, dict) and c.get("user_id") == uid)]
        post["reports"] = [r for r in post.get("reports", []) if not (isinstance(r, dict) and r.get("user_id") == uid)]
        remaining_posts.append(post)
    _save_posts(remaining_posts)

    users = _load_users()
    kept_users = []
    for other in users:
        if other.get("id") == uid:
            continue
        other["following"] = [x for x in other.get("following", []) if x != uid]
        other["blocked_users"] = [x for x in other.get("blocked_users", []) if x != uid]
        kept_users.append(other)
    _save_users(kept_users)

    remaining_post_ids = {p.get("id") for p in remaining_posts}
    reports = [
        r for r in _load_reports()
        if isinstance(r, dict) and r.get("user_id") != uid and r.get("reported_user_id") != uid and r.get("post_id") in remaining_post_ids
    ]
    _save_reports(reports)

    notifications = [
        n for n in _load_notifications()
        if isinstance(n, dict) and n.get("user_id") != uid and n.get("actor_id") != uid
    ]
    _save_notifications(notifications)

    sessions = _load_sessions()
    for token, session in list(sessions.items()):
        if isinstance(session, dict) and session.get("user_id") == uid:
            sessions.pop(token, None)
    _save_sessions(sessions)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.post("/api/profile")
def api_update_profile(payload: ProfileUpdate, request: Request):
    user = _require_user(request)
    users = _load_users()
    target = _find_user_by_id(users, user.get("id"))
    if not target:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    _check_text_safety(payload.display_name, "表示名")
    _check_text_safety(payload.bio, "自己紹介")
    target["display_name"] = payload.display_name.strip()[:30]
    target["bio"] = payload.bio.strip()[:160]
    avatar = (payload.avatar.strip() or target["display_name"][:1] or "M")[:2]
    target["avatar"] = avatar
    _save_users(users)
    return {"user": _public_user(target)}


@app.get("/api/posts")
def api_posts(request: Request):
    return _posts_response(_load_posts(), _current_user(request))


@app.post("/api/posts")
async def api_create_post(
    request: Request,
    text: str = Form(""),
    media_files: list[UploadFile] = File(default=[]),
):
    user = _require_user(request)
    text = (text or "").strip()
    _check_text_safety(text, "投稿")
    media = await _save_media_files(media_files)
    if not text and not media:
        raise HTTPException(status_code=400, detail="投稿内容、または画像・動画を追加してください")
    posts = _load_posts()
    post = {
        "id": str(uuid.uuid4()),
        "text": text[:280],
        "media": media,
        "created_at": _now_iso(),
        "user_id": user["id"],
        "liked_by": [],
        "saved_by": [],
        "reports": [],
        "comments": [],
    }
    posts.insert(0, post)
    _save_posts(posts)
    return _posts_response(posts, user)


@app.post("/api/posts/{post_id}/edit")
def api_edit_post(post_id: str, payload: PostUpdate, request: Request):
    user = _require_user(request)
    text = payload.text.strip()
    _check_text_safety(text, "投稿")
    if not text:
        raise HTTPException(status_code=400, detail="投稿内容を入力してください")
    posts = _load_posts()
    post = _find_post(posts, post_id)
    if post.get("user_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="自分の投稿だけ編集できます")
    post["text"] = text[:280]
    post["updated_at"] = _now_iso()
    _save_posts(posts)
    return _posts_response(posts, user)


@app.post("/api/posts/{post_id}/delete")
def api_delete_post(post_id: str, request: Request):
    user = _require_user(request)
    posts = _load_posts()
    post = _find_post(posts, post_id)
    if post.get("user_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="自分の投稿だけ削除できます")
    _delete_media_files(post.get("media", []))
    posts = [p for p in posts if p.get("id") != post_id]
    reports = [r for r in _load_reports() if not (isinstance(r, dict) and r.get("post_id") == post_id)]
    _save_reports(reports)
    _save_posts(posts)
    return _posts_response(posts, user)


@app.post("/api/posts/{post_id}/like")
def api_toggle_like(post_id: str, request: Request):
    user = _require_user(request)
    posts = _load_posts()
    post = _find_post(posts, post_id)
    _ensure_can_interact(user, post.get("user_id"), _load_users())
    post.setdefault("liked_by", [])
    uid = user["id"]
    if uid in post["liked_by"]:
        post["liked_by"] = [x for x in post["liked_by"] if x != uid]
    else:
        post["liked_by"].append(uid)
        _add_notification(post.get("user_id"), "like", f"{user.get('display_name') or user.get('username')}さんがあなたの投稿にいいねしました", user.get("id"), post_id)
    _save_posts(posts)
    return _posts_response(posts, user)


@app.post("/api/posts/{post_id}/save")
def api_toggle_save(post_id: str, request: Request):
    user = _require_user(request)
    posts = _load_posts()
    post = _find_post(posts, post_id)
    _ensure_can_interact(user, post.get("user_id"), _load_users())
    post.setdefault("saved_by", [])
    uid = user["id"]
    if uid in post["saved_by"]:
        post["saved_by"] = [x for x in post["saved_by"] if x != uid]
    else:
        post["saved_by"].append(uid)
    _save_posts(posts)
    return _posts_response(posts, user)


@app.post("/api/posts/{post_id}/comments")
def api_add_comment(post_id: str, payload: CommentCreate, request: Request):
    user = _require_user(request)
    text = payload.text.strip()
    _check_text_safety(text, "コメント")
    if not text:
        raise HTTPException(status_code=400, detail="コメント内容を入力してください")
    posts = _load_posts()
    post = _find_post(posts, post_id)
    _ensure_can_interact(user, post.get("user_id"), _load_users())
    post.setdefault("comments", [])
    post["comments"].append({
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "text": text[:180],
        "created_at": _now_iso(),
    })
    _add_notification(post.get("user_id"), "comment", f"{user.get('display_name') or user.get('username')}さんがあなたの投稿にコメントしました", user.get("id"), post_id)
    _save_posts(posts)
    return _posts_response(posts, user)


@app.post("/api/posts/{post_id}/report")
def api_report_post(post_id: str, payload: ReportCreate, request: Request):
    user = _require_user(request)
    posts = _load_posts()
    post = _find_post(posts, post_id)
    _ensure_can_interact(user, post.get("user_id"), _load_users())
    post.setdefault("reports", [])
    if any(isinstance(r, dict) and r.get("user_id") == user["id"] for r in post["reports"]):
        raise HTTPException(status_code=400, detail="この投稿はすでに通報済みです")
    report = {
        "id": str(uuid.uuid4()),
        "post_id": post_id,
        "post_text": post.get("text", "")[:280],
        "reported_user_id": post.get("user_id"),
        "user_id": user["id"],
        "reason": payload.reason.strip()[:40],
        "detail": payload.detail.strip()[:240],
        "created_at": _now_iso(),
        "status": "open",
    }
    post["reports"].append(report)
    reports = _load_reports()
    reports.insert(0, report)
    _save_reports(reports)
    _save_posts(posts)
    for admin in [u for u in _load_users() if u.get("is_admin")]:
        _add_notification(admin.get("id"), "report", "新しい通報があります", user.get("id"), post_id)
    return _posts_response(posts, user)


@app.get("/api/reports")
def api_social_reports(request: Request):
    _require_user(request)
    return {"reports": _load_reports()}


@app.get("/api/users/search")
def api_user_search(request: Request, q: str = Query("", max_length=40)):
    current = _require_user(request)
    users = _load_users()
    key = q.strip().lower()
    if not key:
        results = sorted(users, key=lambda u: u.get("created_at", ""), reverse=True)[:20]
    else:
        results = [u for u in users if key in str(u.get("username", "")).lower() or key in str(u.get("display_name", "")).lower()][:20]
    return {"users": [_public_user_with_social(u, current, users) for u in results]}


@app.post("/api/users/{user_id}/follow")
def api_toggle_follow(user_id: str, request: Request):
    current = _require_user(request)
    users = _load_users()
    target = _find_user_by_id(users, user_id)
    actor = _find_user_by_id(users, current.get("id"))
    if not target or not actor:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    if target.get("id") == actor.get("id"):
        raise HTTPException(status_code=400, detail="自分自身はフォローできません")
    if target.get("id") in (actor.get("blocked_users", []) if isinstance(actor.get("blocked_users"), list) else []):
        raise HTTPException(status_code=403, detail="ブロック中のユーザーはフォローできません")
    if actor.get("id") in (target.get("blocked_users", []) if isinstance(target.get("blocked_users"), list) else []):
        raise HTTPException(status_code=403, detail="相手からブロックされているためフォローできません")
    actor.setdefault("following", [])
    if target["id"] in actor["following"]:
        actor["following"] = [x for x in actor["following"] if x != target["id"]]
    else:
        actor["following"].append(target["id"])
        _add_notification(target.get("id"), "follow", f"{actor.get('display_name') or actor.get('username')}さんがあなたをフォローしました", actor.get("id"))
    _save_users(users)
    return {"users": [_public_user_with_social(u, actor, users) for u in users]}


@app.post("/api/users/{user_id}/block")
def api_toggle_block(user_id: str, request: Request):
    current = _require_user(request)
    users = _load_users()
    target = _find_user_by_id(users, user_id)
    actor = _find_user_by_id(users, current.get("id"))
    if not target or not actor:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    if target.get("id") == actor.get("id"):
        raise HTTPException(status_code=400, detail="自分自身はブロックできません")
    actor.setdefault("blocked_users", [])
    actor.setdefault("following", [])
    target.setdefault("following", [])
    if target["id"] in actor["blocked_users"]:
        actor["blocked_users"] = [x for x in actor["blocked_users"] if x != target["id"]]
        blocked = False
    else:
        actor["blocked_users"].append(target["id"])
        actor["following"] = [x for x in actor.get("following", []) if x != target["id"]]
        target["following"] = [x for x in target.get("following", []) if x != actor["id"]]
        blocked = True
    _save_users(users)
    actor = _find_user_by_id(users, actor.get("id")) or actor
    return {"blocked": blocked, "me": _public_user(actor), "users": [_public_user_with_social(u, actor, users) for u in users]}


@app.get("/api/notifications")
def api_notifications(request: Request):
    user = _require_user(request)
    notifications = [n for n in _load_notifications() if isinstance(n, dict) and n.get("user_id") == user.get("id")]
    notifications = sorted(notifications, key=lambda n: n.get("created_at", ""), reverse=True)[:50]
    return {"notifications": notifications, "unread_count": sum(1 for n in notifications if not n.get("read"))}


@app.post("/api/notifications/read")
def api_mark_notifications_read(request: Request):
    user = _require_user(request)
    notifications = _load_notifications()
    for n in notifications:
        if isinstance(n, dict) and n.get("user_id") == user.get("id"):
            n["read"] = True
    _save_notifications(notifications)
    return {"ok": True}


def _admin_report_view(report: dict, users: list[dict], posts: list[dict]) -> dict:
    reporter = _find_user_by_id(users, report.get("user_id"))
    reported = _find_user_by_id(users, report.get("reported_user_id"))
    post = next((p for p in posts if p.get("id") == report.get("post_id")), None)
    return {
        **report,
        "reporter": _public_user(reporter),
        "reported_user": _public_user(reported),
        "post_exists": bool(post),
        "post_text_current": (post or {}).get("text", report.get("post_text", "")),
    }


@app.get("/api/admin/reports")
def api_admin_reports(request: Request):
    _require_admin(request)
    users = _load_users()
    posts = _load_posts()
    reports = sorted(_load_reports(), key=lambda r: r.get("created_at", "") if isinstance(r, dict) else "", reverse=True)
    return {"reports": [_admin_report_view(r, users, posts) for r in reports if isinstance(r, dict)]}


@app.post("/api/admin/posts/{post_id}/delete")
def api_admin_delete_post(post_id: str, request: Request):
    admin = _require_admin(request)
    posts = _load_posts()
    post = _find_post(posts, post_id)
    _delete_media_files(post.get("media", []))
    posts = [p for p in posts if p.get("id") != post_id]
    reports = _load_reports()
    for r in reports:
        if isinstance(r, dict) and r.get("post_id") == post_id:
            r["status"] = "post_deleted"
            r["resolved_at"] = _now_iso()
            r["resolved_by"] = admin.get("id")
    _save_posts(posts)
    _save_reports(reports)
    return {"ok": True}


@app.post("/api/admin/reports/{report_id}/status")
def api_admin_update_report_status(report_id: str, payload: AdminReportStatusUpdate, request: Request):
    admin = _require_admin(request)
    status = payload.status.strip()
    if status not in {"open", "resolved", "ignored", "post_deleted"}:
        raise HTTPException(status_code=400, detail="statusは open / resolved / ignored / post_deleted のいずれかです")
    reports = _load_reports()
    found = False
    for r in reports:
        if isinstance(r, dict) and r.get("id") == report_id:
            r["status"] = status
            r["resolved_at"] = _now_iso() if status != "open" else None
            r["resolved_by"] = admin.get("id") if status != "open" else None
            found = True
    if not found:
        raise HTTPException(status_code=404, detail="通報が見つかりません")
    _save_reports(reports)
    return {"ok": True}


@app.get("/api/app-info")
def api_app_info():
    return {"operator_name": APP_OPERATOR_NAME, "contact_email": APP_CONTACT_EMAIL}


@app.get("/api/health")
def api_health():
    return {"ok": True}


@app.get("/api/news")
def api_news():
    return {"news": fetch_news()}


@app.get("/api/markets")
def api_markets():
    return {"markets": fetch_markets()}


@app.get("/api/column")
def api_column():
    news = fetch_news()
    markets = fetch_markets()
    column = generate_column(news, markets)
    points = generate_points(column)
    return {"column": column, "points": points, "news": news, "markets": markets}


@app.get("/api/quiz")
def api_quiz():
    news = fetch_news()
    markets = fetch_markets()
    column = generate_column(news, markets)
    points = generate_points(column)
    quiz = generate_quiz(column)
    return {"quiz": quiz, "quiz_items": parse_quiz_text(quiz), "column": column, "points": points}


@app.get("/api/report")
def api_report(save: bool = False):
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
    if save:
        report["saved"] = save_report(report)
    return report
