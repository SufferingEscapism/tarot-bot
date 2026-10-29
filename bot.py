import hashlib
import json
import logging
import os
import random
from contextlib import suppress
from datetime import date
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "images"
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx пишет в лог адреса запросов, а в них зашит токен бота
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

DATA = json.loads(
    (BASE_DIR / "data" / "tarot_major_arcana.json").read_text(encoding="utf-8")
)
CARDS = DATA["cards"]
SPHERE_NAMES = {s["id"]: s["name_ru"] for s in DATA["meta"]["spheres"]}

# «Общая ситуация» показывается у карты дня и у дополнительных карт,
# остальные сферы становятся кнопками «Карта на тему»
TOPICS = [sphere for sphere in SPHERE_NAMES if sphere != "general"]
TOPIC_ICONS = {
    "career": "💼",
    "finance": "💰",
    "love": "❤️",
    "health": "🩺",
    "advice": "💡",
}
# в файле сфера называется «Совет дня», но карта на тему выпадает случайно
TOPIC_TITLES = {"advice": "Совет"}

# приписка про здоровье берётся из примечания к тону в самом файле
DISCLAIMERS = {
    "health": next(
        (
            sentence.strip() + "."
            for sentence in DATA["meta"]["tone_note"].split(".")
            if "здоровь" in sentence
        ),
        "",
    )
}

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

WELCOME = (
    "Привет{name}! 🥂\n\n"
    "Я «Арканы и стаканы»: тяну карты таро для своих.\n\n"
    "Что я умею:\n"
    "🔮 <b>Карта дня</b>: одна на сутки, до завтра не меняется.\n"
    "🃏 <b>Ещё карта</b>: если расклад не понравился, тасуем колоду заново.\n"
    "🎯 <b>Карта на тему</b>: работа, финансы, любовь, здоровье или просто совет.\n\n"
    "Выбирай кнопкой. Всё это для настроения, а не для принятия решений. 🍷"
)


def pick_card(user_id: int, day: date) -> tuple[dict, bool]:
    """Карта дня зависит только от человека и даты: за сутки она не меняется."""
    seed = f"{user_id}:{day.isoformat()}".encode()
    digest = hashlib.sha256(seed).digest()
    card = CARDS[int.from_bytes(digest[:8], "big") % len(CARDS)]
    is_reversed = digest[8] % 2 == 1
    return card, is_reversed


def draw_random() -> tuple[dict, bool]:
    """Каждый раз новая карта: как будто колоду перетасовали заново."""
    return random.choice(CARDS), random.random() < 0.5


def image_path(card: dict) -> Path | None:
    """Картинка карты лежит в images/ и называется по её id, например major_00.jpg."""
    for extension in IMAGE_EXTENSIONS:
        path = IMAGES_DIR / f"{card['id']}{extension}"
        if path.exists():
            return path
    return None


def header(card: dict, is_reversed: bool, prefix: str = "") -> str:
    position = "перевёрнутая" if is_reversed else "прямая"
    return f"{prefix}<b>{escape(card['name_ru'])}</b> · {card['roman']} ({position})"


def card_text(card: dict, is_reversed: bool, sphere: str) -> str:
    side = card["reversed" if is_reversed else "upright"]
    return escape(side["spheres"][sphere], quote=False)


def keywords(card: dict, is_reversed: bool) -> str:
    side = card["reversed" if is_reversed else "upright"]
    return f"<i>{escape(side['short'], quote=False)}</i>"


def day_reply(user_id: int) -> tuple[dict, str]:
    card, is_reversed = pick_card(user_id, date.today())
    text = (
        f"{header(card, is_reversed, '🔮 Карта дня: ')}\n"
        f"{keywords(card, is_reversed)}\n\n"
        f"{card_text(card, is_reversed, 'general')}\n\n"
        "<i>Карта дня одна на сутки. Если хочешь ещё, можно перетасовать колоду.</i>"
    )
    return card, text


def extra_reply() -> tuple[dict, str]:
    card, is_reversed = draw_random()
    text = (
        "🃏 Тасую колоду заново...\n\n"
        f"{header(card, is_reversed)}\n"
        f"{keywords(card, is_reversed)}\n\n"
        f"{card_text(card, is_reversed, 'general')}"
    )
    return card, text


def topic_reply(topic: str) -> tuple[dict, str]:
    card, is_reversed = draw_random()
    text = (
        f"{topic_label(topic)}\n"
        f"{header(card, is_reversed)}\n\n"
        f"{card_text(card, is_reversed, topic)}"
    )
    if DISCLAIMERS.get(topic):
        text += f"\n\n<i>{escape(DISCLAIMERS[topic], quote=False)}</i>"
    return card, text


def topic_label(topic: str) -> str:
    title = TOPIC_TITLES.get(topic, SPHERE_NAMES[topic])
    return f"{TOPIC_ICONS.get(topic, '✨')} {title}"


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔮 Карта дня", callback_data="day")],
            [InlineKeyboardButton("🃏 Ещё карта (тасуем колоду)", callback_data="extra")],
            [InlineKeyboardButton("🎯 Карта на тему", callback_data="topics")],
        ]
    )


def topics_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(topic_label(topic), callback_data=f"topic:{topic}")]
        for topic in TOPICS
    ]
    rows.append([InlineKeyboardButton("← Назад", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


async def send(message: Message, text: str, card: dict | None = None) -> None:
    """Картинка (если она есть) идёт отдельным сообщением перед текстом.
    Любой ответ заканчивается главным меню: можно сразу тянуть следующую карту."""
    path = image_path(card) if card else None
    if path:
        try:
            with path.open("rb") as photo:
                await message.reply_photo(photo=photo)
        except TelegramError:
            logger.exception("Не удалось отправить картинку %s", path.name)
    await message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard()
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    first_name = update.effective_user.first_name
    name = f", {escape(first_name, quote=False)}" if first_name else ""
    await send(update.message, WELCOME.format(name=name))


async def card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chosen, text = day_reply(update.effective_user.id)
    await send(update.message, text, chosen)


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Что тянем?", reply_markup=main_keyboard())


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data

    # «Карта на тему» и «Назад» только меняют кнопки под текущим сообщением
    if action in ("topics", "menu"):
        keyboard = topics_keyboard() if action == "topics" else main_keyboard()
        with suppress(BadRequest):
            await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    if action == "day":
        chosen, text = day_reply(query.from_user.id)
    elif action == "extra":
        chosen, text = extra_reply()
    elif action.startswith("topic:") and action.removeprefix("topic:") in TOPICS:
        chosen, text = topic_reply(action.removeprefix("topic:"))
    else:
        return

    # у старого сообщения кнопки убираем, чтобы в чате не копились меню
    with suppress(BadRequest):
        await query.edit_message_reply_markup(reply_markup=None)
    await send(query.message, text, chosen)


async def set_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Начать сначала"),
            BotCommand("card", "Карта дня"),
            BotCommand("menu", "Показать кнопки"),
        ]
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Не найден TELEGRAM_BOT_TOKEN. Вставь токен в файл .env.")

    app = Application.builder().token(token).post_init(set_commands).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("card", card))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()


if __name__ == "__main__":
    main()
