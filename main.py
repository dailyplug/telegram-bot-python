import os
import telebot
from openai import OpenAI

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL = "@marinadnews"
ADMIN_ID = 6056292876

bot = telebot.TeleBot(TOKEN)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def is_admin(message):
    return message.from_user.id == ADMIN_ID


def send_long_message(message, text):
    """Telegram не принимает сообщения длиннее 4096 символов."""
    max_length = 4000

    for i in range(0, len(text), max_length):
        bot.reply_to(message, text[i:i + max_length])


# =========================
# /start
# =========================

@bot.message_handler(commands=["start"])
def start(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    bot.reply_to(
        message,
        "🧂 МАРИНАД на связи.\n\n"
        "Бот работает.\n\n"
        "/myid — показать Telegram ID\n"
        "/publish ТЕКСТ — опубликовать пост в канале\n"
        "/ai ТЕКСТ — попросить ИИ обработать материал"
    )


# =========================
# /myid
# =========================

@bot.message_handler(commands=["myid"])
def myid(message):
    bot.reply_to(
        message,
        f"Твой Telegram ID: {message.from_user.id}"
    )


# =========================
# /publish
# =========================

@bot.message_handler(commands=["publish"])
def publish(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    text = message.text.replace("/publish", "", 1).strip()

    if not text:
        bot.reply_to(
            message,
            "⚠️ После /publish нужно написать текст поста."
        )
        return

    try:
        bot.send_message(CHANNEL, text)

        bot.reply_to(
            message,
            "✅ Пост опубликован в @marinadnews."
        )

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ Ошибка публикации:\n{e}"
        )


# =========================
# /ai
# =========================

@bot.message_handler(commands=["ai"])
def ai(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    prompt = message.text.replace("/ai", "", 1).strip()

    if not prompt:
        bot.reply_to(
            message,
            "⚠️ После /ai нужно написать задачу.\n\n"
            "Например:\n"
            "/ai Напиши короткий пост о росте цен на нефть."
        )
        return

    bot.reply_to(message, "🧠 Обрабатываю...")

    try:
        response = client.responses.create(
            model="gpt-6-astra",
            instructions="""
Ты — ИИ-редактор Telegram-канала «МАРИНАД».

Слоган канала: «Вся соль здесь».

Твоя задача — помогать создавать современные русскоязычные
новостные посты для Telegram.

Стиль:
— живой;
— быстрый;
— современный;
— информативный;
— без канцелярита;
— без лишних вступлений;
— допускается умеренный кликбейт, но нельзя искажать факты;
— не выдумывай факты, цифры, цитаты или источники;
— не добавляй дату в начало поста;
— не используй старый слоган «Новости без лишнего шума»;
— не добавляй подпись @marinadnews, если пользователь отдельно не попросил;
— обычно стремись к 300–700 символам, если задача не требует другого объёма.

Если пользователь прислал новость или материал — сначала пойми суть,
затем предложи готовый вариант публикации.

Если данных недостаточно для утверждения факта, прямо укажи на это.
""",
            input=prompt
        )

        result = response.output_text.strip()

        if not result:
            bot.reply_to(
                message,
                "⚠️ ИИ не вернул текст."
            )
            return

        send_long_message(message, result)

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ Ошибка OpenAI:\n{e}"
        )


# =========================
# Запуск бота
# =========================

print("🧂 МАРИНАД | Бот запущен")

bot.infinity_polling()
