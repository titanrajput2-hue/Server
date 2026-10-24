import logging
import os
import sqlite3
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ChatJoinRequestHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("keybot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "8990148297:AAFCs00KkgVMhfRuZFV-HcOgZlckzs-NZx0").strip()
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID", "-1004386058616").strip()
CHANNEL_INVITE_URL = os.getenv("CHANNEL_INVITE_URL", "https://t.me/+OE0QxHOLGUZlMWVl").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "6338276791").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/bot.db").strip()
COOLDOWN_SECONDS = 24 * 60 * 60

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable.")
if not CHANNEL_ID_RAW:
    raise RuntimeError("Missing CHANNEL_ID environment variable.")
if not ADMIN_IDS_RAW:
    raise RuntimeError("Missing ADMIN_IDS environment variable.")

try:
    CHANNEL_ID = int(CHANNEL_ID_RAW)
    ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()}
except ValueError as exc:
    raise RuntimeError("CHANNEL_ID and ADMIN_IDS must contain numeric Telegram IDs.") from exc

# Ensure the database directory exists (Railway Volume should be mounted at /data).
db_parent = Path(DATABASE_PATH).parent
db_parent.mkdir(parents=True, exist_ok=True)


def connect_db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with connect_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            last_key_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_value TEXT NOT NULL UNIQUE,
            assigned_to INTEGER,
            assigned_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS join_requests (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            requested_at INTEGER NOT NULL
        );
        """)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_keyboard(is_member: bool = False):
    if is_member:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Generate Key", callback_data="generate_key")]
        ])
    rows = []
    if CHANNEL_INVITE_URL:
        rows.append([InlineKeyboardButton("📢 Join Private Channel", url=CHANNEL_INVITE_URL)])
    rows.append([InlineKeyboardButton("✅ Verify", callback_data="verify_join")])
    return InlineKeyboardMarkup(rows)


async def check_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError as exc:
        log.warning("Membership check failed for %s: %s", user_id, exc)
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.effective_message:
        return
    with connect_db() as conn:
        conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user.id,))
    member = await check_membership(context, user.id)
    if member:
        await update.effective_message.reply_text(
            "✅ Verification successful!\n\nYou can generate one key every 24 hours.",
            reply_markup=main_keyboard(True),
        )
    else:
        await update.effective_message.reply_text(
            "🔒 To access the key generator, join our private Telegram channel first.\n\n"
            "1. Tap “Join Private Channel” and send a join request.\n"
            "2. Return here and tap “Verify”.",
            reply_markup=main_keyboard(False),
        )


async def verify_or_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "verify_join":
        if await check_membership(context, user_id):
            await query.edit_message_text(
                "✅ You are verified!\n\nPress below to generate your key.",
                reply_markup=main_keyboard(True),
            )
            return

        # Only approve if Telegram delivered a join-request update for this user.
        with connect_db() as conn:
            request = conn.execute(
                "SELECT user_id FROM join_requests WHERE user_id = ? AND chat_id = ?",
                (user_id, CHANNEL_ID),
            ).fetchone()

        if request:
            try:
                await context.bot.approve_chat_join_request(chat_id=CHANNEL_ID, user_id=user_id)
                with connect_db() as conn:
                    conn.execute("DELETE FROM join_requests WHERE user_id = ?", (user_id,))
                await query.edit_message_text(
                    "✅ Your join request was approved. Press Verify again in a moment, "
                    "or use /start to refresh your membership.",
                    reply_markup=main_keyboard(False),
                )
            except TelegramError as exc:
                log.warning("Could not approve join request for %s: %s", user_id, exc)
                await query.edit_message_text(
                    "I couldn't approve the request. Please make sure the bot is an admin "
                    "with permission to invite users, then try again.",
                    reply_markup=main_keyboard(False),
                )
        else:
            await query.edit_message_text(
                "No pending join request found. Tap Join Private Channel, submit a join request, "
                "then return here and press Verify again.",
                reply_markup=main_keyboard(False),
            )
        return

    if data == "generate_key":
        if not await check_membership(context, user_id):
            await query.edit_message_text(
                "❌ You are not currently a member of the required channel. Join it and verify first.",
                reply_markup=main_keyboard(False),
            )
            return

        now = int(time.time())
        with connect_db() as conn:
            conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
            row = conn.execute("SELECT last_key_at FROM users WHERE user_id = ?", (user_id,)).fetchone()
            last_key_at = row["last_key_at"] if row else None

            if last_key_at and now - int(last_key_at) < COOLDOWN_SECONDS:
                remaining = COOLDOWN_SECONDS - (now - int(last_key_at))
                hours = remaining // 3600
                minutes = (remaining % 3600) // 60
                await query.edit_message_text(
                    f"⏳ You have already received a key in the last 24 hours.\n"
                    f"Try again in about {hours}h {minutes}m.",
                    reply_markup=main_keyboard(True),
                )
                return

            available = conn.execute(
                "SELECT id, key_value FROM keys WHERE assigned_to IS NULL ORDER BY id LIMIT 1"
            ).fetchone()
            if not available:
                await query.edit_message_text(
                    "⚠️ No keys are available right now. Please try again later.",
                    reply_markup=main_keyboard(True),
                )
                return

            # Assign and set cooldown together in a database transaction.
            conn.execute(
                "UPDATE keys SET assigned_to = ?, assigned_at = ? WHERE id = ? AND assigned_to IS NULL",
                (user_id, now, available["id"]),
            )
            conn.execute(
                "UPDATE users SET last_key_at = ? WHERE user_id = ?",
                (now, user_id),
            )
            key_value = available["key_value"]

        from html import escape
        await query.edit_message_text(
            f"🎉 Your key:<br><code>{escape(key_value)}</code><br><br>"
            "You can generate your next key after 24 hours.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh")]
            ]),
        )
        return

    if data == "refresh":
        if await check_membership(context, user_id):
            await query.edit_message_text(
                "You can generate one key every 24 hours.",
                reply_markup=main_keyboard(True),
            )
        else:
            await query.edit_message_text(
                "Please join the required channel and verify first.",
                reply_markup=main_keyboard(False),
            )


async def addkeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_admin(user.id):
        await message.reply_text("⛔ This command is for bot admins only.")
        return
    replied = message.reply_to_message
    if not replied or not replied.text:
        await message.reply_text(
            "Reply to a message containing one key per line with /addkeys.\n\n"
            "Example: send your key list, then reply to that message with /addkeys."
        )
        return
    values = [line.strip() for line in replied.text.splitlines() if line.strip()]
    added = 0
    duplicates = 0
    with connect_db() as conn:
        for value in values:
            try:
                conn.execute("INSERT INTO keys(key_value) VALUES (?)", (value,))
                added += 1
            except sqlite3.IntegrityError:
                duplicates += 1
    await message.reply_text(
        f"✅ Bulk import complete.\nAdded: {added}\nDuplicates skipped: {duplicates}"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_admin(user.id):
        await message.reply_text("⛔ This command is for bot admins only.")
        return
    with connect_db() as conn:
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        available = conn.execute("SELECT COUNT(*) AS n FROM keys WHERE assigned_to IS NULL").fetchone()["n"]
        assigned = conn.execute("SELECT COUNT(*) AS n FROM keys WHERE assigned_to IS NOT NULL").fetchone()["n"]
        pending = conn.execute("SELECT COUNT(*) AS n FROM join_requests").fetchone()["n"]
    await message.reply_text(
        f"📊 Bot statistics\nUsers seen: {users}\nAvailable keys: {available}\n"
        f"Assigned keys: {assigned}\nPending join requests recorded: {pending}"
    )


async def join_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    if not request or request.chat.id != CHANNEL_ID:
        return
    with connect_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO join_requests(user_id, chat_id, requested_at) VALUES (?, ?, ?)",
            (request.from_user.id, request.chat.id, int(time.time())),
        )
    # Intentionally do not auto-approve here. The user must press Verify in the bot.
    log.info("Recorded join request from user %s", request.from_user.id)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            "Commands:\n/start - Open key menu\n"
            "/addkeys - Reply to a message containing keys, one per line\n"
            "/stats - Admin statistics"
        )


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("addkeys", addkeys_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(verify_or_generate))
    app.add_handler(ChatJoinRequestHandler(join_request_handler))
    log.info("Bot is starting with long polling.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
