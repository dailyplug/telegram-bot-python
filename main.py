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


# =========================
# START
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
        "/myid — узнать Telegram ID\n"
        "/publish ТЕКСТ — опубликовать пост\n"
        "/ai ТЕКСТ — обработать текст через AI\n"
        "/news — найти свежие новости\n"
        "/news ТЕМА — найти новости по теме"
    )


# =========================
# MY ID
# =========================

@bot.message_handler(commands=["myid"])
def myid(message):
    bot.reply_to(
        message,
        f"Твой Telegram ID: {message.from_user.id}"
    )


# =========================
# PUBLISH
# =========================

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


# =========================
# AI
# =========================

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

Твоя задача — помогать готовить материалы
для новостного Telegram-канала.

Стиль:
- современный;
- живой;
- краткий;
- понятный;
- без канцелярита;
- допускается умеренный кликбейт,
  но нельзя искажать факты;
- русский язык;
- без лишних вступлений;
- без даты в начале поста;
- не используй фразу
  «Новости без лишнего шума»;
- не придумывай факты;
- не придумывай цифры;
- не придумывай цитаты;
- не придумывай источники;
- если информации недостаточно,
  прямо скажи об этом;
- обычно 300–700 символов,
  если пользователь не попросил другой объём.

Если пользователь прислал исходный материал,
сначала пойми его смысл,
а затем переработай его
в качественный материал для «МАРИНАДА».

Если пользователь просит просто ответить
на вопрос — отвечай непосредственно,
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


# =========================
# NEWS + WEB SEARCH
# =========================

@bot.message_handler(commands=["news"])
def news(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return

    topic = message.text[len("/news"):].strip()

    if topic:
        search_topic = topic
    else:
        search_topic = (
            "самые важные свежие новости России и мира "
            "за последние часы"
        )

    bot.reply_to(
        message,
        "🔎 Ищу свежие новости...\n\n"
        f"Запрос: {search_topic}"
    )

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",

            messages=[
                {
                    "role": "system",
                    "content": """
Ты — главный новостной редактор Telegram-канала
«МАРИНАД».

Твоя задача — искать актуальные новости в интернете,
отбирать действительно важные события
и готовить из них материалы для редактора.

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА:

1. Используй веб-поиск.
2. Ищи информацию в свежих источниках.
3. Не выдумывай события.
4. Не выдумывай цифры.
5. Не выдумывай цитаты.
6. Не выдумывай источники.
7. Если информация выглядит сомнительной,
   не используй её как подтверждённый факт.
8. Старайся сверять важные события
   по нескольким источникам.
9. Отдавай приоритет крупным и надёжным источникам.
10. Отдельно отмечай, если информация
    пока предварительная или требует проверки.

Для каждой найденной новости определи:

- что произошло;
- где произошло;
- когда произошло;
- почему это важно;
- насколько информация подтверждена.

СТИЛЬ МАРИНАДА:

- современный;
- живой;
- короткий;
- понятный;
- без канцелярита;
- умеренный кликбейт разрешён;
- нельзя искажать смысл новости;
- никаких пустых вступлений;
- никаких придуманных подробностей.

Формат результата:

🔥 ЗАГОЛОВОК

Короткий текст новости на 300–700 символов.

Источник: название источника

Статус:
✅ подтверждено
или
⚠️ требует дополнительной проверки

Если найдено несколько действительно важных
новостей, выбери максимум 3 лучшие.

Не заполняй ответ незначительными новостями
только ради количества.

Если достойных новостей не найдено,
честно сообщи об этом.
"""
                },
                {
                    "role": "user",
                    "content": (
                        f"Найди свежие новости по запросу: "
                        f"{search_topic}\n\n"
                        "Сначала проведи веб-поиск, "
                        "затем выбери самые важные события "
                        "и подготовь их для редактора МАРИНАДА."
                    )
                }
            ],

            tools=[
                {
                    "type": "browser_search"
                }
            ],

            tool_choice="required",
            temperature=1,
            max_completion_tokens=4096,
        )

        result = response.choices[0].message.content

        if not result:
            bot.reply_to(
                message,
                "❌ Поиск не вернул результат."
            )
            return

        send_long_message(
            message.chat.id,
            "📰 РЕЗУЛЬТАТ ПОИСКА\n\n" + result
        )

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ Ошибка поиска Groq:\n{e}"
        )


# =========================
# START BOT
# =========================

print("🧂 МАРИНАД | Бот запущен.")

bot.infinity_polling()
