"""
AdexSiteSnapBot - a "Website Screenshot Bot" style Telegram bot.

Send it any URL (or use /shot, /mobile, /full) and it replies with a
screenshot of the page, rendered headlessly with Playwright (Chromium).

Setup
-----
1. pip install -r requirements.txt
2. playwright install --with-deps chromium   (only needed for local runs;
   the Docker image already has this baked in for Railway)
3. Copy .env.example to .env and set BOT_TOKEN
4. python bot.py
"""

import logging
import os
import re

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

URL_RE = re.compile(r"^(https?://)?[\w.-]+\.[a-zA-Z]{2,}(/\S*)?$")

DESKTOP_VIEWPORT = {"width": 1366, "height": 768}
MOBILE_VIEWPORT = {"width": 390, "height": 844}
NAV_TIMEOUT_MS = 30_000


def normalize_url(raw: str) -> str | None:
    raw = raw.strip()
    if not URL_RE.match(raw):
        return None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


async def take_screenshot(context: ContextTypes.DEFAULT_TYPE, url: str,
                           mobile: bool = False, full_page: bool = True) -> bytes:
    browser = context.application.bot_data["browser"]
    viewport = MOBILE_VIEWPORT if mobile else DESKTOP_VIEWPORT
    page = await browser.new_page(
        viewport=viewport,
        is_mobile=mobile,
        has_touch=mobile,
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
            if mobile
            else None
        ),
    )
    try:
        await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
        return await page.screenshot(full_page=full_page)
    finally:
        await page.close()


async def handle_shot_request(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               raw_url: str, mobile: bool = False, full_page: bool = True):
    url = normalize_url(raw_url)
    if not url:
        await update.message.reply_text(
            "That doesn't look like a valid URL. Try something like:\n`example.com`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
    status = await update.message.reply_text(f"📸 Capturing {url} ...")

    try:
        img_bytes = await take_screenshot(context, url, mobile=mobile, full_page=full_page)
    except Exception as exc:
        logger.warning("Screenshot failed for %s: %s", url, exc)
        await status.edit_text(
            f"❌ Couldn't capture that page. It may be down, blocking bots, or timed out.\n`{exc}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    mode = "mobile" if mobile else "desktop"
    await update.message.reply_photo(
        photo=img_bytes,
        caption=f"🌐 {url}\n📱 {mode} • {'full page' if full_page else 'viewport only'}",
    )
    await status.delete()


# --------------------------------------------------------------- commands --

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I'm *AdexSiteSnapBot*.\n\n"
        "Just send me a URL and I'll send back a full-page screenshot.\n\n"
        "Or use:\n"
        "/shot <url> - desktop, full page (default)\n"
        "/mobile <url> - mobile viewport, full page\n"
        "/view <url> - desktop, visible area only (no scrolling)",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Commands*\n"
        "/shot <url> - desktop full-page screenshot\n"
        "/mobile <url> - mobile full-page screenshot\n"
        "/view <url> - desktop viewport only, no scrolling\n\n"
        "Or just paste a URL with no command.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def shot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /shot <url>")
        return
    await handle_shot_request(update, context, context.args[0], mobile=False, full_page=True)


async def mobile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /mobile <url>")
        return
    await handle_shot_request(update, context, context.args[0], mobile=True, full_page=True)


async def view_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /view <url>")
        return
    await handle_shot_request(update, context, context.args[0], mobile=False, full_page=False)


async def on_plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not normalize_url(text):
        await update.message.reply_text(
            "Send me a URL (e.g. `example.com`) or use /help to see commands.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await handle_shot_request(update, context, text, mobile=False, full_page=True)


# -------------------------------------------------------- lifecycle hooks --

async def on_startup(app: Application):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    app.bot_data["playwright"] = playwright
    app.bot_data["browser"] = browser
    logger.info("Playwright Chromium launched.")


async def on_shutdown(app: Application):
    await app.bot_data["browser"].close()
    await app.bot_data["playwright"].stop()
    logger.info("Playwright Chromium closed.")


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN in your .env file (see .env.example).")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("shot", shot_cmd))
    app.add_handler(CommandHandler("mobile", mobile_cmd))
    app.add_handler(CommandHandler("view", view_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_plain_text))

    logger.info("Bot starting (polling mode)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
