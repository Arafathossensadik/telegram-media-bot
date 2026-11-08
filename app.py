import os
import asyncio
import sqlite3
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

DB_PATH = "/data/media.db" if os.path.isdir("/data") else "media.db"
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS items ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "code TEXT, file_id TEXT, unique_id TEXT, kind TEXT, "
    "UNIQUE(code, unique_id))"
)
try:
    db.execute(
        "INSERT OR IGNORE INTO items (code, file_id, unique_id, kind) "
        "SELECT code, file_id, file_id, kind FROM media"
    )
except sqlite3.OperationalError:
    pass
db.commit()


async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.caption:
        return

    code = msg.caption.strip().split()[0].lower()

    if msg.photo:
        f, kind = msg.photo[-1], "photo"
    elif msg.video:
        f, kind = msg.video, "video"
    else:
        return

    cur = db.execute(
        "INSERT OR IGNORE INTO items (code, file_id, unique_id, kind) "
        "VALUES (?, ?, ?, ?)",
        (code, f.file_id, f.file_unique_id, kind),
    )
    db.commit()

    total = db.execute(
        "SELECT COUNT(*) FROM items WHERE code = ?", (code,)
    ).fetchone()[0]

    if cur.rowcount:
        await msg.reply_text(f"Saved with code: {code} ({total} items)")
    else:
        await msg.reply_text(f"Already saved under: {code} ({total} items)")


async def send_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().lower()
    rows = db.execute(
        "SELECT file_id, kind FROM items WHERE code = ? ORDER BY id", (code,)
    ).fetchall()

    if not rows:
        await update.message.reply_text("No media found for that code.")
        return

    for file_id, kind in rows:
        if kind == "photo":
            await update.message.reply_photo(file_id)
        else:
            await update.message.reply_video(file_id)
        await asyncio.sleep(0.4)


def main():
    if not TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, save_media))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, send_media
        )
    )
    print("Bot is running...")
    app.run_polling()


main()
