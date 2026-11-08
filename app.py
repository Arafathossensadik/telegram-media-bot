import os
import sqlite3
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

DB_PATH = "/data/media.db" if os.path.isdir("/data") else "media.db"
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS media (code TEXT PRIMARY KEY, file_id TEXT, kind TEXT)"
)
db.commit()


async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves a photo/video using its caption as the code."""
    msg = update.effective_message
    if not msg or not msg.caption:
        return

    code = msg.caption.strip().split()[0].lower()

    if msg.photo:
        file_id, kind = msg.photo[-1].file_id, "photo"
    elif msg.video:
        file_id, kind = msg.video.file_id, "video"
    else:
        return

    db.execute(
        "INSERT OR REPLACE INTO media (code, file_id, kind) VALUES (?, ?, ?)",
        (code, file_id, kind),
    )
    db.commit()
    await msg.reply_text(f"Saved with code: {code}")


async def send_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """In private chat: you send a code, the bot sends the media."""
    code = update.message.text.strip().lower()
    row = db.execute(
        "SELECT file_id, kind FROM media WHERE code = ?", (code,)
    ).fetchone()

    if not row:
        await update.message.reply_text("No media found for that code.")
        return

    file_id, kind = row
    if kind == "photo":
        await update.message.reply_photo(file_id)
    else:
        await update.message.reply_video(file_id)


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


if _name_ == "_main_":
    main()
