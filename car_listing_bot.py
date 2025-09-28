import os
import re
import logging
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv
from telegram import Update, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Load env ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Set it in .env or Railway Variables.")
if not ADMIN_CHAT_ID or not ADMIN_CHAT_ID.isdigit():
    raise RuntimeError("ADMIN_CHAT_ID missing or not numeric.")

ADMIN_CHAT_ID_INT = int(ADMIN_CHAT_ID)

# --- Data model ---
@dataclass
class CarListing:
    make: str = ""
    model: str = ""
    year: str = ""
    mileage: str = ""
    price: str = ""           # <-- NEW
    vin: str = ""
    photos: List[str] = field(default_factory=list)  # Telegram file_ids

# --- States ---
(S_MAKE, S_MODEL, S_YEAR, S_MILEAGE, S_PRICE, S_VIN, S_PHOTOS) = range(7)  # <-- NEW S_PRICE

# --- Validators ---
VIN_REGEX = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)  # 17 chars, no I/O/Q
PRICE_CLEANER = re.compile(r"[^\d\.]")  # remove everything except digits and dot

def is_valid_year(t: str) -> bool:
    return t.isdigit() and 1900 <= int(t) <= 2100

def is_valid_mileage(t: str) -> bool:
    raw = t.replace(",", "")
    return raw.isdigit()

def is_valid_vin(t: str) -> bool:
    v = t.strip().upper().replace(" ", "")
    return bool(VIN_REGEX.match(v))

def normalize_price(p: str) -> str:
    """
    Accepts formats like $12,500, 12500, 12,500.00
    Returns a normalized string like '12500.00' (two decimals).
    """
    cleaned = PRICE_CLEANER.sub("", p)  # remove $, commas, spaces, etc. keep digits and dot
    if cleaned.count(".") > 1:
        return ""  # too many dots => invalid
    # if no dot, treat as integer dollars
    try:
        val = float(cleaned) if "." in cleaned else float(int(cleaned))
        return f"{val:.2f}"
    except Exception:
        return ""

def format_currency(normalized: str) -> str:
    # normalized must be like '12500.00'
    try:
        val = float(normalized)
        # add thousand separators
        whole, frac = f"{val:.2f}".split(".")
        whole_with_commas = "{:,}".format(int(whole))
        return f"${whole_with_commas}.{frac}"
    except Exception:
        return normalized

def fmt_listing(lst: CarListing) -> str:
    price_line = f"• Price: *{format_currency(lst.price)}*\n" if lst.price else ""
    return (
        "📋 *New Car Listing*\n"
        f"• Make: *{lst.make}*\n"
        f"• Model: *{lst.model}*\n"
        f"• Year: *{lst.year}*\n"
        f"• Mileage: *{lst.mileage}*\n"
        f"{price_line}"
        f"• VIN: *{lst.vin}*\n"
        f"• Photos: {len(lst.photos)}"
    )

# --- Commands ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["listing"] = CarListing()
    await update.message.reply_text(
        "Welcome! We'll collect: make, model, year, mileage, price, VIN, and up to 12 photos.\n"
        "Type /cancel anytime to stop.\n\n"
        "First, what's the *Make*?"
    )
    return S_MAKE

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Your chat ID is: `{update.effective_chat.id}`",
        parse_mode="Markdown"
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Canceled. 👋")
    return ConversationHandler.END

# --- Steps ---
async def step_make(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["listing"].make = update.message.text.strip()
    await update.message.reply_text("Great. Now the *Model*?")
    return S_MODEL

async def step_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["listing"].model = update.message.text.strip()
    await update.message.reply_text("Year? (e.g., 2018)")
    return S_YEAR

async def step_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = update.message.text.strip()
    if not is_valid_year(year):
        await update.message.reply_text("Send a valid 4-digit year (e.g., 2018).")
        return S_YEAR
    context.user_data["listing"].year = year
    await update.message.reply_text("Mileage? (numbers only, e.g., 72,500)")
    return S_MILEAGE

async def step_mileage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mileage = update.message.text.strip()
    if not is_valid_mileage(mileage):
        await update.message.reply_text("Send a valid mileage (e.g., 72500).")
        return S_MILEAGE
    context.user_data["listing"].mileage = mileage.replace(",", "")
    await update.message.reply_text("Price? (e.g., 12,500 or $12500)")
    return S_PRICE

async def step_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    normalized = normalize_price(raw)
    if not normalized:
        await update.message.reply_text("Invalid price. Send a number like 12500 or $12,500.00")
        return S_PRICE
    context.user_data["listing"].price = normalized
    await update.message.reply_text("VIN? (17 characters, no I/O/Q)")
    return S_VIN

async def step_vin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vin = update.message.text.strip().upper().replace(" ", "")
    if not is_valid_vin(vin):
        await update.message.reply_text("Invalid VIN. Must be 17 chars (no I, O, Q). Try again.")
        return S_VIN
    context.user_data["listing"].vin = vin
    await update.message.reply_text(
        "Now send up to *12 photos*. You can send them all at once or one by one.\n"
        "When finished, send /done."
    )
    return S_PHOTOS

async def step_collect_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    listing: CarListing = context.user_data["listing"]
    if update.message.photo:
        if len(listing.photos) >= 12:
            await update.message.reply_text("You already added 12 photos. Send /done.")
            return S_PHOTOS
        file_id = update.message.photo[-1].file_id
        listing.photos.append(file_id)
        await update.message.reply_text(f"Photo saved ({len(listing.photos)}/12). Send more or /done.")
    else:
        await update.message.reply_text("Please send a *photo* (or /done).")
    return S_PHOTOS

async def step_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    listing: CarListing = context.user_data.get("listing")
    if not listing:
        await update.message.reply_text("No active listing. Send /start to begin.")
        return ConversationHandler.END

    caption = fmt_listing(listing)

    # Send to admin
    if listing.photos:
        media = []
        for i, fid in enumerate(listing.photos):
            if i == 0:
                media.append(InputMediaPhoto(media=fid, caption=caption, parse_mode="Markdown"))
            else:
                media.append(InputMediaPhoto(media=fid))
        await context.bot.send_media_group(chat_id=ADMIN_CHAT_ID_INT, media=media)
    else:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID_INT, text=caption, parse_mode="Markdown")

    await update.message.reply_text("✅ Submitted! Thank you.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Main ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            S_MAKE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_make)],
            S_MODEL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, step_model)],
            S_YEAR:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_year)],
            S_MILEAGE:[MessageHandler(filters.TEXT & ~filters.COMMAND, step_mileage)],
            S_PRICE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, step_price)],   # <-- NEW
            S_VIN:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_vin)],
            S_PHOTOS: [
                MessageHandler(filters.PHOTO, step_collect_photo),
                CommandHandler("done", step_done),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Long polling keeps it simple for Railway
    logging.info("🚀 Bot starting…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
