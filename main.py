import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import telebot
from openai import OpenAI


# ============================================================
# НАСТРОЙКИ
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

CHANNEL = "@marinadnews"
ADMIN_ID = 6056292876

MSK = timezone(timedelta(hours=3))

# Ищем шире
SEARCH_WINDOW_HOURS = 48

# Но в финальный TOP допускаем только свежие новости
FINAL_NEWS_WINDOW_HOURS = 24

# Допустимая погрешность времени
FUTURE_TOLERANCE_MINUTES = 15

# Минимальное количество кандидатов
MIN_CANDIDATES = 3

# Сколько новостей дополнительно проверяем
MAX_VERIFICATION_CANDIDATES = 5


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
# РАЗРЕШЁННЫЕ ИСТОЧНИКИ
# ============================================================

TRUSTED_DOMAINS = {
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "afp.com",
    "tass.ru",
    "ria.ru",
    "interfax.ru",
    "rg.ru",
    "kremlin.ru",
    "government.ru",
    "mid.ru",
    "mil.ru",
    "mchs.gov.ru",
    "mos.ru",
    "lenta.ru",
    "rbc.ru",
    "kommersant.ru",
    "vedomosti.ru",
    "t.me",
}


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
    max_length = 4000

    if len(text) <= max_length:
        bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
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
        bot.send_message(
            chat_id,
            part,
            parse_mode="HTML",
            disable_web_page_preview=True
        )


def parse_iso_datetime(value):
    if not value:
        return None

    value = value.strip()

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(MSK)

    except Exception:
        return None


# ============================================================
# ПРОВЕРКА ДАТЫ
# ============================================================

def validate_news_date(
    publication_dt,
    event_dt,
    current_time
):

    if publication_dt is None:
        return False, "нет даты публикации"

    future_limit = current_time + timedelta(
        minutes=FUTURE_TOLERANCE_MINUTES
    )

    if publication_dt > future_limit:
        return False, "дата публикации в будущем"

    if event_dt and event_dt > future_limit:
        return False, "дата события в будущем"

    publication_age = (
        current_time - publication_dt
    )

    if publication_age > timedelta(
        hours=FINAL_NEWS_WINDOW_HOURS
    ):
        return False, "публикация старше 24 часов"

    if event_dt:

        event_age = (
            current_time - event_dt
        )

        if event_age > timedelta(
            hours=SEARCH_WINDOW_HOURS
        ):
            return False, "событие старше 48 часов"

    return True, "OK"


# ============================================================
# ПРОВЕРКА РУССКОГО ЗАГОЛОВКА
# ============================================================

def is_russian_title(title):
    """
    Проверяем, что заголовок действительно русский.
    """

    if not title:
        return False

    cyrillic = len(
        re.findall(
            r"[А-Яа-яЁё]",
            title
        )
    )

    latin = len(
        re.findall(
            r"[A-Za-z]",
            title
        )
    )

    total_letters = cyrillic + latin

    if total_letters == 0:
        return False

    # Если кириллицы меньше 50%,
    # считаем заголовок не русским.
    if cyrillic / total_letters < 0.5:
        return False

    return True


# ============================================================
# ПРОВЕРКА URL
# ============================================================

def get_domain(url):
    try:
        parsed = urlparse(url)

        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return ""


def is_trusted_domain(url):
    domain = get_domain(url)

    if not domain:
        return False

    for trusted in TRUSTED_DOMAINS:

        if domain == trusted:
            return True

        if domain.endswith("." + trusted):
            return True

    return False


def check_url(url):
    """
    Проверяем:
    1. корректность URL;
    2. HTTPS;
    3. домен;
    4. доступность страницы.

    Некоторые СМИ могут отвечать 403/405 на автоматические запросы.
    Это не обязательно означает, что ссылка фальшивая.
    """

    if not url:
        return False, "пустой URL"

    if not url.startswith("https://"):
        return False, "не HTTPS"

    parsed = urlparse(url)

    if not parsed.netloc:
        return False, "некорректный URL"

    domain = get_domain(url)

    if not is_trusted_domain(url):
        return False, f"неразрешённый источник: {domain}"

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(compatible; MARINAD-NewsBot/1.0)"
                )
            },
            method="HEAD"
        )

        with urllib.request.urlopen(
            request,
            timeout=8
        ) as response:

            status = response.status

            if 200 <= status < 400:
                return True, "OK"

            if status in (401, 403, 405, 429):
                return True, f"сервер доступен, HTTP {status}"

            return False, f"HTTP {status}"

    except urllib.error.HTTPError as e:

        if e.code in (401, 403, 405, 429):
            return True, f"сервер доступен, HTTP {e.code}"

        return False, f"HTTP {e.code}"

    except Exception as e:

        # Сетевой таймаут не доказывает,
        # что URL фальшивый.
        # Но для неизвестного источника мы его отбрасываем.
        return False, "страница недоступна"


# ============================================================
# ПАРСИНГ НОВОСТЕЙ
# ============================================================

def parse_news_candidates(
    raw_text,
    current_time
):

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

        publication_dt = parse_iso_datetime(
            publication_raw
        )

        event_dt = parse_iso_datetime(
            event_raw
        )

        # ----------------------------------------------------
        # ПРОВЕРКА ДАТ
        # ----------------------------------------------------

        valid, reason = validate_news_date(
            publication_dt,
            event_dt,
            current_time
        )

        if not valid:
            continue

        # ----------------------------------------------------
        # ПРОВЕРКА ЗАГОЛОВКА
        # ----------------------------------------------------

        if not is_russian_title(title):
            continue

        # ----------------------------------------------------
        # ПРОВЕРКА URL
        # ----------------------------------------------------

        url_valid, url_reason = check_url(url)

        if not url_valid:
            continue

        freshness = max(
            0,
            min(10, freshness)
        )

        importance = max(
            0,
            min(10, importance)
        )

        interest = max(
            0,
            min(10, interest)
        )

        reliability = max(
            0,
            min(10, reliability)
        )

        # ----------------------------------------------------
        # РЕАЛЬНАЯ СВЕЖЕСТЬ
        # ----------------------------------------------------

        age_hours = (
            current_time - publication_dt
        ).total_seconds() / 3600

        # Бонус за реальную свежесть
        if age_hours <= 2:
            real_freshness_bonus = 10

        elif age_hours <= 4:
            real_freshness_bonus = 9

        elif age_hours <= 8:
            real_freshness_bonus = 8

        elif age_hours <= 12:
            real_freshness_bonus = 7

        elif age_hours <= 18:
            real_freshness_bonus = 6

        else:
            real_freshness_bonus = 4

        # ----------------------------------------------------
        # БАЗОВЫЙ РЕДАКЦИОННЫЙ БАЛЛ
        # ----------------------------------------------------

        score = (
            freshness * 0.20
            + importance * 0.25
            + interest * 0.20
            + reliability * 0.20
            + real_freshness_bonus * 0.15
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

            "verified": False,

            "verification_score": 0,

            "secondary_source": "",

        })

    return candidates


# ============================================================
# УДАЛЕНИЕ ДУБЛИКАТОВ
# ============================================================

def normalize_title(title):

    normalized = title.lower()

    normalized = re.sub(
        r"[^а-яa-z0-9 ]",
        " ",
        normalized
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized
    )

    return normalized.strip()


def remove_duplicates(candidates):

    result = []
    seen = set()

    for item in candidates:

        key = normalize_title(
            item["title"]
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


# ============================================================
# СОРТИРОВКА
# ============================================================

def sort_candidates(candidates):

    return sorted(
        candidates,
        key=lambda x: (
            x["score"],
            x["publication"]
        ),
        reverse=True
    )


# ============================================================
# ВТОРИЧНАЯ ПРОВЕРКА НОВОСТИ
# ============================================================

def verify_candidate(candidate):

    prompt = f"""
Ты — фактчекер Telegram-канала МАРИНАД.

Нужно проверить конкретную новость.

ЗАГОЛОВОК:
{candidate["title"]}

ИСТОЧНИК:
{candidate["source"]}

URL:
{candidate["url"]}

Найди эту публикацию через browser search.

ВАЖНО:
1. Проверь, существует ли реально указанная публикация.
2. Проверь, соответствует ли содержание публикации заголовку.
3. Проверь дату публикации.
4. Найди независимое подтверждение этой же новости,
   желательно у другого крупного СМИ или официального источника.
5. Не считай перепечатки одного агентства независимыми источниками.

ОТВЕТ ДОЛЖЕН БЫТЬ СТРОГО В ФОРМАТЕ:

VERIFY|YES|secondary_source|secondary_url|reason

или

VERIFY|NO|NONE|NONE|reason

Никаких дополнительных строк.

Не придумывай URL.
Если независимого подтверждения нет,
это не обязательно означает, что новость ложная,
но укажи VERIFY|NO.
"""

    try:

        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=prompt,
            tools=[
                {
                    "type": "browser_search"
                }
            ],
            tool_choice="required",
            reasoning={
                "effort": "low"
            },
        )

        result = response.output_text.strip()

        for line in result.splitlines():

            line = line.strip()

            if not line.startswith("VERIFY|"):
                continue

            parts = line.split("|")

            if len(parts) < 5:
                continue

            decision = parts[1].strip().upper()

            secondary_source = parts[2].strip()

            secondary_url = parts[3].strip()

            reason = "|".join(
                parts[4:]
            ).strip()

            if decision == "YES":

                return {
                    "verified": True,
                    "secondary_source": secondary_source,
                    "secondary_url": secondary_url,
                    "reason": reason,
                }

            return {
                "verified": False,
                "secondary_source": "",
                "secondary_url": "",
                "reason": reason,
            }

    except Exception as e:

        return {
            "verified": False,
            "secondary_source": "",
            "secondary_url": "",
            "reason": str(e),
        }

    return {
        "verified": False,
        "secondary_source": "",
        "secondary_url": "",
        "reason": "не удалось получить проверку",
    }


# ============================================================
# ДОПОЛНИТЕЛЬНЫЙ ПОИСК
# ============================================================

def search_news_second_pass(topic=None):

    current_time = now_msk()

    if topic:

        search_direction = f"""
ТЕМА:
{topic}
"""

    else:

        search_direction = """
Ищи дополнительно по направлениям:

Россия,
мир,
политика,
конфликты,
происшествия,
экономика,
технологии,
общество.
"""

    prompt = f"""
Ты — второй новостной редактор канала МАРИНАД.

Сейчас:
{current_time.strftime("%d.%m.%Y %H:%M")} МСК.

{search_direction}

Найди самые важные события последних 24 часов.

ОСОБО ИЩИ:
- новости сегодняшнего дня;
- события последних часов;
- новые официальные заявления;
- новые последствия крупных событий;
- крупные происшествия;
- международные события;
- экономические события;
- технологии и науку.

Не используй публикации старше 24 часов,
если это не новое развитие старого события.

НУЖНЫ ТОЛЬКО РУССКИЕ ЗАГОЛОВКИ.

Никаких английских headline.

Нужны минимум 5 кандидатов.

Формат:

NEWS|РУССКИЙ ЗАГОЛОВОК|publication_iso|event_iso|SOURCE|URL|freshness|importance|interest|reliability

Все даты должны быть реальными.

Не придумывай URL.

Никаких дополнительных пояснений.
"""

    try:

        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=prompt,
            tools=[
                {
                    "type": "browser_search"
                }
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
# ОСНОВНОЙ ПОИСК
# ============================================================

def search_news(topic=None):

    current_time = now_msk()

    if topic:

        directions = f"""
ОСНОВНАЯ ТЕМА:

{topic}

Ищи именно эту тему.
"""

    else:

        directions = """
ПРОВЕРЬ НЕСКОЛЬКО НАПРАВЛЕНИЙ:

1. Россия
2. Мир
3. Политика
4. Конфликты / безопасность
5. Происшествия
6. Экономика / бизнес
7. Технологии / наука / общество

Не обязательно брать каждую категорию.
Выбирай самые сильные события.
"""

    prompt = f"""
Ты — главный редактор новостного Telegram-канала МАРИНАД.

СЕЙЧАС:

{current_time.strftime("%d.%m.%Y %H:%M")} МСК

ПЕРИОД ПОИСКА:

Последние {SEARCH_WINDOW_HOURS} часов.

ФИНАЛЬНО ДОПУСКАЮТСЯ:

Только новости, опубликованные за последние
{FINAL_NEWS_WINDOW_HOURS} часов.

{directions}

ПРИОРИТЕТ:

1. События последних часов.
2. Крупные события сегодняшнего дня.
3. Новые развития важных событий.
4. Россия.
5. Мир.
6. Конфликты и безопасность.
7. Крупные происшествия.
8. Экономика.
9. Технологии и наука.

ИСТОЧНИКИ:

Предпочитай:

Reuters
Associated Press
BBC
AFP
ТАСС
РИА Новости
Интерфакс
официальные государственные источники
крупные СМИ.

КРИТИЧЕСКИ ВАЖНО:

ЗАГОЛОВОК ОБЯЗАТЕЛЬНО НА РУССКОМ ЯЗЫКЕ.

Не пиши английские заголовки.

Не копируй английский headline.

Сформулируй нормальный русский редакционный заголовок
для Telegram-канала.

Не придумывай:

- факты;
- даты;
- цифры;
- погибших;
- цитаты;
- источники;
- URL.

URL должен быть РЕАЛЬНОЙ ссылкой на конкретную публикацию.

Не используй главную страницу СМИ.

Не создавай URL самостоятельно.

Дата публикации должна соответствовать конкретной найденной статье.

Дата события должна соответствовать самому событию.

Если старое событие получило новое развитие,
используй именно новую публикацию.

Найди минимум 8 кандидатов, если это возможно.

Оцени:

freshness 0-10
importance 0-10
interest 0-10
reliability 0-10

ФОРМАТ:

NEWS|РУССКИЙ ЗАГОЛОВОК|publication_iso|event_iso|SOURCE|URL|freshness|importance|interest|reliability

ТОЛЬКО ЭТИ СТРОКИ.

Без Markdown.
Без нумерации.
Без пояснений.

Текущее время:
{current_time.isoformat()}
"""

    try:

        response = client.responses.create(
            model="openai/gpt-oss-120b",
            input=prompt,
            tools=[
                {
                    "type": "browser_search"
                }
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
🧂 <b>МАРИНАД | NEWS BOT</b>

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
""",
        parse_mode="HTML"
    )


# ============================================================
# /MYID
# ============================================================

@bot.message_handler(commands=["myid"])
def myid_command(message):

    bot.reply_to(
        message,
        f"🆔 <b>Ваш Telegram ID:</b>\n{message.from_user.id}",
        parse_mode="HTML"
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
            text,
            parse_mode="HTML"
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
Ты — главный редактор Telegram-канала МАРИНАД.

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

Заголовок должен быть на русском языке.

Обычно пост:
300–700 символов.

Не добавляй:

«СВЕДЕО»
«Новости без лишнего шума»
дату отдельной строкой.

Слоган МАРИНАД:

«Вся соль здесь».

Не используй его автоматически в конце каждого поста.
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
            "🔎 <b>Ищу свежие новости для МАРИНАД...</b>\n\n"
            f"🕐 Сейчас: {format_msk(current_time)} МСК\n"
            f"🔍 Поиск: последние {SEARCH_WINDOW_HOURS} часов\n"
            f"🔒 Финальный фильтр: последние {FINAL_NEWS_WINDOW_HOURS} часов"
        ),
        parse_mode="HTML"
    )

    # ========================================================
    # ПЕРВЫЙ ПОИСК
    # ========================================================

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

    # ========================================================
    # ВТОРОЙ ПОИСК
    # ========================================================

    if len(candidates) < MIN_CANDIDATES:

        raw_second = search_news_second_pass(
            topic
        )

        if not raw_second.startswith("ERROR|"):

            second_candidates = parse_news_candidates(
                raw_second,
                current_time
            )

            candidates.extend(
                second_candidates
            )

    # ========================================================
    # УДАЛЯЕМ ДУБЛИКАТЫ
    # ========================================================

    candidates = remove_duplicates(
        candidates
    )

    # ========================================================
    # СОРТИРУЕМ
    # ========================================================

    candidates = sort_candidates(
        candidates
    )

    # ========================================================
    # ВТОРИЧНАЯ ПРОВЕРКА
    # ========================================================

    verified_candidates = []

    for candidate in candidates[
        :MAX_VERIFICATION_CANDIDATES
    ]:

        verification = verify_candidate(
            candidate
        )

        if verification["verified"]:

            candidate["verified"] = True

            candidate["verification_score"] = 10

            candidate["secondary_source"] = (
                verification["secondary_source"]
            )

            candidate["secondary_url"] = (
                verification["secondary_url"]
            )

            # Бонус за независимое подтверждение
            candidate["score"] = round(
                candidate["score"] + 0.45,
                2
            )

            verified_candidates.append(
                candidate
            )

    # ========================================================
    # ЕСЛИ ВТОРИЧНАЯ ПРОВЕРКА НЕ НАШЛА НИЧЕГО
    # ========================================================

    if len(verified_candidates) == 0:

        # Не показываем потенциально сомнительные новости.
        bot.edit_message_text(
            (
                "⚠️ <b>Надёжных новостей не найдено.</b>\n\n"
                "Поиск дал кандидатов, но они не прошли "
                "вторичную проверку.\n\n"
                "Старые и неподтверждённые материалы "
                "бот не показывает."
            ),
            message.chat.id,
            status.message_id,
            parse_mode="HTML"
        )

        return

    # ========================================================
    # ФИНАЛЬНАЯ СОРТИРОВКА
    # ========================================================

    verified_candidates = sort_candidates(
        verified_candidates
    )

    top_news = verified_candidates[:3]

    # ========================================================
    # ФОРМИРУЕМ КОМПАКТНЫЙ ОТВЕТ
    # ========================================================

    output = []

    output.append(
        "🧂 <b>МАРИНАД | РЕДАКЦИОННЫЙ ОТБОР</b>"
    )

    output.append("")

    output.append(
        f"🕐 {format_msk(current_time)} МСК"
    )

    output.append("")

    for index, item in enumerate(
        top_news,
        start=1
    ):

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
            f"🔥 <b>TOP-{index}</b>"
        )

        output.append(
            f"<b>{item['title']}</b>"
        )

        output.append(
            f"📰 {item['source']}"
        )

        output.append(
            f"🕐 {format_msk(item['publication'])} МСК · {age_text}"
        )

        output.append(
            f"📊 Рейтинг: {item['score']}/10"
        )

        output.append(
            "✅ <b>Подтверждено вторым источником</b>"
        )

        if item["secondary_source"]:

            output.append(
                f"↳ {item['secondary_source']}"
            )

        output.append(
            f"🔗 {item['url']}"
        )

        output.append("")

    output.append(
        "🔒 <i>Старые, англоязычные и "
        "непрошедшие проверку материалы отфильтрованы.</i>"
    )

    final_text = "\n".join(
        output
    )

    bot.edit_message_text(
        final_text,
        message.chat.id,
        status.message_id,
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ============================================================
# ЗАПУСК
# ============================================================

print(
    "🧂 МАРИНАД | Telegram bot started"
)

bot.infinity_polling(
    skip_pending=True
)
