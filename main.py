import os
import telebot

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL = "@marinadnews"

bot = telebot.TeleBot(TOKEN)


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "🧂 МАРИНАД на связи.\n\n"
        "Бот работает.\n"
        "Используй /myid, чтобы узнать свой Telegram ID.\n"
        "Используй /publish ТЕКСТ — чтобы опубликовать пост в канале."
    )


@bot.message_handler(commands=["myid"])
def myid(message):
    bot.reply_to(
        message,
        f"Твой Telegram ID: `{message.from_user.id}`",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["publish"])
def publish(message):
    # Проверяем, что команду отправил администратор канала
    try:
        member = bot.get_chat_member(CHANNEL, message.from_user.id)

        if member.status not in ["administrator", "creator"]:
            bot.reply_to(message, "⛔ У тебя нет прав для публикации.")
            return

    except Exception:
        bot.reply_to(
            message,
            "❌ Не удалось проверить права администратора канала."
        )
        return

    text = message.text.replace("/publish", "", 1).strip()

    if not text:
        bot.reply_to(
            message,
            "Напиши текст после команды.\n\n"
            "Пример:\n"
            "/publish 🚨 Важная новость"
        )
        return

    try:
        bot.send_message(CHANNEL, text)
        bot.reply_to(message, "✅ Пост опубликован в @marinadnews.")

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ Ошибка публикации:\n{e}"
        )


print("МАРИНАД | NEWS запущен")

bot.infinity_polling(skip_pending=True)
