"""
ربات دوست‌یابی دانشجویی پیشرفته
- بدون پرسیدن شهر
- پرسیدن: جنسیت، کاردانی/کارشناسی، ترم، علایق، بیو و عکس (اجباری)
- سیستم لایک و مچ
- چت دوطرفه داخل ربات
- درخواست آیدی با اجازه طرف مقابل
"""

import os
import json
import asyncio
import sqlite3
import logging
from datetime import datetime
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    TypeHandler,
    ApplicationHandlerStop,
    filters,
)
from telegram.constants import ParseMode

# ==================== تنظیمات ====================
TELEGRAM_TOKEN = "8557089182:AAHeLPClY55H9b3ezvR9SW3n2IasQgY14KI"
# آیدی عددی مالک/ادمین‌ها (برای فهمیدن آیدی عددی خودت داخل ربات /myid بزن)
ADMIN_IDS = [5932482562]  # مثلاً [123456789]

# وضعیت‌های مکالمه
(
    REG_GENDER,
    REG_LOOKING,
    REG_DEGREE,
    REG_TERM,
    REG_AGE,
    REG_INTERESTS,
    REG_BIO,
    REG_PHOTO,
    EDIT_BIO,
    EDIT_PHOTO,
    CHAT_MESSAGE,
    REG_NAME,
    EDIT_NAME,
) = range(13)

# ==================== دیتابیس ====================
DB_PATH = "dating_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            gender TEXT,
            looking_for TEXT,
            degree TEXT,
            term INTEGER,
            age INTEGER,
            bio TEXT,
            interests TEXT,
            photo_file_id TEXT,
            is_active INTEGER DEFAULT 1,
            is_banned INTEGER DEFAULT 0,
            created_at TEXT,
            last_active TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            created_at TEXT,
            UNIQUE(from_user, to_user)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1 INTEGER,
            user2 INTEGER,
            created_at TEXT,
            UNIQUE(user1, user2)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocker INTEGER,
            blocked INTEGER,
            created_at TEXT,
            UNIQUE(blocker, blocked)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS id_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter INTEGER,
            reported INTEGER,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            resolved_by INTEGER,
            created_at TEXT,
            resolved_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            kind TEXT,
            content TEXT,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def user_exists(user_id: int) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    exists = c.fetchone() is not None
    conn.close()
    return exists


def get_user(user_id: int) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def save_user(user_id: int, data: dict):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    if user_exists(user_id):
        fields = []
        values = []
        for k, v in data.items():
            fields.append(f"{k} = ?")
            values.append(v)
        values.append(now)
        values.append(user_id)
        c.execute(f"UPDATE users SET {', '.join(fields)}, last_active = ? WHERE user_id = ?", values)
    else:
        data["user_id"] = user_id
        data["created_at"] = now
        data["last_active"] = now
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        c.execute(f"INSERT INTO users ({cols}) VALUES ({placeholders})", list(data.values()))

    conn.commit()
    conn.close()


def is_matched(u1: int, u2: int) -> bool:
    conn = get_db()
    c = conn.cursor()
    a, b = min(u1, u2), max(u1, u2)
    c.execute("SELECT 1 FROM matches WHERE user1 = ? AND user2 = ?", (a, b))
    result = c.fetchone() is not None
    conn.close()
    return result


def create_match(u1: int, u2: int):
    conn = get_db()
    c = conn.cursor()
    a, b = min(u1, u2), max(u1, u2)
    now = datetime.now().isoformat()
    try:
        c.execute("INSERT INTO matches (user1, user2, created_at) VALUES (?, ?, ?)", (a, b, now))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()


def add_like(from_user: int, to_user: int) -> str:
    """لایک اضافه می‌کند.
    خروجی: "mutual" (مچ شد) / "liked" (لایک جدید ثبت شد) / "exists" (قبلاً لایک شده بود)"""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute("INSERT INTO likes (from_user, to_user, created_at) VALUES (?, ?, ?)", (from_user, to_user, now))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return "exists"

    # چک لایک متقابل
    c.execute("SELECT 1 FROM likes WHERE from_user = ? AND to_user = ?", (to_user, from_user))
    mutual = c.fetchone() is not None
    conn.close()

    if mutual:
        create_match(from_user, to_user)
        return "mutual"
    return "liked"


def get_matches(user_id: int) -> list:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT CASE WHEN user1 = ? THEN user2 ELSE user1 END AS partner_id
        FROM matches WHERE user1 = ? OR user2 = ?
    """, (user_id, user_id, user_id))
    partners = [row["partner_id"] for row in c.fetchall()]
    conn.close()
    return partners


def get_chat_request(u1: int, u2: int) -> Optional[dict]:
    """آخرین درخواست چت بین دو نفر (در هر جهت)"""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """SELECT * FROM chat_requests
           WHERE (from_user = ? AND to_user = ?) OR (from_user = ? AND to_user = ?)
           ORDER BY id DESC LIMIT 1""",
        (u1, u2, u2, u1),
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def create_chat_request(from_user: int, to_user: int) -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO chat_requests (from_user, to_user, status, created_at) VALUES (?, ?, 'pending', ?)",
        (from_user, to_user, datetime.now().isoformat()),
    )
    req_id = c.lastrowid
    conn.commit()
    conn.close()
    return req_id


def set_chat_request_status(req_id: int, status: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE chat_requests SET status = ? WHERE id = ?", (status, req_id))
    conn.commit()
    conn.close()


def chat_accepted(u1: int, u2: int) -> bool:
    req = get_chat_request(u1, u2)
    return bool(req and req["status"] == "accepted")


def is_blocked(blocker: int, blocked: int) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM blocks WHERE blocker = ? AND blocked = ?", (blocker, blocked))
    result = c.fetchone() is not None
    conn.close()
    return result


def block_user(blocker: int, blocked: int):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute("INSERT INTO blocks (blocker, blocked, created_at) VALUES (?, ?, ?)", (blocker, blocked, now))
        # حذف مچ اگر وجود داشت
        a, b = min(blocker, blocked), max(blocker, blocked)
        c.execute("DELETE FROM matches WHERE user1 = ? AND user2 = ?", (a, b))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()


def get_next_profile(user_id: int, exclude=None) -> Optional[dict]:
    """یک پروفایل مناسب برای نمایش پیدا می‌کند"""
    me = get_user(user_id)
    if not me:
        return None

    conn = get_db()
    c = conn.cursor()

    # کسانی که قبلاً لایک یا بلاک شده‌اند را رد کن
    c.execute("""
        SELECT user_id FROM users
        WHERE is_active = 1 AND is_banned = 0
          AND user_id != ?
          AND user_id NOT IN (SELECT to_user FROM likes WHERE from_user = ?)
          AND user_id NOT IN (SELECT blocked FROM blocks WHERE blocker = ?)
          AND user_id NOT IN (SELECT blocker FROM blocks WHERE blocked = ?)
    """, (user_id, user_id, user_id, user_id))

    exclude = set(exclude or [])
    candidates = [row["user_id"] for row in c.fetchall() if row["user_id"] not in exclude]
    conn.close()

    for cid in candidates:
        other = get_user(cid)
        if not other:
            continue

        # فیلتر جنسیت
        my_looking = me.get("looking_for") or "هر دو"
        other_looking = other.get("looking_for") or "هر دو"
        my_gender = me.get("gender")
        other_gender = other.get("gender")

        if my_looking != "هر دو" and other_gender != my_looking:
            continue
        if other_looking != "هر دو" and my_gender != other_looking:
            continue

        return other

    return None


# ==================== ابزارهای کمکی (ادمین، تاریخ، گزارش) ====================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def gregorian_to_jalali(gy: int, gm: int, gd: int):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def fmt_date(iso) -> str:
    """تاریخ ISO را به شمسی (با ساعت) تبدیل می‌کند"""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        return str(iso)
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy}/{jm:02d}/{jd:02d} {dt.hour:02d}:{dt.minute:02d}".translate(PERSIAN_DIGITS)


def get_setting(key: str, default=None):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


DEFAULT_MAINTENANCE_MSG = "🛠 ربات موقتاً برای به‌روزرسانی غیرفعاله. کمی بعد دوباره امتحان کن."


def is_maintenance() -> bool:
    return get_setting("maintenance", "0") == "1"


def user_line(uid: int) -> str:
    u = get_user(uid)
    if not u:
        return f"حساب حذف‌شده (ID: {uid})"
    un = f"@{u['username']}" if u.get("username") else "بدون یوزرنیم"
    return f"{u.get('full_name') or 'بدون نام'} | {un} | ID: {uid}"


def short_name(uid: int) -> str:
    u = get_user(uid)
    name = (u.get("full_name") if u else None) or "ناشناس"
    return f"{name}({uid})"


# ---------- گزارش‌ها ----------
REPORT_REASONS = ["بد دهنی و توهین", "محتوای نامناسب", "مزاحمت", "حساب جعلی", "اسپم و تبلیغات"]

REPORT_STATUS_TEXT = {
    "pending": "⏳ در انتظار بررسی",
    "accepted": "✅ تأیید شد (با فرد برخورد شد)",
    "banned": "✅ تأیید شد و فرد مسدود شد",
    "rejected": "❌ رد شد",
}


def create_report(reporter: int, reported: int, reason: str) -> Optional[int]:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM reports WHERE reporter = ? AND reported = ? AND status = 'pending'",
        (reporter, reported),
    )
    if c.fetchone():
        conn.close()
        return None
    c.execute(
        "INSERT INTO reports (reporter, reported, reason, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
        (reporter, reported, reason, datetime.now().isoformat()),
    )
    rid = c.lastrowid
    conn.commit()
    conn.close()
    return rid


def get_report(rid: int) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM reports WHERE id = ?", (rid,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_reports(limit: int = 10) -> list:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM reports WHERE status = 'pending' ORDER BY id ASC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def resolve_report(rid: int, status: str, admin_id: int) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE reports SET status = ?, resolved_by = ?, resolved_at = ? WHERE id = ? AND status = 'pending'",
        (status, admin_id, datetime.now().isoformat(), rid),
    )
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def report_text(r: dict) -> str:
    return (
        f"⚠️ گزارش #{r['id']}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📝 دلیل گزارش: {r['reason']}\n"
        f"👤 شخص گزارش‌دهنده: {user_line(r['reporter'])}\n"
        f"🚫 شخص گزارش‌شده: {user_line(r['reported'])}\n"
        f"🕒 تاریخ گزارش: {fmt_date(r['created_at'])}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"وضعیت: {REPORT_STATUS_TEXT.get(r['status'], r['status'])}"
    )


# ---------- سابقه چت ----------
def log_chat(from_user: int, to_user: int, kind: str, content: str):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO chat_log (from_user, to_user, kind, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (from_user, to_user, kind, content or "", datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_chat_log(a: int, b: int, limit: int = 80) -> list:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """SELECT * FROM (
               SELECT * FROM chat_log
               WHERE (from_user = ? AND to_user = ?) OR (from_user = ? AND to_user = ?)
               ORDER BY id DESC LIMIT ?
           ) ORDER BY id ASC""",
        (a, b, b, a, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_chat_partners(uid: int) -> list:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """SELECT CASE WHEN from_user = ? THEN to_user ELSE from_user END AS partner,
                  COUNT(*) AS cnt, MAX(created_at) AS last_at
           FROM chat_log WHERE from_user = ? OR to_user = ?
           GROUP BY partner ORDER BY last_at DESC""",
        (uid, uid, uid),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ---------- کاربران و آمار ----------
USERS_PER_PAGE = 10


def count_users() -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS n FROM users")
    n = c.fetchone()["n"]
    conn.close()
    return n


def list_users(offset: int, limit: int = USERS_PER_PAGE) -> list:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_stats() -> dict:
    conn = get_db()
    c = conn.cursor()
    stats = {}
    for key, sql in {
        "users": "SELECT COUNT(*) FROM users",
        "active": "SELECT COUNT(*) FROM users WHERE is_active = 1 AND is_banned = 0",
        "banned": "SELECT COUNT(*) FROM users WHERE is_banned = 1",
        "matches": "SELECT COUNT(*) FROM matches",
        "pending_reports": "SELECT COUNT(*) FROM reports WHERE status = 'pending'",
    }.items():
        c.execute(sql)
        stats[key] = c.fetchone()[0]
    conn.close()
    return stats


def get_broadcast_targets() -> list:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_banned = 0")
    ids = [row["user_id"] for row in c.fetchall()]
    conn.close()
    return ids


def md(text) -> str:
    """کاراکترهای خاص Markdown را در متن کاربر escape می‌کند تا پیام خراب نشود"""
    if text is None:
        return ""
    for ch in ("\\", "_", "*", "`", "["):
        text = str(text).replace(ch, "\\" + ch)
    return text


# ==================== کیبوردها ====================
ADMIN_PANEL_BUTTON = "🛠 پنل مالک"


def main_menu_keyboard(user_id: int = None):
    keyboard = [
        ["🔍 پیدا کردن دوست", "💕 مچ‌های من"],
        ["👤 پروفایل من", "✏️ ویرایش پروفایل"],
        ["⚙️ تنظیمات", "ℹ️ راهنما"],
    ]
    if user_id is not None and is_admin(user_id):
        keyboard.append([ADMIN_PANEL_BUTTON])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def gender_keyboard():
    keyboard = [
        [InlineKeyboardButton("👨 مرد", callback_data="gender_مرد"),
         InlineKeyboardButton("👩 زن", callback_data="gender_زن")],
    ]
    return InlineKeyboardMarkup(keyboard)


def looking_keyboard():
    keyboard = [
        [InlineKeyboardButton("👨 مرد", callback_data="looking_مرد"),
         InlineKeyboardButton("👩 زن", callback_data="looking_زن")],
        [InlineKeyboardButton("👥 هر دو", callback_data="looking_هر دو")],
    ]
    return InlineKeyboardMarkup(keyboard)


def degree_keyboard():
    keyboard = [
        [InlineKeyboardButton("کاردانی", callback_data="degree_کاردانی"),
         InlineKeyboardButton("کارشناسی", callback_data="degree_کارشناسی")],
        [InlineKeyboardButton("کارشناسی ارشد", callback_data="degree_کارشناسی ارشد"),
         InlineKeyboardButton("دکتری", callback_data="degree_دکتری")],
    ]
    return InlineKeyboardMarkup(keyboard)


def term_keyboard():
    buttons = []
    row = []
    for i in range(1, 9):
        row.append(InlineKeyboardButton(str(i), callback_data=f"term_{i}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("بیشتر از ۸", callback_data="term_9")])
    return InlineKeyboardMarkup(buttons)


INTERESTS_LIST = [
    "🎮 گیم", "📚 مطالعه", "🎵 موسیقی", "🎬 فیلم و سریال",
    "⚽ ورزش", "🎨 هنر", "💻 برنامه‌نویسی", "📸 عکاسی",
    "🍳 آشپزی", "✈️ سفر", "🧠 روانشناسی", "🔬 علم",
    "🎭 تئاتر", "📱 شبکه‌های اجتماعی", "🏋️ بدنسازی", "🧘 مدیتیشن",
]


def interests_keyboard(selected: list = None):
    selected = selected or []
    buttons = []
    row = []
    for interest in INTERESTS_LIST:
        mark = "✅ " if interest in selected else ""
        row.append(InlineKeyboardButton(f"{mark}{interest}", callback_data=f"int_{interest}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✅ تمام شد", callback_data="int_done")])
    return InlineKeyboardMarkup(buttons)


def profile_actions_keyboard(target_id: int):
    keyboard = [
        [
            InlineKeyboardButton("❤️ لایک", callback_data=f"like_{target_id}"),
            InlineKeyboardButton("➡️ بعدی", callback_data="next_profile"),
        ],
        [
            InlineKeyboardButton("🚫 بلاک", callback_data=f"block_{target_id}"),
            InlineKeyboardButton("⚠️ گزارش", callback_data=f"report_{target_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def match_actions_keyboard(partner_id: int):
    keyboard = [
        [InlineKeyboardButton("💬 شروع چت", callback_data=f"chat_{partner_id}")],
        [InlineKeyboardButton("📩 درخواست آیدی تلگرام", callback_data=f"reqid_{partner_id}")],
        [
            InlineKeyboardButton("🚫 بلاک کردن", callback_data=f"block_{partner_id}"),
            InlineKeyboardButton("⚠️ گزارش", callback_data=f"report_{partner_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def like_notice_keyboard(liker_id: int):
    keyboard = [
        [
            InlineKeyboardButton("❤️ لایک کردن", callback_data=f"likeback_{liker_id}"),
            InlineKeyboardButton("👁 مشاهده پروفایل", callback_data=f"viewliker_{liker_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def liker_profile_keyboard(liker_id: int):
    keyboard = [
        [InlineKeyboardButton("❤️ لایک کردن", callback_data=f"likeback_{liker_id}")],
        [
            InlineKeyboardButton("🚫 بلاک", callback_data=f"block_{liker_id}"),
            InlineKeyboardButton("⚠️ گزارش", callback_data=f"report_{liker_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def chat_request_keyboard(requester_id: int):
    keyboard = [
        [
            InlineKeyboardButton("✅ آره", callback_data=f"chatreq_yes_{requester_id}"),
            InlineKeyboardButton("❌ نه", callback_data=f"chatreq_no_{requester_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def yes_no_keyboard(action: str, target_id: int):
    keyboard = [
        [
            InlineKeyboardButton("✅ بله، اجازه می‌دم", callback_data=f"{action}_yes_{target_id}"),
            InlineKeyboardButton("❌ نه", callback_data=f"{action}_no_{target_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ==================== نمایش پروفایل ====================
def format_profile(user: dict, show_id: bool = False) -> str:
    interests = json.loads(user.get("interests") or "[]")
    interests_str = "، ".join(interests) if interests else "ثبت نشده"

    text = (
        f"👤 **{md(user.get('full_name')) or 'بدون نام'}**\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⚧ جنسیت: {user.get('gender', '—')}\n"
        f"🎓 مقطع: {user.get('degree', '—')}\n"
        f"📖 ترم: {user.get('term', '—')}\n"
        f"🎂 سن: {user.get('age', '—')}\n"
        f"💡 علایق: {interests_str}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📝 درباره من:\n{md(user.get('bio')) or 'هنوز چیزی ننوشته...'}"
    )
    if show_id and user.get("username"):
        text += f"\n\n🔗 @{md(user['username'])}"
    return text


async def send_profile(update_or_query, context, user: dict, with_actions: bool = True,
                       target_id: int = None, keyboard=None, delete_old: bool = True):
    text = format_profile(user)
    photo = user.get("photo_file_id")

    if keyboard is None and with_actions and target_id:
        keyboard = profile_actions_keyboard(target_id)

    if getattr(update_or_query, "callback_query", None):
        q = update_or_query.callback_query
        chat_id = q.message.chat_id
        # پیام قبلی (چه عکس‌دار چه متنی) پاک می‌شود و پیام جدید ارسال می‌شود
        if delete_old:
            try:
                await q.message.delete()
            except Exception:
                pass
    else:
        chat_id = update_or_query.effective_chat.id

    if photo:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            return
        except Exception as e:
            logging.warning("send_photo failed, falling back to text: %s", e)

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


async def edit_text_or_caption(query, text: str):
    """متن پیام را ویرایش می‌کند (چه پیام متنی باشد چه عکس با کپشن)"""
    try:
        if query.message.caption is not None or query.message.photo:
            await query.edit_message_caption(caption=text, parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logging.warning("edit message failed: %s", e)


async def show_next_profile(update, context, user_id: int, from_button: bool = False):
    """پروفایل بعدی را نشان می‌دهد (پروفایل‌های رد شده تکرار نمی‌شوند)"""
    seen = context.user_data.setdefault("seen", set())
    current = context.user_data.get("current_view")
    if current:
        seen.add(current)

    profile = get_next_profile(user_id, exclude=seen)
    if not profile:
        context.user_data["seen"] = set()
        context.user_data.pop("current_view", None)
        msg = "پروفایل دیگری پیدا نشد 😔\nبعداً دوباره امتحان کن."
        if from_button:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
        else:
            await update.message.reply_text(msg)
        return

    context.user_data["current_view"] = profile["user_id"]
    await send_profile(update, context, profile, with_actions=True, target_id=profile["user_id"])


# ==================== ثبت‌نام ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user_exists(user.id):
        u = get_user(user.id)
        if u and u.get("is_banned"):
            await update.message.reply_text("حساب شما مسدود شده است.")
            return ConversationHandler.END

        await update.message.reply_text(
            f"سلام {user.first_name}! 👋\nبه ربات دوست‌یابی دانشجویی خوش اومدی.",
            reply_markup=main_menu_keyboard(update.effective_user.id),
        )
        return ConversationHandler.END

    context.user_data["reg"] = {
        "username": user.username,
        "full_name": user.full_name or user.first_name,
    }

    await update.message.reply_text(
        "سلام! 👋\nبه ربات **دوست‌یابی دانشجویی** خوش اومدی.\n\n"
        "برای شروع باید پروفایلت رو کامل کنی.\n\n"
        "اول اسمت رو بنویس (مثلاً: امیر یا مینا):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return REG_NAME


def valid_name(name: str) -> bool:
    return 2 <= len(name) <= 30 and not name.startswith("/")


async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not valid_name(name):
        await update.message.reply_text("اسم باید بین ۲ تا ۳۰ کاراکتر باشه. دوباره بنویس:")
        return REG_NAME

    context.user_data["reg"]["full_name"] = name
    await update.message.reply_text(
        f"خوشحالم {name}! 😊\nحالا جنسیتت رو انتخاب کن:",
        reply_markup=gender_keyboard(),
    )
    return REG_GENDER


async def reg_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gender = query.data.replace("gender_", "")
    context.user_data["reg"]["gender"] = gender

    await query.edit_message_text(
        "به دنبال آشنایی با چه جنسیتی هستی؟",
        reply_markup=looking_keyboard(),
    )
    return REG_LOOKING


async def reg_looking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    looking = query.data.replace("looking_", "")
    context.user_data["reg"]["looking_for"] = looking

    await query.edit_message_text(
        "مقطع تحصیلت چیه؟",
        reply_markup=degree_keyboard(),
    )
    return REG_DEGREE


async def reg_degree(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    degree = query.data.replace("degree_", "")
    context.user_data["reg"]["degree"] = degree

    await query.edit_message_text(
        "ترم چند هستی؟",
        reply_markup=term_keyboard(),
    )
    return REG_TERM


async def reg_term(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    term = int(query.data.replace("term_", ""))
    context.user_data["reg"]["term"] = term

    await query.edit_message_text("سنت چند ساله؟ (فقط عدد بفرست، مثلاً ۲۱)")
    return REG_AGE


async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or not (16 <= int(text) <= 45):
        await update.message.reply_text("لطفاً یک سن معتبر بین ۱۶ تا ۴۵ وارد کن.")
        return REG_AGE

    context.user_data["reg"]["age"] = int(text)
    context.user_data["reg"]["interests"] = []

    await update.message.reply_text(
        "علایقت رو انتخاب کن (می‌تونی چند تا انتخاب کنی، بعد «تمام شد» رو بزن):",
        reply_markup=interests_keyboard(),
    )
    return REG_INTERESTS


async def reg_interests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "int_done":
        selected = context.user_data["reg"].get("interests", [])
        if not selected:
            await query.answer("حداقل یک علاقه انتخاب کن!", show_alert=True)
            return REG_INTERESTS
        await query.answer()

        await query.edit_message_text(
            "یه متن کوتاه درباره خودت بنویس (بیو):\n"
            "مثلاً: دانشجوی کامپیوتر، عاشق گیم و فیلم، دنبال دوست برای گپ زدن..."
        )
        return REG_BIO

    await query.answer()
    interest = query.data.replace("int_", "")
    selected = context.user_data["reg"].setdefault("interests", [])
    if interest in selected:
        selected.remove(interest)
    else:
        selected.append(interest)

    await query.edit_message_text(
        "علایقت رو انتخاب کن (می‌تونی چند تا انتخاب کنی، بعد «تمام شد» رو بزن):",
        reply_markup=interests_keyboard(selected),
    )
    return REG_INTERESTS


async def reg_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bio = update.message.text.strip()
    if len(bio) < 10:
        await update.message.reply_text("بیو حداقل ۱۰ کاراکتر باشه. دوباره بنویس:")
        return REG_BIO

    context.user_data["reg"]["bio"] = bio

    await update.message.reply_text(
        "حالا یه عکس از خودت بفرست.\n"
        "📸 گذاشتن عکس پروفایل اجباریه."
    )
    return REG_PHOTO


async def reg_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reg = context.user_data["reg"]

    if update.message.photo:
        photo = update.message.photo[-1]
        reg["photo_file_id"] = photo.file_id
    else:
        await update.message.reply_text(
            "📸 برای ساخت پروفایل حتماً باید عکس بفرستی.\n"
            "لطفاً یک عکس (به صورت Photo، نه فایل) ارسال کن."
        )
        return REG_PHOTO

    # ذخیره نهایی
    save_user(update.effective_user.id, {
        "username": reg.get("username"),
        "full_name": reg.get("full_name"),
        "gender": reg.get("gender"),
        "looking_for": reg.get("looking_for"),
        "degree": reg.get("degree"),
        "term": reg.get("term"),
        "age": reg.get("age"),
        "bio": reg.get("bio"),
        "interests": json.dumps(reg.get("interests", []), ensure_ascii=False),
        "photo_file_id": reg.get("photo_file_id"),
        "is_active": 1,
    })

    await update.message.reply_text(
        "🎉 پروفایلت با موفقیت ساخته شد!\n\n"
        "حالا می‌تونی از منوی زیر استفاده کنی:",
        reply_markup=main_menu_keyboard(update.effective_user.id),
    )
    return ConversationHandler.END


# ==================== منوی اصلی ====================
MENU_BUTTONS = {
    "🔍 پیدا کردن دوست",
    "💕 مچ‌های من",
    "👤 پروفایل من",
    "✏️ ویرایش پروفایل",
    "⚙️ تنظیمات",
    "ℹ️ راهنما",
    ADMIN_PANEL_BUTTON,
}


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🔍 پیدا کردن دوست":
        await find_friend(update, context)
    elif text == "💕 مچ‌های من":
        await show_matches(update, context)
    elif text == "👤 پروفایل من":
        await show_my_profile(update, context)
    elif text == "✏️ ویرایش پروفایل":
        await edit_profile_menu(update, context)
    elif text == "⚙️ تنظیمات":
        await settings_menu(update, context)
    elif text == "ℹ️ راهنما":
        await help_command(update, context)
    elif text == ADMIN_PANEL_BUTTON and is_admin(update.effective_user.id):
        await cmd_admin(update, context)
    else:
        await update.message.reply_text("از منوی زیر استفاده کن:", reply_markup=main_menu_keyboard(update.effective_user.id))


async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنها هندلر پیام‌های عادی: ویرایش پروفایل / منو / چت را تشخیص می‌دهد"""
    if not update.message:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else None

    # دکمه «پنل مالک» در منوی پایین (فقط ادمین)
    if text == ADMIN_PANEL_BUTTON and is_admin(user_id):
        context.user_data.pop("editing", None)
        context.user_data.pop("chatting_with", None)
        context.user_data.pop("admin_action", None)
        await cmd_admin(update, context)
        return

    # ادمین در حال ارسال متن پیام همگانی / متن تعمیر
    if is_admin(user_id) and context.user_data.get("admin_action"):
        if text in MENU_BUTTONS:
            context.user_data.pop("admin_action", None)
        elif text:
            await handle_admin_input(update, context)
            return
        else:
            await update.message.reply_text("فقط متن بفرست (یا /cancel).")
            return

    if not user_exists(user_id):
        await update.message.reply_text("اول باید ثبت‌نام کنی. /start رو بزن.")
        return

    u = get_user(user_id)
    if u and u.get("is_banned"):
        await update.message.reply_text("حساب شما مسدود شده است.")
        return

    # دکمه‌های منو همیشه اولویت دارند (و حالت ویرایش/چت را لغو می‌کنند)
    if text in MENU_BUTTONS:
        context.user_data.pop("editing", None)
        context.user_data.pop("chatting_with", None)
        await handle_menu(update, context)
        return

    # در حال ویرایش بیو/عکس
    if context.user_data.get("editing") in ("name", "bio", "photo", "report_reason"):
        await handle_edit_message(update, context)
        return

    # در حال چت
    if context.user_data.get("chatting_with"):
        await relay_message(update, context)
        return

    await update.message.reply_text("از منوی زیر استفاده کن:", reply_markup=main_menu_keyboard(update.effective_user.id))


async def find_friend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["seen"] = set()
    context.user_data.pop("current_view", None)
    await show_next_profile(update, context, update.effective_user.id)


async def show_my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("پروفایلی پیدا نشد.")
        return
    await send_profile(update, context, user, with_actions=False)


async def show_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    partners = get_matches(user_id)

    if not partners:
        await update.message.reply_text(
            "هنوز هیچ مچی نداری 💔\n"
            "برو «پیدا کردن دوست» و لایک کن تا مچ بشی!"
        )
        return

    buttons = []
    for pid in partners:
        p = get_user(pid)
        if not p:
            continue
        name = p.get("full_name") or f"کاربر {pid}"
        buttons.append([InlineKeyboardButton(f"👤 {name}", callback_data=f"viewmatch_{pid}")])

    await update.message.reply_text(
        f"💕 مچ‌های تو ({len(partners)} نفر):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def edit_profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏷 تغییر اسم", callback_data="edit_name")],
        [InlineKeyboardButton("📝 تغییر بیو", callback_data="edit_bio")],
        [InlineKeyboardButton("🖼 تغییر عکس", callback_data="edit_photo")],
        [InlineKeyboardButton("💡 تغییر علایق", callback_data="edit_interests")],
        [InlineKeyboardButton("👁 تغییر «دنبال چه کسی»", callback_data="edit_looking")],
    ]
    await update.message.reply_text(
        "چه قسمتی از پروفایلت رو می‌خوای ویرایش کنی؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    status = "فعال ✅" if user.get("is_active") else "غیرفعال ❌"
    keyboard = [
        [InlineKeyboardButton(f"وضعیت پروفایل: {status}", callback_data="toggle_active")],
        [InlineKeyboardButton("🗑 حذف کامل حساب", callback_data="delete_account")],
    ]
    await update.message.reply_text(
        "⚙️ تنظیمات:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 **راهنمای ربات دوست‌یابی دانشجویی**\n\n"
        "🔍 **پیدا کردن دوست**: پروفایل‌های مناسب رو می‌بینی و می‌تونی لایک کنی.\n"
        "💕 **مچ‌های من**: کسانی که متقابلاً لایک کردید.\n"
        "💬 **چت**: بعد از مچ می‌تونی داخل ربات چت کنی.\n"
        "📩 **درخواست آیدی**: اگر خواستی آیدی تلگرام طرف رو بدونی، اول ازش اجازه گرفته می‌شه.\n\n"
        "⚠️ قوانین:\n"
        "• احترام متقابل رعایت شود\n"
        "• محتوای نامناسب ممنوع است\n"
        "• در صورت تخلف، حساب مسدود می‌شود\n"
        "• پیام‌های چت ذخیره می‌شوند و ممکن است برای رسیدگی به گزارش‌ها توسط مالک بررسی شوند\n"
        "• با دکمه «⚠️ گزارش» می‌توانی تخلف را به مالک اطلاع بدهی\n\n"
        "ساخته شده با ❤️ برای دانشجویان"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ==================== کال‌بک‌ها ====================
async def enter_chat(query, context, partner_id: int):
    """کاربر را وارد حالت چت با partner می‌کند"""
    context.user_data.pop("editing", None)
    context.user_data["chatting_with"] = partner_id
    partner = get_user(partner_id)
    name = md(partner.get("full_name")) if partner else ""
    await query.message.reply_text(
        f"💬 چت با **{name or 'طرف مقابل'}** شروع شد.\n"
        f"هر پیامی بفرستی بهش می‌رسه.\n"
        f"برای خروج از چت بنویس: /stopchat\n\n"
        f"🔒 پیام‌های چت ذخیره می‌شن و ممکنه برای رسیدگی به گزارش‌ها توسط مالک ربات بررسی بشن.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup([["/stopchat"]], resize_keyboard=True),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هر کال‌بک فقط یک بار answer می‌شود (تا آلرت‌ها درست نمایش داده شوند)"""
    query = update.callback_query
    try:
        await _button_handler(update, context)
    finally:
        try:
            await query.answer()
        except Exception:
            pass


async def _button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id

    # دکمه‌های ادمین (پنل، گزارش‌ها، سابقه چت)
    if data.startswith(("adm_", "rep_", "bc_")):
        await admin_callback(update, context, data)
        return

    # لایک
    if data.startswith("like_"):
        target_id = int(data.replace("like_", ""))
        if is_blocked(target_id, user_id) or is_blocked(user_id, target_id):
            await query.answer("امکان لایک وجود ندارد.", show_alert=True)
            return

        result = add_like(user_id, target_id)
        me = get_user(user_id)
        name = md(me.get("full_name")) or "کسی"

        if result == "mutual":
            await edit_text_or_caption(query, "🎉 **مچ شدید!** حالا می‌تونید با هم چت کنید.\nاز منوی «💕 مچ‌های من» چت رو شروع کنید.")
            # اطلاع به طرف مقابل
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"🎉 **مچ جدید!**\n{name} هم شما را لایک کرده.\nاز منوی «💕 مچ‌های من» می‌توانید چت را شروع کنید.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        else:
            if result == "liked":
                # به طرف مقابل خبر بده که لایک شده
                try:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text=f"❤️ **{name}** شما را لایک کرد!\nتو هم می‌خوای لایکش کنی؟",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=like_notice_keyboard(user_id),
                    )
                except Exception:
                    pass
            await query.answer("لایک ثبت شد ❤️", show_alert=False)
            await show_next_profile(update, context, user_id, from_button=True)

    elif data.startswith("likeback_"):
        liker_id = int(data.replace("likeback_", ""))
        if is_blocked(liker_id, user_id) or is_blocked(user_id, liker_id):
            await query.answer("امکان لایک وجود ندارد.", show_alert=True)
            return
        liker = get_user(liker_id)
        if not liker:
            await query.answer("این کاربر دیگر وجود ندارد.", show_alert=True)
            return
        if is_matched(user_id, liker_id):
            await query.answer("شما قبلاً مچ شدید! از «💕 مچ‌های من» چت کنید.", show_alert=True)
            return

        result = add_like(user_id, liker_id)
        me = get_user(user_id)
        name = md(me.get("full_name")) or "کسی"
        if result == "mutual":
            await edit_text_or_caption(query, f"🎉 **مچ شدید!** شما و {md(liker.get('full_name'))} همدیگر را لایک کردید.\nاز منوی «💕 مچ‌های من» چت رو شروع کنید.")
            try:
                await context.bot.send_message(
                    chat_id=liker_id,
                    text=f"🎉 **مچ جدید!**\n{name} هم شما را لایک کرد.\nاز منوی «💕 مچ‌های من» می‌توانید چت را شروع کنید.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        else:
            await query.answer("لایک ثبت شد ❤️", show_alert=True)

    elif data.startswith("viewliker_"):
        liker_id = int(data.replace("viewliker_", ""))
        liker = get_user(liker_id)
        if not liker:
            await query.answer("این کاربر دیگر وجود ندارد.", show_alert=True)
            return
        await send_profile(
            update, context, liker,
            keyboard=liker_profile_keyboard(liker_id),
            delete_old=False,
        )

    elif data == "next_profile":
        await show_next_profile(update, context, user_id, from_button=True)

    elif data.startswith("block_"):
        target_id = int(data.replace("block_", ""))
        block_user(user_id, target_id)
        await query.answer("کاربر بلاک شد.", show_alert=True)
        if context.user_data.get("chatting_with") == target_id:
            context.user_data.pop("chatting_with", None)
        try:
            await query.message.delete()
        except Exception:
            pass

    elif data.startswith("report_"):
        target_id = int(data.replace("report_", ""))
        if target_id == user_id:
            await query.answer("نمی‌تونی خودت رو گزارش بدی.", show_alert=True)
            return
        await query.message.reply_text(
            "⚠️ دلیل گزارش چیه؟",
            reply_markup=report_reason_keyboard(target_id),
        )

    elif data.startswith("rr_"):
        parts = data.split("_")
        if parts[1] == "cancel":
            context.user_data.pop("editing", None)
            context.user_data.pop("report_target", None)
            await query.edit_message_text("گزارش لغو شد.")
            return
        target_id = int(parts[2])
        if parts[1] == "x":
            context.user_data["editing"] = "report_reason"
            context.user_data["report_target"] = target_id
            await query.edit_message_text("✍️ دلیل گزارش رو بنویس (برای لغو: /cancel):")
            return
        reason = REPORT_REASONS[int(parts[1])]
        result = await submit_report(context, user_id, target_id, reason)
        await query.edit_message_text(result)

    elif data.startswith("viewmatch_"):
        partner_id = int(data.replace("viewmatch_", ""))
        partner = get_user(partner_id)
        if not partner:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        text = format_profile(partner)
        await query.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=match_actions_keyboard(partner_id),
        )

    elif data.startswith("chat_"):
        partner_id = int(data.replace("chat_", ""))
        if not is_matched(user_id, partner_id):
            await query.answer("شما مچ نیستید!", show_alert=True)
            return

        req = get_chat_request(user_id, partner_id)

        if req and req["status"] == "accepted":
            await enter_chat(query, context, partner_id)

        elif req and req["status"] == "pending" and req["from_user"] == user_id:
            await query.answer("درخواستت قبلاً ارسال شده. منتظر جواب طرف مقابل باش.", show_alert=True)

        elif req and req["status"] == "pending" and req["from_user"] == partner_id:
            # طرف مقابل قبلاً درخواست داده، پس این‌جا یعنی قبول
            set_chat_request_status(req["id"], "accepted")
            me = get_user(user_id)
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=f"✅ **{md(me.get('full_name'))}** درخواست چتت رو قبول کرد!",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("💬 ورود به چت", callback_data=f"chat_{user_id}")]]
                    ),
                )
            except Exception:
                pass
            await enter_chat(query, context, partner_id)

        elif req and req["status"] == "declined" and req["from_user"] == user_id:
            await query.answer("طرف مقابل فعلاً تمایلی به چت نداره.", show_alert=True)

        else:
            me = get_user(user_id)
            name = md(me.get("full_name")) or "کسی"
            req_id = create_chat_request(user_id, partner_id)
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=(
                        f"💬 شما با **{name}** مچ شدید.\n"
                        f"اون می‌خواد باهاتون چت رو شروع کنه، درخواستش رو قبول می‌کنی؟"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=chat_request_keyboard(user_id),
                )
                await query.answer("درخواست چت ارسال شد. منتظر جواب باش ⏳", show_alert=True)
            except Exception:
                set_chat_request_status(req_id, "failed")
                await query.answer("نتونستم به طرف مقابل پیام بدم.", show_alert=True)

    elif data.startswith("chatreq_yes_"):
        requester_id = int(data.replace("chatreq_yes_", ""))
        req = get_chat_request(user_id, requester_id)
        if (not is_matched(user_id, requester_id) or not req
                or req["status"] != "pending" or req["from_user"] != requester_id):
            await query.edit_message_text("این درخواست دیگر معتبر نیست.")
            return

        set_chat_request_status(req["id"], "accepted")
        me = get_user(user_id)
        try:
            await context.bot.send_message(
                chat_id=requester_id,
                text=f"✅ **{md(me.get('full_name'))}** درخواست چتت رو قبول کرد!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("💬 ورود به چت", callback_data=f"chat_{user_id}")]]
                ),
            )
        except Exception:
            pass
        await query.edit_message_text("✅ درخواست رو قبول کردی.")
        await enter_chat(query, context, requester_id)

    elif data.startswith("chatreq_no_"):
        requester_id = int(data.replace("chatreq_no_", ""))
        req = get_chat_request(user_id, requester_id)
        if req and req["status"] == "pending" and req["from_user"] == requester_id:
            set_chat_request_status(req["id"], "declined")
            try:
                await context.bot.send_message(
                    chat_id=requester_id,
                    text="❌ طرف مقابل فعلاً تمایلی به چت نداره.",
                )
            except Exception:
                pass
        await query.edit_message_text("درخواست چت رد شد.")

    elif data.startswith("reqid_"):
        partner_id = int(data.replace("reqid_", ""))
        if not is_matched(user_id, partner_id):
            await query.answer("شما مچ نیستید!", show_alert=True)
            return

        me = get_user(user_id)
        name = md(me.get("full_name")) or "کسی"

        # ذخیره درخواست
        conn = get_db()
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute(
            "INSERT INTO id_requests (from_user, to_user, status, created_at) VALUES (?, ?, 'pending', ?)",
            (user_id, partner_id, now),
        )
        conn.commit()
        conn.close()

        try:
            await context.bot.send_message(
                chat_id=partner_id,
                text=(
                    f"📩 **درخواست آیدی**\n\n"
                    f"**{name}** می‌خواد آیدی تلگرامت رو بدونه.\n"
                    f"اجازه می‌دی؟"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=yes_no_keyboard("idreq", user_id),
            )
            await query.answer("درخواست ارسال شد. منتظر پاسخ باش.", show_alert=True)
        except Exception:
            await query.answer("نتونستم به طرف مقابل پیام بدم.", show_alert=True)

    elif data.startswith("idreq_yes_"):
        requester_id = int(data.replace("idreq_yes_", ""))
        me = get_user(user_id)
        my_username = me.get("username")

        if my_username:
            msg = f"✅ طرف مقابل اجازه داد!\n\nآیدی تلگرام: @{my_username}"
        else:
            msg = (
                f"✅ طرف مقابل اجازه داد!\n\n"
                f"اما طرف مقابل یوزرنیم عمومی نداره.\n"
                f"می‌تونی از طریق چت داخل ربات ادامه بدی."
            )

        try:
            await context.bot.send_message(requester_id, msg)
        except Exception:
            pass

        await query.edit_message_text("✅ اجازه دادی. آیدی‌ت برای طرف ارسال شد.")

    elif data.startswith("idreq_no_"):
        requester_id = int(data.replace("idreq_no_", ""))
        try:
            await context.bot.send_message(
                requester_id,
                "❌ طرف مقابل فعلاً تمایلی به دادن آیدی نداره.",
            )
        except Exception:
            pass
        await query.edit_message_text("درخواست رد شد.")

    elif data == "edit_name":
        await query.message.reply_text("اسم جدیدت رو بنویس:")
        context.user_data["editing"] = "name"

    elif data == "edit_bio":
        await query.message.reply_text("بیو جدیدت رو بفرست:")
        context.user_data["editing"] = "bio"
        return  # will be handled by message handler

    elif data == "edit_photo":
        await query.message.reply_text("عکس جدیدت رو بفرست:")
        context.user_data["editing"] = "photo"

    elif data == "edit_interests":
        user = get_user(user_id)
        current = json.loads(user.get("interests") or "[]")
        context.user_data["edit_interests"] = current
        await query.message.reply_text(
            "علایق جدیدت رو انتخاب کن:",
            reply_markup=interests_keyboard(current),
        )

    elif data == "edit_looking":
        await query.message.reply_text(
            "دنبال آشنایی با چه جنسیتی هستی؟",
            reply_markup=looking_keyboard(),
        )
        context.user_data["editing"] = "looking"

    elif data.startswith("looking_") and context.user_data.get("editing") == "looking":
        looking = data.replace("looking_", "")
        save_user(user_id, {"looking_for": looking})
        context.user_data.pop("editing", None)
        await query.edit_message_text(f"✅ تنظیم شد: {looking}")

    elif data.startswith("int_") and "edit_interests" in context.user_data:
        if data == "int_done":
            selected = context.user_data.get("edit_interests", [])
            save_user(user_id, {"interests": json.dumps(selected, ensure_ascii=False)})
            context.user_data.pop("edit_interests", None)
            await query.edit_message_text("✅ علایق به‌روزرسانی شد.")
        else:
            interest = data.replace("int_", "")
            selected = context.user_data.setdefault("edit_interests", [])
            if interest in selected:
                selected.remove(interest)
            else:
                selected.append(interest)
            await query.edit_message_text(
                "علایق جدیدت رو انتخاب کن:",
                reply_markup=interests_keyboard(selected),
            )

    elif data == "toggle_active":
        user = get_user(user_id)
        new_status = 0 if user.get("is_active") else 1
        save_user(user_id, {"is_active": new_status})
        status = "فعال ✅" if new_status else "غیرفعال ❌"
        await query.edit_message_text(f"وضعیت پروفایل: {status}")

    elif data == "delete_account":
        keyboard = [
            [
                InlineKeyboardButton("✅ بله، حذف کن", callback_data="confirm_delete"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel_delete"),
            ]
        ]
        await query.edit_message_text(
            "⚠️ مطمئنی می‌خوای حسابت کامل حذف بشه؟ این کار برگشت‌ناپذیره.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data == "confirm_delete":
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM likes WHERE from_user = ? OR to_user = ?", (user_id, user_id))
        c.execute("DELETE FROM matches WHERE user1 = ? OR user2 = ?", (user_id, user_id))
        c.execute("DELETE FROM blocks WHERE blocker = ? OR blocked = ?", (user_id, user_id))
        c.execute("DELETE FROM chat_requests WHERE from_user = ? OR to_user = ?", (user_id, user_id))
        c.execute("DELETE FROM id_requests WHERE from_user = ? OR to_user = ?", (user_id, user_id))
        conn.commit()
        conn.close()
        await query.edit_message_text("حسابت حذف شد. برای شروع دوباره /start بزن.")

    elif data == "cancel_delete":
        await query.edit_message_text("حذف لغو شد.")


async def handle_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش پیام‌های ویرایش پروفایل و چت"""
    user_id = update.effective_user.id
    editing = context.user_data.get("editing")

    if editing == "report_reason":
        reason = (update.message.text or "").strip()
        if not (3 <= len(reason) <= 300):
            await update.message.reply_text("دلیل باید بین ۳ تا ۳۰۰ کاراکتر باشه. دوباره بنویس (یا /cancel):")
            return
        target_id = context.user_data.pop("report_target", None)
        context.user_data.pop("editing", None)
        if not target_id:
            await update.message.reply_text("گزارش منقضی شده. دوباره از روی پروفایل گزارش بده.", reply_markup=main_menu_keyboard(update.effective_user.id))
            return
        result = await submit_report(context, user_id, target_id, reason)
        await update.message.reply_text(result, reply_markup=main_menu_keyboard(update.effective_user.id))
        return

    if editing == "name":
        if not update.message.text:
            await update.message.reply_text("لطفاً اسم را به صورت متن بفرست:")
            return
        name = update.message.text.strip()
        if not valid_name(name):
            await update.message.reply_text("اسم باید بین ۲ تا ۳۰ کاراکتر باشه. دوباره بنویس:")
            return
        save_user(user_id, {"full_name": name})
        context.user_data.pop("editing", None)
        await update.message.reply_text("✅ اسم به‌روزرسانی شد.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return

    if editing == "bio":
        if not update.message.text:
            await update.message.reply_text("لطفاً بیو را به صورت متن بفرست:")
            return
        bio = update.message.text.strip()
        if len(bio) < 10:
            await update.message.reply_text("بیو خیلی کوتاهه. دوباره بنویس:")
            return
        save_user(user_id, {"bio": bio})
        context.user_data.pop("editing", None)
        await update.message.reply_text("✅ بیو به‌روزرسانی شد.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return

    if editing == "photo":
        if update.message.photo:
            photo = update.message.photo[-1]
            save_user(user_id, {"photo_file_id": photo.file_id})
            context.user_data.pop("editing", None)
            await update.message.reply_text("✅ عکس به‌روزرسانی شد.", reply_markup=main_menu_keyboard(update.effective_user.id))
        else:
            await update.message.reply_text("📸 عکس پروفایل اجباریه، لطفاً یک عکس بفرست.")
        return



async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    partner_id = context.user_data.get("chatting_with")
    if not partner_id:
        return

    if update.message.text and update.message.text.strip() == "/stopchat":
        context.user_data.pop("chatting_with", None)
        await update.message.reply_text(
            "چت بسته شد.",
            reply_markup=main_menu_keyboard(update.effective_user.id),
        )
        return

    uid = update.effective_user.id
    if not is_matched(uid, partner_id) or not chat_accepted(uid, partner_id):
        context.user_data.pop("chatting_with", None)
        await update.message.reply_text(
            "این چت دیگر فعال نیست.",
            reply_markup=main_menu_keyboard(update.effective_user.id),
        )
        return

    partner = get_user(partner_id)
    if not partner or partner.get("is_banned"):
        context.user_data.pop("chatting_with", None)
        await update.message.reply_text(
            "این کاربر در دسترس نیست.",
            reply_markup=main_menu_keyboard(update.effective_user.id),
        )
        return

    me = get_user(update.effective_user.id)
    name = md(me.get("full_name")) or "طرف مقابل"

    # اگر طرف مقابل الان داخل چت با من نیست، دکمه «پاسخ» زیر پیام بگذار
    partner_state = context.application.user_data.get(partner_id, {})
    if partner_state.get("chatting_with") == uid:
        reply_markup = None
    else:
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("💬 پاسخ", callback_data=f"chat_{uid}")]]
        )

    try:
        if update.message.photo:
            await context.bot.send_photo(
                chat_id=partner_id,
                photo=update.message.photo[-1].file_id,
                caption=f"💬 از **{name}**:\n{md(update.message.caption)}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
            )
        elif update.message.text:
            await context.bot.send_message(
                chat_id=partner_id,
                text=f"💬 از **{name}**:\n{md(update.message.text)}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
            )
        elif update.message.voice:
            await context.bot.send_voice(
                chat_id=partner_id,
                voice=update.message.voice.file_id,
                caption=f"💬 ویس از **{name}**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
            )
        else:
            await update.message.reply_text("این نوع پیام پشتیبانی نمی‌شود.")
            return

        if update.message.photo:
            log_chat(uid, partner_id, "photo", update.message.caption or "")
        elif update.message.text:
            log_chat(uid, partner_id, "text", update.message.text)
        else:
            log_chat(uid, partner_id, "voice", "")

        await update.message.reply_text("✅ ارسال شد")
    except Exception as e:
        await update.message.reply_text("❌ پیام ارسال نشد. ممکن است طرف مقابل ربات را بلاک کرده باشد.")


async def stop_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("chatting_with", None)
    await update.message.reply_text("چت بسته شد.", reply_markup=main_menu_keyboard(update.effective_user.id))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("لغو شد.", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END


# ==================== ادمین و گزارش‌ها ====================
def report_admin_keyboard(rid: int, resolved: bool = False):
    rows = []
    if not resolved:
        rows.append([
            InlineKeyboardButton("✅ تأیید گزارش", callback_data=f"rep_ok_{rid}"),
            InlineKeyboardButton("✅ تأیید + مسدود", callback_data=f"rep_ban_{rid}"),
        ])
        rows.append([InlineKeyboardButton("❌ رد گزارش", callback_data=f"rep_no_{rid}")])
    rows.append([InlineKeyboardButton("📜 سابقه چت", callback_data=f"rep_chat_{rid}")])
    return InlineKeyboardMarkup(rows)


def report_reason_keyboard(target_id: int):
    rows = [
        [InlineKeyboardButton(r, callback_data=f"rr_{i}_{target_id}")]
        for i, r in enumerate(REPORT_REASONS)
    ]
    rows.append([InlineKeyboardButton("✍️ دلیل دیگر (می‌نویسم)", callback_data=f"rr_x_{target_id}")])
    rows.append([InlineKeyboardButton("↩️ انصراف", callback_data="rr_cancel")])
    return InlineKeyboardMarkup(rows)


async def send_report_to_admins(bot, rid: int):
    r = get_report(rid)
    if not r:
        return
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, report_text(r), reply_markup=report_admin_keyboard(rid))
        except Exception as e:
            logging.warning("could not send report to admin %s: %s", admin, e)


async def submit_report(context, reporter_id: int, target_id: int, reason: str) -> str:
    rid = create_report(reporter_id, target_id, reason)
    if rid is None:
        return "قبلاً درباره این کاربر گزارش دادی و هنوز در حال بررسیه."
    await send_report_to_admins(context.bot, rid)
    return f"✅ گزارشت (#{rid}) ثبت شد و برای مالک ارسال شد. نتیجه بررسی رو بهت اطلاع می‌دیم."


async def send_chat_log(bot, chat_id: int, a: int, b: int):
    msgs = get_chat_log(a, b)
    header = f"📜 سابقه چت بین:\n• {user_line(a)}\n• {user_line(b)}\n"
    if not msgs:
        await bot.send_message(chat_id, header + "\nهیچ پیامی ذخیره نشده است.")
        return

    header += f"(آخرین {len(msgs)} پیام)\n━━━━━━━━━━━━━━━\n"
    icons = {"text": "", "photo": "📷 ", "voice": "🎤 "}
    lines = []
    for m in msgs:
        content = m["content"] or {"photo": "[عکس]", "voice": "[ویس]"}.get(m["kind"], "")
        if len(content) > 1500:
            content = content[:1500] + "…"
        lines.append(f"[{fmt_date(m['created_at'])}] {short_name(m['from_user'])}: {icons.get(m['kind'], '')}{content}")

    chunks, cur = [], header
    for line in lines:
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for chunk in chunks:
        await bot.send_message(chat_id, chunk)


def admin_panel_content():
    st = get_stats()
    maint = is_maintenance()
    text = (
        "🛠 **پنل مالک**\n"
        "━━━━━━━━━━━━━━━\n"
        f"👥 کاربران: {st['users']} (فعال: {st['active']}، مسدود: {st['banned']})\n"
        f"💕 مچ‌ها: {st['matches']}\n"
        f"⚠️ گزارش‌های در انتظار: {st['pending_reports']}\n"
        f"وضعیت ربات: {'🔴 غیرفعال (حالت تعمیر)' if maint else '🟢 فعال'}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data="adm_broadcast")],
        [InlineKeyboardButton(
            "🟢 فعال کردن ربات" if maint else "🔴 غیرفعال کردن ربات برای عموم",
            callback_data="adm_maint",
        )],
        [InlineKeyboardButton("✏️ متن پیام حالت تعمیر", callback_data="adm_maintmsg")],
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="adm_users_0")],
        [InlineKeyboardButton("📋 گزارش‌های در انتظار", callback_data="adm_reports")],
    ])
    return text, keyboard


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text, kb = admin_panel_content()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"آیدی عددی تو: `{update.effective_user.id}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await send_pending_reports(context.bot, update.effective_chat.id)


async def send_pending_reports(bot, chat_id: int) -> int:
    reports = get_pending_reports()
    for r in reports:
        await bot.send_message(chat_id, report_text(r), reply_markup=report_admin_keyboard(r["id"]))
    return len(reports)


async def cmd_chatlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        a, b = int(context.args[0]), int(context.args[1])
    except Exception:
        await update.message.reply_text("استفاده: /chatlog آیدی_اول آیدی_دوم")
        return
    await send_chat_log(context.bot, update.effective_chat.id, a, b)


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        uid = int(context.args[0])
    except Exception:
        await update.message.reply_text("استفاده: /chats آیدی_عددی_کاربر")
        return
    partners = get_chat_partners(uid)
    if not partners:
        await update.message.reply_text("این کاربر هیچ چتی ندارد.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{short_name(p['partner'])} — {p['cnt']} پیام",
            callback_data=f"adm_log_{uid}_{p['partner']}",
        )]
        for p in partners[:30]
    ]
    await update.message.reply_text(
        f"💬 چت‌های {short_name(uid)}:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _set_ban(update: Update, context: ContextTypes.DEFAULT_TYPE, banned: int):
    if not is_admin(update.effective_user.id):
        return
    try:
        uid = int(context.args[0])
    except Exception:
        await update.message.reply_text("استفاده: /ban آیدی_عددی  یا  /unban آیدی_عددی")
        return
    if not user_exists(uid):
        await update.message.reply_text("کاربری با این آیدی پیدا نشد.")
        return
    save_user(uid, {"is_banned": banned})
    await update.message.reply_text(("🚫 مسدود شد: " if banned else "✅ رفع مسدودیت شد: ") + user_line(uid))


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_ban(update, context, 1)


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_ban(update, context, 0)


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """متنی که ادمین بعد از زدن دکمه‌های پنل می‌فرستد (پیام همگانی / متن تعمیر)"""
    action = context.user_data.pop("admin_action", None)
    text = update.message.text.strip()

    if action == "broadcast":
        context.user_data["broadcast_text"] = text
        n = len(get_broadcast_targets())
        await update.message.reply_text(
            f"👀 پیش‌نمایش:\n\n📢 پیام مالک:\n\n{text}\n\n━━━━━━━━━━━━━━━\nبرای {n} نفر ارسال بشه؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ ارسال برای همه", callback_data="bc_yes"),
                InlineKeyboardButton("❌ لغو", callback_data="bc_no"),
            ]]),
        )
    elif action == "maint_msg":
        set_setting("maintenance_msg", text)
        await update.message.reply_text("✅ متن پیام حالت تعمیر ذخیره شد.")


async def do_broadcast(bot, text: str, admin_chat_id: int):
    ok = fail = 0
    for uid in get_broadcast_targets():
        for attempt in range(2):
            try:
                await bot.send_message(uid, f"📢 پیام مالک:\n\n{text}")
                ok += 1
                break
            except Exception as e:
                if type(e).__name__ == "RetryAfter" and attempt == 0:
                    await asyncio.sleep(getattr(e, "retry_after", 1) + 1)
                    continue
                fail += 1
                break
        await asyncio.sleep(0.05)
    try:
        await bot.send_message(admin_chat_id, f"✅ ارسال پیام همگانی تمام شد.\nموفق: {ok}\nناموفق: {fail}")
    except Exception:
        pass


def users_page_content(page: int):
    total = count_users()
    pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    users = list_users(page * USERS_PER_PAGE)

    lines = [f"👥 کاربران ثبت‌نام‌شده (صفحه {page + 1} از {pages} — کل: {total})", "━━━━━━━━━━━━━━━"]
    if not users:
        lines.append("هنوز کسی ثبت‌نام نکرده.")
    for i, u in enumerate(users, start=page * USERS_PER_PAGE + 1):
        flags = ""
        if u.get("is_banned"):
            flags += " 🚫مسدود"
        elif not u.get("is_active"):
            flags += " ⏸غیرفعال"
        un = f"@{u['username']}" if u.get("username") else "بدون یوزرنیم"
        lines.append(
            f"{i}. {u.get('full_name') or 'بدون نام'} | {u.get('gender') or '—'} | "
            f"{u.get('age') or '—'} ساله\n"
            f"   📅 ثبت‌نام: {fmt_date(u.get('created_at'))}\n"
            f"   {un} | ID: {u['user_id']}{flags}"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"adm_users_{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"adm_users_{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton("🏠 پنل", callback_data="adm_home")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        await query.answer("دسترسی نداری.", show_alert=True)
        return

    if data == "adm_home":
        text, kb = admin_panel_content()
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    elif data == "adm_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await query.message.reply_text(
            "📢 متن پیام همگانی رو بفرست.\n"
            "مثلاً: ربات تا چند لحظه دیگه برای آپدیت از دسترس خارج خواهد شد.\n\n"
            "برای لغو: /cancel"
        )

    elif data == "bc_yes":
        text = context.user_data.pop("broadcast_text", None)
        if not text:
            await query.answer("پیامی برای ارسال نیست.", show_alert=True)
            return
        await query.edit_message_text("⏳ در حال ارسال پیام همگانی... (بعد از اتمام گزارش می‌دم)")
        context.application.create_task(do_broadcast(context.bot, text, query.message.chat_id))

    elif data == "bc_no":
        context.user_data.pop("broadcast_text", None)
        await query.edit_message_text("ارسال پیام همگانی لغو شد.")

    elif data == "adm_maint":
        new_state = "0" if is_maintenance() else "1"
        set_setting("maintenance", new_state)
        await query.answer(
            "ربات برای عموم غیرفعال شد 🔴" if new_state == "1" else "ربات دوباره فعال شد 🟢",
            show_alert=True,
        )
        text, kb = admin_panel_content()
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    elif data == "adm_maintmsg":
        context.user_data["admin_action"] = "maint_msg"
        current = get_setting("maintenance_msg") or DEFAULT_MAINTENANCE_MSG
        await query.message.reply_text(
            f"متن فعلی:\n{current}\n\nمتن جدید پیام حالت تعمیر رو بفرست (برای لغو: /cancel):"
        )

    elif data.startswith("adm_users_"):
        text, kb = users_page_content(int(data.replace("adm_users_", "")))
        await query.edit_message_text(text, reply_markup=kb)

    elif data == "adm_reports":
        n = await send_pending_reports(context.bot, query.message.chat_id)
        if n == 0:
            await query.answer("گزارش در انتظاری نیست ✅", show_alert=True)

    elif data.startswith("adm_log_"):
        _, _, a, b = data.split("_")
        await send_chat_log(context.bot, query.message.chat_id, int(a), int(b))

    elif data.startswith("rep_chat_"):
        r = get_report(int(data.replace("rep_chat_", "")))
        if not r:
            await query.answer("گزارش پیدا نشد.", show_alert=True)
            return
        await send_chat_log(context.bot, query.message.chat_id, r["reporter"], r["reported"])

    elif data.startswith(("rep_ok_", "rep_ban_", "rep_no_")):
        kind, rid = data.split("_", 1)[1].rsplit("_", 1)
        rid = int(rid)
        r = get_report(rid)
        if not r:
            await query.answer("گزارش پیدا نشد.", show_alert=True)
            return
        status = {"ok": "accepted", "ban": "banned", "no": "rejected"}[kind]
        if not resolve_report(rid, status, uid):
            await query.answer("این گزارش قبلاً بررسی شده.", show_alert=True)
            return

        if status == "banned" and user_exists(r["reported"]):
            save_user(r["reported"], {"is_banned": 1})

        # اطلاع نتیجه به گزارش‌دهنده
        if status == "rejected":
            msg = f"❌ گزارش شما (#{rid}) بررسی شد و توسط مالک رد شد.\nدلیل گزارش: {r['reason']}"
        else:
            msg = f"✅ گزارش شما (#{rid}) بررسی شد و با فرد برخورد شد.\nدلیل گزارش: {r['reason']}"
        try:
            await context.bot.send_message(r["reporter"], msg)
        except Exception:
            pass

        r = get_report(rid)
        await query.edit_message_text(
            report_text(r) + "\n📨 نتیجه برای گزارش‌دهنده ارسال شد.",
            reply_markup=report_admin_keyboard(rid, resolved=True),
        )


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قبل از همه هندلرها: حالت تعمیر و کاربران مسدود را متوقف می‌کند (ادمین‌ها مستثنی‌اند)"""
    user = update.effective_user
    if not user or is_admin(user.id):
        return

    msg = None
    if is_maintenance():
        msg = get_setting("maintenance_msg") or DEFAULT_MAINTENANCE_MSG
    else:
        u = get_user(user.id)
        if u and u.get("is_banned"):
            msg = "حساب شما مسدود شده است."

    if msg:
        try:
            if update.callback_query:
                await update.callback_query.answer(msg[:200], show_alert=True)
            elif update.message:
                await update.message.reply_text(msg)
        except Exception:
            pass
        raise ApplicationHandlerStop


# ==================== اجرا ====================
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    init_db()

    print("=" * 50)
    print("ربات دوست‌یابی دانشجویی در حال راه‌اندازی...")
    print("=" * 50)

    builder = Application.builder().token(TELEGRAM_TOKEN)

    app = builder.build()

    # ثبت‌نام
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_GENDER: [CallbackQueryHandler(reg_gender, pattern="^gender_")],
            REG_LOOKING: [CallbackQueryHandler(reg_looking, pattern="^looking_")],
            REG_DEGREE: [CallbackQueryHandler(reg_degree, pattern="^degree_")],
            REG_TERM: [CallbackQueryHandler(reg_term, pattern="^term_")],
            REG_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_age)],
            REG_INTERESTS: [CallbackQueryHandler(reg_interests, pattern="^int_")],
            REG_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_bio)],
            REG_PHOTO: [
                MessageHandler(filters.PHOTO, reg_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # قبل از همه: حالت تعمیر و کاربران مسدود
    app.add_handler(TypeHandler(Update, gate), group=-1)

    app.add_handler(conv)
    app.add_handler(CommandHandler("stopchat", stop_chat))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CommandHandler("chatlog", cmd_chatlog))
    app.add_handler(CommandHandler("chats", cmd_chats))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.VOICE, message_router))

    print("ربات دوست‌یابی دانشجویی روشن شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
