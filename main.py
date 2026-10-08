import os
import telebot
from openai import OpenAI

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

CHANNEL = "@marinadnews"
ADMIN_ID = 6056292876

bot = telebot.TeleBot(TOKEN)

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)


def is_admin(message):
    return message.from_user.id == ADMIN_ID


def send_long_message(chat_id, text):
    """Telegram имеет ограничение на длину сообщения."""
    max_length = 4000

    if len(text) <= max_length:
        bot.send_message(chat_id, text)
        return

    for i in range(0, len(text), max_length):
        bot.send_message(chat_id, text[i:i + max_length])


@bot.message_handler(commands=["start"])
def start(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    bot.reply_to(
        message,
        "🧂 МАРИНАД на связи.\n\n"
        "Бот работает.\n\n"
        "/myid — узнать Telegram ID\n"
        "/publish ТЕКСТ — опубликовать пост\n"
        "/ai ТЕКСТ — попросить AI обработать текст"
    )


@bot.message_handler(commands=["myid"])
def myid(message):
    bot.reply_to(
        message,
        f"Твой Telegram ID: {message.from_user.id}"
    )


@bot.message_handler(commands=["publish"])
def publish(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    text = message.text[len("/publish"):].strip()

    if not text:
        bot.reply_to(
            message,
            "Использование:\n/publish ТЕКСТ"
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


@bot.message_handler(commands=["ai"])
def ai(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    prompt = message.text[len("/ai"):].strip()

    if not prompt:
        bot.reply_to(
            message,
            "Использование:\n/ai ТЕКСТ"
        )
        return

    bot.reply_to(message, "🧠 Обрабатываю...")

    try:
        response = client.responses.create(
            model="openai/gpt-oss-120b",
            instructions="""
Ты — AI-редактор Telegram-канала «МАРИНАД».

Твоя задача — помогать готовить материалы для новостного Telegram-канала.

Стиль:
- современный;
- живой;
- краткий;
- понятный;
- без канцелярита;
- допускается умеренный кликбейт, но нельзя искажать факты;
- русский язык;
- без лишних вступлений;
- без даты в начале поста;
- без фразы «Новости без лишнего шума»;
- не придумывай факты, цифры, цитаты или источники;
- если информации недостаточно, прямо укажи это;
- обычно 300–700 символов, если пользователь не попросил другой объём.

Если пользователь прислал исходный материал, сначала пойми его смысл,
а затем переработай его в качественный материал для «МАРИНАДА».

Если пользователь просит просто ответить на вопрос — отвечай непосредственно,
а не обязательно оформляй ответ как новость.
""",
            input=prompt,
        )

        result = response.output_text.strip()

        if not result:
            bot.reply_to(
                message,
                "❌ AI вернул пустой ответ."
            )
            return

        send_long_message(message.chat.id, result)

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ Ошибка Groq:\n{e}"
        )


print("🧂 МАРИНАД | Бот запущен.")
bot.infinity_polling()
