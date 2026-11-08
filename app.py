import os
import time
import asyncio
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BATCH_IDLE_SECONDS = 300  # batch closes after 5 minutes without media

DB_PATH = "/data/media.db" if os.path.isdir("/data") else "media.db"
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS items ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "code TEXT, file_id TEXT, unique_id TEXT, kind TEXT, "
    "UNIQUE(code, unique_id))"
)
db.execute(
    "CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, code TEXT)"
)
db.execute(
    "CREATE TABLE IF NOT EXISTS pending ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "group_id TEXT, file_id TEXT, unique_id TEXT, kind TEXT)"
)
try:
    db.execute(
        "INSERT OR IGNORE INTO items (code, file_id, unique_id, kind) "
        "SELECT code, file_id, file_id, kind FROM media"
    )
except sqlite3.OperationalError:
    pass
db.commit()


def get_code(text):
    words = (text or "").split()
    return words[0].lower() if words else None


def get_file(msg):
    if msg.photo:
        return msg.photo[-1], "photo"
    if msg.video:
        return msg.video, "video"
    return None, None


def add_item(code, f, kind):
    cur = db.execute(
        "INSERT OR IGNORE INTO items (code, file_id, unique_id, kind) "
        "VALUES (?, ?, ?, ?)",
        (code, f.file_id, f.file_unique_id, kind),
    )
    db.commit()
    return cur.rowcount


def total_for(code):
    return db.execute(
        "SELECT COUNT(*) FROM items WHERE code = ?", (code,)
    ).fetchone()[0]


def finish_batch(context):
    code = context.chat_data.pop("batch", None)
    added = context.chat_data.pop("batch_count", 0)
    context.chat_data.pop("batch_time", None)
    if not code:
        return None
    return f"Done. Added {added} new item(s) to {code}. Total: {total_for(code)}"


async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    f, kind = get_file(msg)
    if not f:
        return

    # Batch mode: everything goes to the batch code
    batch_code = context.chat_data.get("batch")
    if batch_code:
        idle = time.time() - context.chat_data.get("batch_time", 0)
        if idle > BATCH_IDLE_SECONDS:
            finish_batch(context)  # expired, continue as normal
        else:
            added = add_item(batch_code, f, kind)
            context.chat_data["batch_count"] = (
                context.chat_data.get("batch_count", 0) + added
            )
            context.chat_data["batch_time"] = time.time()
            return

    code = get_code(msg.caption)
    gid = msg.media_group_id

    # Album: the caption on one item gives the code to all items
    if gid:
        if code:
            db.execute(
                "INSERT OR REPLACE INTO groups (group_id, code) VALUES (?, ?)",
                (gid, code),
            )
            rows = db.execute(
                "SELECT file_id, unique_id, kind FROM pending WHERE group_id = ?",
                (gid,),
            ).fetchall()
            for fid, uid, k in rows:
                db.execute(
                    "INSERT OR IGNORE INTO items (code, file_id, unique_id, kind) "
                    "VALUES (?, ?, ?, ?)",
                    (code, fid, uid, k),
                )
            db.execute("DELETE FROM pending WHERE group_id = ?", (gid,))
            db.commit()
            add_item(code, f, kind)
            await msg.reply_text(f"Album saved with code: {code}")
        else:
            row = db.execute(
                "SELECT code FROM groups WHERE group_id = ?", (gid,)
            ).fetchone()
            if row:
                add_item(row[0], f, kind)
            else:
                db.execute(
                    "INSERT INTO pending (group_id, file_id, unique_id, kind) "
                    "VALUES (?, ?, ?, ?)",
                    (gid, f.file_id, f.file_unique_id, kind),
                )
                db.commit()
        return

    # Single item with a caption
    if not code:
        return
    added = add_item(code, f, kind)
    total = total_for(code)
    if added:
        await msg.reply_text(f"Saved with code: {code} ({total} items)")
    else:
        await msg.reply_text(f"Already saved under: {code} ({total} items)")


async def batch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Use: /batch CODE")
        return
    code = context.args[0].lower()
    context.chat_data["batch"] = code
    context.chat_data["batch_count"] = 0
    context.chat_data["batch_time"] = time.time()
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Done", callback_data="done")]]
    )
    await msg.reply_text(
        f"Batch ON for code: {code}\n"
        "Send or forward your videos/photos now.\n"
        "It closes by itself after 5 minutes without media.",
        reply_markup=keyboard,
    )


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = finish_batch(context) or "No batch is running."
    await update.effective_message.reply_text(text)


async def done_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = finish_batch(context) or "No batch is running."
    await query.edit_message_text(text)


async def remove_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    target = msg.reply_to_message

    # Reply to a video/photo with /remove
    if target and (target.photo or target.video):
        f, kind = get_file(target)
        code = get_code(target.caption)
        if not code and target.media_group_id:
            row = db.execute(
                "SELECT code FROM groups WHERE group_id = ?",
                (target.media_group_id,),
            ).fetchone()
            if row:
                code = row[0]

        if code:
            cur = db.execute(
                "DELETE FROM items WHERE code = ? AND (unique_id = ? OR file_id = ?)",
                (code, f.file_unique_id, f.file_id),
            )
        else:
            cur = db.execute(
                "DELETE FROM items WHERE unique_id = ? OR file_id = ?",
                (f.file_unique_id, f.file_id),
            )
        db.commit()
        if cur.rowcount:
            await msg.reply_text("Removed.")
        else:
            await msg.reply_text("Not found in saved items.")
        return

    # /remove CODE
    if context.args:
        code = context.args[0].lower()
        cur = db.execute("DELETE FROM items WHERE code = ?", (code,))
        db.commit()
        if cur.rowcount:
            await msg.reply_text(f"Deleted {cur.rowcount} item(s) from code: {code}")
        else:
            await msg.reply_text("Nothing saved under that code.")
        return

    await msg.reply_text("Reply to a video with /remove, or send /remove CODE")


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
    app.add_handler(CommandHandler("remove", remove_item))
    app.add_handler(CommandHandler("batch", batch_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(done_button, pattern="^done$"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, save_media))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, send_media
        )
    )
    print("Bot is running...")
    app.run_polling()


main()
