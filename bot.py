import hashlib
import json
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx пишет в лог адреса запросов, а в них зашит токен бота
logging.getLogger("httpx").setLevel(logging.WARNING)

CARDS = json.loads((BASE_DIR / "cards.json").read_text(encoding="utf-8"))


def pick_card(user_id: int, day: date) -> tuple[dict, bool]:
    """Карта дня зависит только от человека и даты: за сутки она не меняется."""
    seed = f"{user_id}:{day.isoformat()}".encode()
    digest = hashlib.sha256(seed).digest()
    card = CARDS[int.from_bytes(digest[:8], "big") % len(CARDS)]
    is_reversed = digest[8] % 2 == 1
    return card, is_reversed


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я вытягиваю карту дня. Жми /card и узнай, что приготовили арканы. "
        "Карта одна на сутки: повторные попытки судьба не одобряет."
    )


async def card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chosen, is_reversed = pick_card(update.effective_user.id, date.today())
    position = "перевёрнутая" if is_reversed else "прямая"
    meaning = chosen["reversed"] if is_reversed else chosen["upright"]
    await update.message.reply_text(
        f"🔮 <b>{chosen['name']}</b> ({position})\n\n{meaning}",
        parse_mode=ParseMode.HTML,
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Не найден TELEGRAM_BOT_TOKEN. Вставь токен в файл .env.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("card", card))
    app.run_polling()


if __name__ == "__main__":
    main()
