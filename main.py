import os
from datetime import datetime, timezone, timedelta

import telebot
from openai import OpenAI


# ============================================================
# НАСТРОЙКИ
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

CHANNEL = "@marinadnews"
ADMIN_ID = 6056292876

# Московское время
MSK = timezone(timedelta(hours=3))

# Сколько часов реально разрешаем для финального отбора
FINAL_NEWS_WINDOW_HOURS = 24

# Сколько часов просим модель просматривать при поиске
SEARCH_WINDOW_HOURS = 48

# Максимальный разумный запас на ошибку часов у источника/модели
FUTURE_TOLERANCE_MINUTES = 15


# ============================================================
# TELEGRAM
# ============================================================

bot = telebot.TeleBot(TOKEN)


# ============================================================
# GROQ
# ============================================================

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def is_admin(message):
    return message.from_user.id == ADMIN_ID


def now_msk():
    return datetime.now(MSK)


def format_msk(dt):
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def send_long_message(chat_id, text):
    """
    Telegram ограничивает длину одного сообщения.
    Разбиваем длинный ответ на части.
    """
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


def parse_iso_datetime(value):
    """
    Безопасно превращает ISO дату модели в datetime.
    """
    if not value:
        return None

    value = value.strip()

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(MSK)

    except Exception:
        return None


def validate_news_date(publication_dt, event_dt, current_time):
    """
    ЖЁСТКАЯ ПРОГРАММНАЯ ПРОВЕРКА.

    Новости:
    - не могут быть из будущего;
    - публикация должна быть не старше 24 часов;
    - событие не должно быть существенно старше 48 часов.
    """

    if publication_dt is None:
        return False, "нет корректной даты публикации"

    future_limit = current_time + timedelta(
        minutes=FUTURE_TOLERANCE_MINUTES
    )

    # Защита от будущих дат
    if publication_dt > future_limit:
        return False, "дата публикации в будущем"

    if event_dt and event_dt > future_limit:
        return False, "дата события в будущем"

    publication_age = current_time - publication_dt

    # Публикация старше 24 часов
    if publication_age > timedelta(hours=FINAL_NEWS_WINDOW_HOURS):
        return False, "публикация старше 24 часов"

    # Событие не должно быть слишком старым
    if event_dt:
        event_age = current_time - event_dt

        if event_age > timedelta(hours=SEARCH_WINDOW_HOURS):
            return False, "само событие старше 48 часов"

    return True, "OK"


# ============================================================
# ПАРСИНГ НОВОСТЕЙ
# ============================================================

def parse_news_candidates(raw_text, current_time):
    """
    Модель обязана отдавать новости в машинно-читаемом формате:

    NEWS|Заголовок|publication_iso|event_iso|source|url|freshness|importance|interest|reliability

    После получения мы НЕ доверяем модели на слово,
    а программно проверяем даты.
    """

    candidates = []

    if not raw_text:
        return candidates

    for line in raw_text.splitlines():

        line = line.strip()

        if not line.startswith("NEWS|"):
            continue

        parts = line.split("|")

        if len(parts) < 10:
            continue

        title = parts[1].strip()
        publication_raw = parts[2].strip()
        event_raw = parts[3].strip()
        source = parts[4].strip()
        url = parts[5].strip()

        try:
            freshness = int(parts[6].strip())
            importance = int(parts[7].strip())
            interest = int(parts[8].strip())
            reliability = int(parts[9].strip())

        except Exception:
            continue

        publication_dt = parse_iso_datetime(publication_raw)
        event_dt = parse_iso_datetime(event_raw)

        valid, reason = validate_news_date(
            publication_dt,
            event_dt,
            current_time,
        )

        if not valid:
            continue

        # Нормализуем оценки
        freshness = max(0, min(10, freshness))
        importance = max(0, min(10, importance))
        interest = max(0, min(10, interest))
        reliability = max(0, min(10, reliability))

        # Чем свежее новость, тем выше бонус.
        age_hours = (
            current_time - publication_dt
        ).total_seconds() / 3600

        freshness_bonus = max(
            0,
            10 - int(age_hours / 2)
        )

        # Итоговый редакционный балл.
        score = (
            freshness * 0.30
            + importance * 0.25
            + interest * 0.20
            + reliability * 0.20
            + freshness_bonus * 0.05
        )

        candidates.append({
            "title": title,
            "publication": publication_dt,
            "event": event_dt,
            "source": source,
            "url": url,
            "freshness": freshness,
            "importance": importance,
            "interest": interest,
            "reliability": reliability,
            "score": round(score, 2),
        })

    return candidates


def remove_duplicates(candidates):
    """
    Простая защита от повторов в одной выдаче.
    """

    result = []
    seen_titles = set()

    for item in candidates:

        normalized = (
            item["title"]
            .lower()
            .replace("«", "")
            .replace("»", "")
            .replace('"', "")
        )

        # Берём первые 100 символов,
        # чтобы ловить практически одинаковые заголовки.
        key = normalized[:100]

        if key in seen_titles:
            continue

        seen_titles.add(key)
        result.append(item)

    return result


def sort_candidates(candidates):
    """
    Сначала самые сильные и свежие новости.
    """

    return sorted(
        candidates,
        key=lambda x: (
            x["score"],
            x["publication"],
        ),
        reverse=True,
    )


# ============================================================
# ПОИСК НОВОСТЕЙ
# ============================================================

def search_news(topic=None):
    current_time = now_msk()

    if topic:
        directions = f"""
ОСНОВНАЯ ТЕМА ПОИСКА:
{topic}

Ищи новости именно по этой теме, но выбирай только реально значимые
и актуальные события.
"""
    else:
        directions = """
ОБЯЗАТЕЛЬНО ПРОВЕРЬ НЕСКОЛЬКО НАПРАВЛЕНИЙ:

1. Россия
2. Мир
3. Политика
4. Конфликты / военная и международная безопасность
5. Крупные происшествия
6. Экономика / крупный бизнес
7. Технологии / наука / общество

Не обязательно брать новости из каждой категории.
Нужно найти самые сильные события независимо от категории.
"""

    prompt = f"""
Ты работаешь как редактор новостного Telegram-канала «МАРИНАД».

Текущая дата и время:
{current_time.isoformat()}

Московское время:
{current_time.strftime("%d.%m.%Y %H:%M")} МСК

ТВОЯ ЗАДАЧА:
Найти самые сильные новости для публикации в Telegram.

ПЕРИОД ПОИСКА:
Последние {SEARCH_WINDOW_HOURS} часов.

КРИТИЧЕСКИ ВАЖНО:

Мы НЕ хотим старые новости.

Дата публикации каждой новости ОБЯЗАТЕЛЬНО должна быть указана
в ISO-формате с часовым поясом.

Дата события тоже должна быть указана, если она известна.

Не выдавай новость, если:
- она опубликована больше 24 часов назад;
- она относится к событию старше 48 часов;
- дата неизвестна;
- дата выглядит сомнительно;
- дата находится в будущем;
- это просто старый материал, который снова всплыл в поиске.

Если старое событие получило НОВОЕ РАЗВИТИЕ сегодня,
можно использовать именно новое развитие.
В таком случае publication_iso должна соответствовать новой публикации.

ПРИОРИТЕТ:
1. Новости последних нескольких часов.
2. Крупные события сегодняшнего дня.
3. Новые развития важных событий.
4. Международные и российские события с высоким общественным интересом.
5. Происшествия с большим масштабом.
6. Экономика и технологии, если событие действительно значимое.

ИСТОЧНИКИ:
Предпочитай:
- Reuters
- Associated Press
- BBC
- AFP
- TASS
- РИА Новости
- Интерфакс
- официальные государственные источники
- официальные заявления организаций
- крупные международные СМИ

Не используй сомнительные сайты как единственное подтверждение
для важных новостей.

НЕ ПРИДУМЫВАЙ:
- даты;
- цифры;
- погибших;
- заявления;
- источники;
- ссылки.

Если информацию невозможно надёжно подтвердить,
лучше не включай её.

{directions}

НАЙДИ НЕ МЕНЕЕ 8 КАНДИДАТОВ, ЕСЛИ В ПОИСКЕ ЕСТЬ ДОСТАТОЧНО
СВЕЖИХ НОВОСТЕЙ.

Для каждой новости оцени:

freshness = свежесть от 0 до 10
importance = важность от 0 до 10
interest = интерес для аудитории от 0 до 10
reliability = надёжность от 0 до 10

ОСОБО:
Не ставь высокий reliability, если новость подтверждена только
одним сомнительным источником.

ФОРМАТ ОТВЕТА:

Только строки следующего формата.
Без нумерации.
Без Markdown.
Без пояснений.
Без дополнительных строк.

NEWS|ЗАГОЛОВОК|publication_iso|event_iso|SOURCE|URL|freshness|importance|interest|reliability

Пример формата:

NEWS|В Москве произошло крупное событие|2026-10-08T12:30:00+03:00|2026-10-08T11:50:00+03:00|Reuters|https://example.com|9|8|9|10

ВАЖНО:
Сейчас {current_time.strftime("%d.%m.%Y %H:%M")} МСК.
Не путай дату публикации статьи с датой события.
"""

    try:
        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=prompt,
            tools=[
                {"type": "browser_search"}
            ],
            tool_choice="required",
            reasoning={
                "effort": "low"
            },
        )

        return response.output_text.strip()

    except Exception as e:
        return f"ERROR|{str(e)}"


# ============================================================
# ВТОРОЙ ПОИСК
# ============================================================

def search_news_second_pass(topic=None):
    """
    Второй поиск нужен, если первый дал мало валидных новостей.
    """

    current_time = now_msk()

    if topic:
        search_direction = f"""
ТЕМА:
{topic}
"""
    else:
        search_direction = """
Сделай дополнительный поиск по самым актуальным событиям:

Россия,
мир,
конфликты,
политика,
происшествия,
экономика,
технологии,
общество.
"""

    prompt = f"""
Ты — второй новостной редактор канала «МАРИНАД».

Сейчас:
{current_time.strftime("%d.%m.%Y %H:%M")} МСК.

Нужны САМЫЕ СВЕЖИЕ события за последние 24 часа.

{search_direction}

Ищи дополнительно, а не повторяй очевидные старые материалы.

Особенно ищи:
- события сегодняшнего дня;
- новости последних часов;
- новые официальные заявления;
- новые последствия событий;
- крупные происшествия;
- новые решения властей;
- новые международные события;
- резонансные новости.

Не используй публикации старше 24 часов.

Старое событие допускается только если сегодня произошло
новое существенное развитие.

Нужны минимум 5 кандидатов.

Формат каждой строки:

NEWS|ЗАГОЛОВОК|publication_iso|event_iso|SOURCE|URL|freshness|importance|interest|reliability

Никаких дополнительных пояснений.

Не придумывай даты, источники и факты.
"""

    try:
        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=prompt,
            tools=[
                {"type": "browser_search"}
            ],
            tool_choice="required",
            reasoning={
                "effort": "low"
            },
        )

        return response.output_text.strip()

    except Exception as e:
        return f"ERROR|{str(e)}"


# ============================================================
# /START
# ============================================================

@bot.message_handler(commands=["start"])
def start_command(message):

    if not is_admin(message):
        bot.reply_to(
            message,
            "⛔ Доступ запрещён."
        )
        return

    bot.reply_to(
        message,
        """
🧂 МАРИНАД | NEWS BOT

Команды:

/myid — узнать Telegram ID

/publish ТЕКСТ
Опубликовать текст в @marinadnews

/ai ТЕКСТ
Сгенерировать текст через ИИ

/news
Найти свежие новости

/news ТЕМА
Найти свежие новости по конкретной теме
"""
    )


# ============================================================
# /MYID
# ============================================================

@bot.message_handler(commands=["myid"])
def myid_command(message):

    bot.reply_to(
        message,
        f"🆔 Ваш Telegram ID:\n{message.from_user.id}"
    )


# ============================================================
# /PUBLISH
# ============================================================

@bot.message_handler(commands=["publish"])
def publish_command(message):

    if not is_admin(message):
        bot.reply_to(
            message,
            "⛔ Доступ запрещён."
        )
        return

    text = message.text.replace(
        "/publish",
        "",
        1
    ).strip()

    if not text:
        bot.reply_to(
            message,
            "Использование:\n/publish ТЕКСТ"
        )
        return

    try:

        bot.send_message(
            CHANNEL,
            text
        )

        bot.reply_to(
            message,
            "✅ Пост опубликован в @marinadnews"
        )

    except Exception as e:

        bot.reply_to(
            message,
            f"❌ Ошибка публикации:\n{e}"
        )


# ============================================================
# /AI
# ============================================================

@bot.message_handler(commands=["ai"])
def ai_command(message):

    if not is_admin(message):
        bot.reply_to(
            message,
            "⛔ Доступ запрещён."
        )
        return

    prompt = message.text.replace(
        "/ai",
        "",
        1
    ).strip()

    if not prompt:
        bot.reply_to(
            message,
            "Использование:\n/ai ТЕКСТ"
        )
        return

    status = bot.reply_to(
        message,
        "🧠 Готовлю материал для МАРИНАД..."
    )

    try:

        response = client.responses.create(
            model="openai/gpt-oss-120b",
            instructions="""
Ты — редактор Telegram-канала МАРИНАД.

Стиль:
- современный;
- быстрый;
- живой;
- понятный;
- без канцелярита;
- без лишней воды;
- допускается цепкий заголовок;
- факты нельзя искажать;
- нельзя придумывать цитаты;
- нельзя придумывать цифры;
- нельзя придумывать источники.

Формат:
сильный заголовок
+
короткий содержательный текст.

Обычно 300–700 символов.

Не добавляй:
«СВЕДЕО»
«Новости без лишнего шума»
дату отдельной строкой.

Слоган МАРИНАД:
«Вся соль здесь».

Не используй его в конце каждого поста автоматически.
""",
            input=prompt,
        )

        result = response.output_text.strip()

        bot.delete_message(
            message.chat.id,
            status.message_id
        )

        send_long_message(
            message.chat.id,
            result
        )

    except Exception as e:

        bot.edit_message_text(
            f"❌ Ошибка ИИ:\n{e}",
            message.chat.id,
            status.message_id
        )


# ============================================================
# /NEWS
# ============================================================

@bot.message_handler(commands=["news"])
def news_command(message):

    if not is_admin(message):
        bot.reply_to(
            message,
            "⛔ Доступ запрещён."
        )
        return

    topic = message.text.replace(
        "/news",
        "",
        1
    ).strip()

    current_time = now_msk()

    status = bot.reply_to(
        message,
        (
            "🔎 Ищу свежие новости для МАРИНАД...\n\n"
            f"🕐 Сейчас: {format_msk(current_time)} МСК\n"
            f"🔍 Поиск: последние {SEARCH_WINDOW_HOURS} часов\n"
            f"✅ Финальный фильтр: последние {FINAL_NEWS_WINDOW_HOURS} часов"
        )
    )

    # --------------------------------------------------------
    # ПЕРВЫЙ ПОИСК
    # --------------------------------------------------------

    raw_first = search_news(topic)

    if raw_first.startswith("ERROR|"):
        bot.edit_message_text(
            "❌ Ошибка поиска:\n" + raw_first[6:],
            message.chat.id,
            status.message_id
        )
        return

    candidates = parse_news_candidates(
        raw_first,
        current_time
    )

    # --------------------------------------------------------
    # ВТОРОЙ ПОИСК
    # Если валидных новостей мало
    # --------------------------------------------------------

    if len(candidates) < 3:

        raw_second = search_news_second_pass(topic)

        if not raw_second.startswith("ERROR|"):

            second_candidates = parse_news_candidates(
                raw_second,
                current_time
            )

            candidates.extend(
                second_candidates
            )

    # --------------------------------------------------------
    # УДАЛЯЕМ ПОВТОРЫ
    # --------------------------------------------------------

    candidates = remove_duplicates(
        candidates
    )

    # --------------------------------------------------------
    # СОРТИРОВКА
    # --------------------------------------------------------

    candidates = sort_candidates(
        candidates
    )

    # Берём максимум 3
    top_news = candidates[:3]

    # --------------------------------------------------------
    # ЕСЛИ НИЧЕГО НЕ НАЙДЕНО
    # --------------------------------------------------------

    if not top_news:

        bot.edit_message_text(
            (
                "⚠️ Свежих подтверждённых новостей "
                "за последние 24 часа не найдено.\n\n"
                "Старые публикации бот специально отфильтровал."
            ),
            message.chat.id,
            status.message_id
        )

        return

    # --------------------------------------------------------
    # ФОРМИРУЕМ ОТВЕТ
    # --------------------------------------------------------

    output = []

    output.append(
        "🧂 МАРИНАД | РЕДАКЦИОННЫЙ ОТБОР"
    )

    output.append("")

    output.append(
        f"🕐 Проверено: {format_msk(current_time)} МСК"
    )

    output.append(
        f"📌 Поиск: последние {SEARCH_WINDOW_HOURS} часов"
    )

    output.append(
        f"🔒 Финальный фильтр: последние {FINAL_NEWS_WINDOW_HOURS} часов"
    )

    output.append("")

    for index, item in enumerate(top_news, start=1):

        publication_age = (
            current_time - item["publication"]
        ).total_seconds() / 3600

        if publication_age < 1:
            age_text = (
                f"{max(1, int(publication_age * 60))} мин назад"
            )
        else:
            age_text = (
                f"{publication_age:.1f} ч назад"
            )

        output.append(
            f"🔥 TOP-{index}"
        )

        output.append(
            f"**{item['title']}**"
        )

        output.append(
            f"Источник: {item['source']}"
        )

        output.append(
            f"Публикация: {format_msk(item['publication'])} МСК"
        )

        if item["event"]:
            output.append(
                f"Событие: {format_msk(item['event'])} МСК"
            )

        output.append(
            f"Свежесть: {item['freshness']}/10"
        )

        output.append(
            f"Важность: {item['importance']}/10"
        )

        output.append(
            f"Интерес: {item['interest']}/10"
        )

        output.append(
            f"Надёжность: {item['reliability']}/10"
        )

        output.append(
            f"Редакционный балл: {item['score']}/10"
        )

        output.append(
            f"⏱ {age_text}"
        )

        output.append(
            f"🔗 {item['url']}"
        )

        output.append("")

    output.append(
        "✅ Старые публикации и некорректные даты отфильтрованы программно."
    )

    final_text = "\n".join(output)

    bot.edit_message_text(
        final_text,
        message.chat.id,
        status.message_id,
        disable_web_page_preview=True
    )


# ============================================================
# ЗАПУСК
# ============================================================

print("🧂 МАРИНАД | Telegram bot started")

bot.infinity_polling(
    skip_pending=True
)
