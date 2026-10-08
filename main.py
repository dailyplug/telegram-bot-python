import os
import telebot

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL = "@marinadnews"

# Только этот Telegram ID может управлять ботом
ADMIN_ID = 6056292876

bot = telebot.TeleBot(TOKEN)


def is_admin(message):
    return message.from_user.id == ADMIN_ID


@bot.message_handler(commands=["start"])
def start(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    bot.reply_to(
        message,
        "🧂 МАРИНАД на связи.\n\n"
        "Бот работает.\n\n"
        "/myid — показать твой Telegram ID\n"
        "/publish ТЕКСТ — опубликовать пост в канале."
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

    # Получаем текст после /publish
    text = message.text.partition(" ")[2].strip()

    if not text:
        bot.reply_to(
            message,
            "⚠️ После команды /publish нужно написать текст поста.\n\n"
            "Пример:\n"
            "/publish 🧂 МАРИНАД тестовый пост"
        )
        return

    try:
        bot.send_message(
            CHANNEL,
            text
        )

        bot.reply_to(
            message,
            f"✅ Пост опубликован в {CHANNEL}."
        )

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ Ошибка публикации:\n{e}"
        )


@bot.message_handler(func=lambda message: True)
def unknown(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    bot.reply_to(
        message,
        "Неизвестная команда.\n\n"
        "Используй:\n"
        "/start\n"
        "/myid\n"
        "/publish ТЕКСТ"
    )


print("МАРИНАД | NEWS bot запущен.")

bot.infinity_polling()
