import os
from datetime import datetime, timezone, timedelta

import telebot
from openai import OpenAI


# ==========================
# SETTINGS
# ==========================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

CHANNEL = "@marinadnews"
ADMIN_ID = 6056292876


# ==========================
# CLIENTS
# ==========================

bot = telebot.TeleBot(TOKEN)

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)


# ==========================
# HELPERS
# ==========================

def is_admin(message):
    return message.from_user.id == ADMIN_ID


def send_long_message(chat_id, text):
    max_length = 4000

    if len(text) <= max_length:
        bot.send_message(chat_id, text)
        return

    parts = []

    while len(text) > max_length:
        split_at = text.rfind("\n", 0, max_length)

        if split_at == -1:
            split_at = max_length

        parts.append(text[:split_at])
        text = text[split_at:].lstrip()

    if text:
        parts.append(text)

    for part in parts:
        bot.send_message(chat_id, part)


# ==========================
# START
# ==========================

@bot.message_handler(commands=["start"])
def start(message):

    if not is_admin(message):
        bot.reply_to(
            message,
            "⛔ Доступ запрещён."
        )
        return

    bot.reply_to(
        message,
        "🧂 МАРИНАД | NEWS\n\n"
        "Бот редакции запущен.\n\n"
        "Доступные команды:\n"
        "/myid — показать Telegram ID\n"
        "/publish ТЕКСТ — опубликовать пост\n"
        "/ai ТЕКСТ — обработать текст через AI\n"
        "/news — найти свежие новости\n"
        "/news ТЕМА — найти новости по теме"
    )


# ==========================
# MY ID
# ==========================

@bot.message_handler(commands=["myid"])
def myid(message):

    bot.reply_to(
        message,
        f"🆔 Ваш Telegram ID:\n{message.from_user.id}"
    )


# ==========================
# PUBLISH
# ==========================

@bot.message_handler(commands=["publish"])
def publish(message):

    if not is_admin(message):
        bot.reply_to(
            message,
            "⛔ Доступ запрещён."
        )
        return

    text = message.text[len("/publish"):].strip()

    if not text:
        bot.reply_to(
            message,
            "⚠️ После команды нужно указать текст.\n\n"
            "Пример:\n"
            "/publish Тестовый пост"
        )
        return

    try:

        bot.send_message(
            CHANNEL,
            text
        )

        bot.reply_to(
            message,
            "✅ Пост опубликован в @marinadnews."
        )

    except Exception as e:

        bot.reply_to(
            message,
            f"❌ Ошибка публикации:\n{e}"
        )


# ==========================
# AI
# ==========================

@bot.message_handler(commands=["ai"])
def ai(message):

    if not is_admin(message):
        bot.reply_to(
            message,
            "⛔ Доступ запрещён."
        )
        return

    prompt = message.text[len("/ai"):].strip()

    if not prompt:
        bot.reply_to(
            message,
            "⚠️ После /ai нужно написать задачу.\n\n"
            "Пример:\n"
            "/ai Сделай пост о повышении цен на бензин"
        )
        return

    bot.reply_to(
        message,
        "🤖 Обрабатываю..."
    )

    try:

        response = client.responses.create(
            model="openai/gpt-oss-120b",

            instructions="""
Ты — главный редактор Telegram-канала «МАРИНАД».

Название канала: МАРИНАД
Слоган: «Вся соль здесь»
Username: @marinadnews

Твоя задача — помогать создавать сильные Telegram-посты.

СТИЛЬ:

- современный;
- живой;
- быстрый;
- понятный;
- уверенный;
- без канцелярита;
- без лишней воды;
- допускается умеренный кликбейт;
- нельзя искажать факты.

ВАЖНЫЕ ПРАВИЛА:

1. Никогда не придумывай факты.
2. Никогда не придумывай цитаты.
3. Никогда не придумывай цифры.
4. Если информация не подтверждена — прямо укажи это.
5. Не выдавай предположение за факт.
6. Не используй старый бренд «СВЕДЕО».
7. Не используй фразу «Новости без лишнего шума».
8. Не добавляй дату в начало поста.
9. Не перегружай текст эмодзи.
10. Используй @marinadnews при необходимости как подпись.

Типичная структура:

🔥 Сильный заголовок

Основная информация.

Короткий контекст или важная деталь.

Источник: ...

@marinadnews

Пост должен быть компактным и удобным для Telegram.
""",

            input=prompt,
        )

        result = response.output_text.strip()

        if not result:
            bot.reply_to(
                message,
                "❌ AI не вернул результат."
            )
            return

        send_long_message(
            message.chat.id,
            "🤖 РЕЗУЛЬТАТ\n\n" + result
        )

    except Exception as e:

        bot.reply_to(
            message,
            f"❌ Ошибка AI:\n{e}"
        )


# ==========================
# NEWS SEARCH
# ==========================

@bot.message_handler(commands=["news"])
def news(message):

    if not is_admin(message):
        bot.reply_to(
            message,
            "⛔ Доступ запрещён."
        )
        return

    topic = message.text[len("/news"):].strip()

    if topic:
        search_topic = topic
    else:
        search_topic = "главные новости России и мира"

    # Текущее время UTC
    now = datetime.now(timezone.utc)

    # Ищем только последние 12 часов
    cutoff = now - timedelta(hours=12)

    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M UTC")

    cutoff_date = cutoff.strftime("%Y-%m-%d")
    cutoff_time = cutoff.strftime("%H:%M UTC")

    bot.reply_to(
        message,
        "🔎 Ищу действительно свежие новости...\n\n"
        f"Тема: {search_topic}\n"
        f"Период: последние 12 часов\n"
        f"Сейчас: {current_date} {current_time}"
    )

    prompt = f"""
Ты — главный новостной редактор Telegram-канала «МАРИНАД».

ТЕКУЩЕЕ ВРЕМЯ:

Дата:
{current_date}

Время:
{current_time}

Часовой пояс:
UTC

ПОИСК:

Тема:
{search_topic}

Нужно найти самые важные новости по этой теме.

КРИТИЧЕСКИ ВАЖНО:

Используй браузерный поиск.

Нам нужны ТОЛЬКО действительно свежие новости.

РАЗРЕШЁННЫЙ ПЕРИОД:

с {cutoff_date} {cutoff_time} UTC
до {current_date} {current_time} UTC.

То есть только последние 12 часов.

СТРОГИЙ ФИЛЬТР ДАТЫ:

1. Проверяй дату публикации каждого материала.

2. Проверяй дату самого события.

3. Материалы 2024 года НЕ ИСПОЛЬЗОВАТЬ.

4. Материалы 2025 года НЕ ИСПОЛЬЗОВАТЬ.

5. Любые материалы старше разрешённого 12-часового окна НЕ ИСПОЛЬЗОВАТЬ.

6. Если статья опубликована сегодня, но рассказывает о событии недельной, месячной или годовой давности — НЕ ИСПОЛЬЗОВАТЬ.

7. Если сегодня опубликовано обновление старой новости — НЕ ИСПОЛЬЗОВАТЬ, если само событие произошло раньше разрешённого периода.

8. Не путай дату обновления страницы с датой первоначальной публикации.

9. Если точную дату публикации установить невозможно — НЕ ИСПОЛЬЗОВАТЬ.

10. Не используй поисковые сниппеты как единственное подтверждение даты.

11. Не придумывай дату публикации.

12. Если свежих новостей недостаточно — верни меньше новостей.

13. Лучше вернуть одну действительно свежую новость, чем три старые.

ОСОБЕННО ВАЖНО:

Поисковая выдача может показывать старые статьи выше новых.

Это нельзя считать признаком свежести.

Каждую новость необходимо проверять отдельно.

ПРИОРИТЕТ ИСТОЧНИКОВ:

1. Reuters
2. Associated Press
3. BBC
4. ТАСС
5. РИА Новости
6. Интерфакс
7. официальные государственные ведомства
8. официальные заявления компаний и организаций
9. другие крупные надёжные СМИ

Для важных событий желательно наличие минимум двух независимых источников.

НЕ ИСПОЛЬЗУЙ:

- старые статьи;
- статьи 2024 года;
- статьи 2025 года;
- старые новости, которые снова стали популярными;
- новости из Telegram как единственный источник;
- неподтверждённые слухи;
- выдуманные цитаты;
- выдуманные цифры;
- выдуманные источники;
- предположения, выданные за факты.

ЗАДАЧА:

Найди максимум 3 самые важные свежие новости.

Для каждой новости обязательно укажи:

🔥 Заголовок

Короткое описание того, что произошло.

Дата публикации: ДД.ММ.ГГГГ

Время публикации: если доступно

Дата события: если отличается

Источник: название СМИ или организации

Подтверждение:

✅ подтверждено

или

⚠️ требует дополнительной проверки

ВАЖНО:

Если новость подтверждается только одним источником,
не называй её полностью подтверждённой.

Если событие подтверждено официальным ведомством,
можно указать это отдельно.

ПЕРЕД ФИНАЛЬНЫМ ОТВЕТОМ:

Ещё раз проверь дату каждой выбранной новости.

Если новость старше 12 часов — УДАЛИ ЕЁ.

Если новость относится к 2024 или 2025 году — УДАЛИ ЕЁ.

Если статья сегодня рассказывает о старом событии — УДАЛИ ЕЁ.

Если подходящих новостей меньше трёх — НЕ ЗАПОЛНЯЙ список старыми новостями.

ФОРМАТ:

🔥 Новость 1

[текст]

Дата публикации: ...
Время публикации: ...
Дата события: ...

Источник: ...

Подтверждение: ...

---

🔥 Новость 2

[текст]

Дата публикации: ...
Время публикации: ...
Дата события: ...

Источник: ...

Подтверждение: ...

---

🔥 Новость 3

[текст]

Дата публикации: ...
Время публикации: ...
Дата события: ...

Источник: ...

Подтверждение: ...

В конце:

🕐 Проверено: {current_date} {current_time}
📌 Период поиска: последние 12 часов

Если достойных свежих новостей нет, напиши:

«Свежих подтверждённых новостей по заданной теме
за последние 12 часов не найдено.»

Не объясняй процесс своей работы.

Сразу выдай результат поиска.
"""

    try:

        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",

            messages=[
                {
                    "role": "system",
                    "content": prompt
                },
                {
                    "role": "user",
                    "content": (
                        f"Найди самые важные свежие новости "
                        f"по теме: {search_topic}.\n\n"
                        f"Используй браузерный поиск.\n"
                        f"Строго соблюдай ограничение "
                        f"последних 12 часов.\n"
                        f"Не используй старые материалы."
                    )
                }
            ],

            tools=[
                {
                    "type": "browser_search"
                }
            ],

            tool_choice="required",

            temperature=0.2,

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


# ==========================
# START BOT
# ==========================

print("🚰 МАРИНАД | Бот запущен.")

bot.infinity_polling()
