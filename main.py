import os
import re
import html
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from functools import wraps
from difflib import SequenceMatcher

import telebot
from openai import OpenAI


# ============================================================
# НАСТРОЙКИ
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

CHANNEL = "@marinadnews"
ADMIN_ID = 6056292876

# Основная модель выбрана экономичнее, чем gpt-oss-120b.
# Её можно заменить в Railway → Variables без правки кода.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_TIMEOUT_SECONDS = 75
GROQ_MAX_RETRIES = 0  # не тратим токены на скрытые повторы SDK

MSK = timezone(timedelta(hours=3))

# Ищем шире
SEARCH_WINDOW_HOURS = 48

# Но в финальный TOP допускаем только свежие новости
FINAL_NEWS_WINDOW_HOURS = 24

# Допустимая погрешность времени
FUTURE_TOLERANCE_MINUTES = 15

# Минимальное количество кандидатов
MIN_CANDIDATES = 3

# Сколько новостей дополнительно проверяем.
# Меньше проверок = меньше отдельных browser_search-вызовов.
MAX_VERIFICATION_CANDIDATES = 3

# Ограничиваем одновременные поиски, чтобы команды не сжигали лимит параллельно.
NEWS_LOCK = threading.Lock()

# Кэш проверок ссылок в рамках работы процесса.
URL_CHECK_CACHE = {}
URL_CHECK_CACHE_TTL = 300


# ============================================================
# TELEGRAM
# ============================================================

if not TOKEN:
    raise RuntimeError("Не задана переменная окружения TELEGRAM_BOT_TOKEN в Railway.")

if not GROQ_API_KEY:
    raise RuntimeError("Не задана переменная окружения GROQ_API_KEY в Railway.")

bot = telebot.TeleBot(TOKEN)


# ============================================================
# GROQ
# ============================================================

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
    timeout=GROQ_TIMEOUT_SECONDS,
    max_retries=GROQ_MAX_RETRIES,
)


def friendly_api_error(error):
    """Преобразует ошибки API в короткие понятные сообщения без лишнего дампа."""
    message = str(error)
    status = getattr(error, "status_code", None)
    lowered = message.lower()

    if status == 429 or "rate_limit" in lowered or "rate limit" in lowered:
        return (
            "Groq временно ограничил запросы или исчерпан лимит модели. "
            "Подожди восстановления лимита и повтори команду. "
            "Проверь Usage/Limits в консоли Groq."
        )

    if status in (401, 403) or "authentication" in lowered or "invalid api key" in lowered:
        return "Groq отклонил авторизацию. Проверь переменную GROQ_API_KEY в Railway."

    if "model" in lowered and ("not found" in lowered or "unsupported" in lowered):
        return (
            f"Модель {GROQ_MODEL} недоступна для этого запроса. "
            "Проверь имя модели в Railway → Variables."
        )

    return f"Ошибка внешнего API ({status or 'без кода'}): {message[:500]}"


def is_rate_limit_error(error):
    message = str(error).lower()
    return (
        getattr(error, "status_code", None) == 429
        or "rate_limit" in message
        or "rate limit" in message
        or "tokens per day" in message
        or "tokens per minute" in message
    )


def prevent_concurrent_news(function):
    """Не даёт нескольким /news запускать тяжёлые поиски одновременно."""
    @wraps(function)
    def wrapped(message):
        if not NEWS_LOCK.acquire(blocking=False):
            bot.reply_to(
                message,
                "⏳ Поиск новостей уже выполняется. Дождись ответа и затем повтори команду."
            )
            return
        try:
            return function(message)
        finally:
            NEWS_LOCK.release()
    return wrapped


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
        try:
            bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception:
            plain_text = html.unescape(re.sub(r"<[^>]*>", "", text))
            bot.send_message(
                chat_id,
                plain_text,
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
        try:
            bot.send_message(
                chat_id,
                part,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception:
            plain_text = html.unescape(re.sub(r"<[^>]*>", "", part))
            bot.send_message(
                chat_id,
                plain_text,
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
        domain = (parsed.hostname or "").lower()
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
    """Проверяет HTTPS, разрешённый домен и базовую доступность ссылки."""
    if not url or not isinstance(url, str):
        return False, "пустой URL"

    url = url.strip()
    cached = URL_CHECK_CACHE.get(url)
    if cached and time.time() - cached[0] < URL_CHECK_CACHE_TTL:
        return cached[1], cached[2]

    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            result = (False, "нужна корректная HTTPS-ссылка")
        elif parsed.username or parsed.password:
            result = (False, "URL с данными авторизации запрещён")
        elif not is_trusted_domain(url):
            result = (False, f"неразрешённый источник: {get_domain(url)}")
        else:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; MARINAD-NewsBot/2.0)"
                },
                method="HEAD",
            )
            try:
                with urllib.request.urlopen(request, timeout=4) as response:
                    status = response.status
                if 200 <= status < 400 or status in (401, 403, 405, 429):
                    result = (True, f"сервер ответил HTTP {status}")
                else:
                    result = (False, f"HTTP {status}")
            except urllib.error.HTTPError as error:
                if error.code in (401, 403, 405, 429):
                    result = (True, f"сервер ответил HTTP {error.code}")
                else:
                    result = (False, f"HTTP {error.code}")
            except Exception:
                # Часть СМИ блокирует HEAD или автоматические запросы.
                # Для доверенного домена с таймаутом не утверждаем, что ссылка фальшивая.
                result = (True, "домен доверенный, доступность страницы не подтверждена")

    except Exception:
        result = (False, "некорректный URL")

    URL_CHECK_CACHE[url] = (time.time(), result[0], result[1])
    return result


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

        if not title or not source or len(title) > 280:
            continue

        try:
            freshness = int(parts[6].strip())
            importance = int(parts[7].strip())
            interest = int(parts[8].strip())
            reliability = int(parts[9].strip())
        except (ValueError, TypeError):
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
    normalized = (title or "").lower().replace("ё", "е")
    normalized = re.sub(r"[^а-яa-z0-9 ]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def remove_duplicates(candidates):
    result = []
    seen_urls = set()
    seen_titles = []

    for item in candidates:
        url_key = (item.get("url") or "").rstrip("/").lower()
        title_key = normalize_title(item.get("title", ""))

        if not title_key or url_key in seen_urls:
            continue

        # Убираем почти одинаковые заголовки из разных перепечаток.
        if any(
            SequenceMatcher(None, title_key, existing).ratio() >= 0.90
            for existing in seen_titles
        ):
            continue

        seen_urls.add(url_key)
        seen_titles.append(title_key)
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
    """Проверяет публикацию и различает двойное подтверждение и один источник."""
    prompt = f"""
Ты — фактчекер Telegram-канала МАРИНАД. Выполни browser search.

ПРОВЕРЯЕМАЯ НОВОСТЬ:
Заголовок: {candidate["title"]}
Источник: {candidate["source"]}
URL: {candidate["url"]}
Заявленная дата: {candidate["publication"].isoformat()}

Сначала найди ИМЕННО указанную публикацию. Убедись, что страница/результат
реально существует, содержание соответствует заголовку, а дата не противоречит
заявленной дате. Не делай вывод только по совпадению домена.

Затем ищи независимое подтверждение у другого доверенного СМИ или официального
источника. Перепечатки одного агентства не считаются независимыми.
Не выдумывай названия источников, даты или URL.

Верни ровно одну строку в одном из форматов:
VERIFY|YES|secondary_source|secondary_url|reason
SINGLE|YES|NONE|NONE|reason
VERIFY|NO|NONE|NONE|reason

Используй VERIFY|YES только если нашёл реальную исходную публикацию И реальную
независимую публикацию на другом домене, подтверждающую то же событие.
Используй SINGLE|YES только если исходная публикация действительно найдена,
заголовок и дата соответствуют, но независимое подтверждение найти не удалось.
Если исходная публикация не найдена, дата/содержание не совпадают или результат
неоднозначен — VERIFY|NO. Не выдавай отсутствие подтверждения за доказательство лжи.
Никаких дополнительных строк.
"""

    try:
        response = client.responses.create(
            model=GROQ_MODEL,
            input=prompt,
            max_output_tokens=900,
            tools=[{"type": "browser_search"}],
            tool_choice="required",
            reasoning={"effort": "low"},
        )
        result = (response.output_text or "").strip()

        for line in result.splitlines():
            line = line.strip()
            if not (line.startswith("VERIFY|") or line.startswith("SINGLE|")):
                continue
            parts = line.split("|", 4)
            if len(parts) < 5:
                continue

            kind = parts[0].strip().upper()
            decision = parts[1].strip().upper()
            source = parts[2].strip()
            url = parts[3].strip()
            reason = parts[4].strip()

            if kind == "SINGLE" and decision == "YES":
                # Кандидат остаётся только в явно помеченной категории одного источника.
                primary_ok, primary_reason = check_url(candidate.get("url", ""))
                if not primary_ok or not is_trusted_domain(candidate.get("url", "")):
                    return {
                        "verified": False, "single_source": False,
                        "secondary_source": "", "secondary_url": "",
                        "reason": f"исходная ссылка не прошла проверку: {primary_reason}",
                        "error": False, "rate_limited": False,
                    }
                return {
                    "verified": False, "single_source": True,
                    "secondary_source": "", "secondary_url": "",
                    "reason": reason or "исходная публикация найдена; независимое подтверждение не найдено",
                    "error": False, "rate_limited": False,
                }

            if kind == "VERIFY" and decision == "YES":
                primary_domain = get_domain(candidate.get("url", ""))
                secondary_domain = get_domain(url)
                if not source or not url or url.upper() == "NONE":
                    return {
                        "verified": False, "single_source": False,
                        "secondary_source": "", "secondary_url": "",
                        "reason": "нет ссылки на независимое подтверждение",
                        "error": False, "rate_limited": False,
                    }
                if not is_trusted_domain(url):
                    return {
                        "verified": False, "single_source": False,
                        "secondary_source": "", "secondary_url": "",
                        "reason": "второй источник не входит в список доверенных",
                        "error": False, "rate_limited": False,
                    }
                if secondary_domain == primary_domain:
                    return {
                        "verified": False, "single_source": False,
                        "secondary_source": "", "secondary_url": "",
                        "reason": "второй источник совпадает с первым",
                        "error": False, "rate_limited": False,
                    }
                secondary_ok, secondary_reason = check_url(url)
                if not secondary_ok:
                    return {
                        "verified": False, "single_source": False,
                        "secondary_source": "", "secondary_url": "",
                        "reason": f"вторичная ссылка не прошла проверку: {secondary_reason}",
                        "error": False, "rate_limited": False,
                    }
                return {
                    "verified": True, "single_source": False,
                    "secondary_source": source, "secondary_url": url,
                    "reason": reason, "error": False, "rate_limited": False,
                }

            return {
                "verified": False, "single_source": False,
                "secondary_source": "", "secondary_url": "",
                "reason": reason or "проверка не пройдена",
                "error": False, "rate_limited": False,
            }

        return {
            "verified": False, "single_source": False,
            "secondary_source": "", "secondary_url": "",
            "reason": "ответ фактчекера не распознан", "error": False,
            "rate_limited": False,
        }

    except Exception as e:
        return {
            "verified": False, "single_source": False,
            "secondary_source": "", "secondary_url": "",
            "reason": friendly_api_error(e), "error": True,
            "rate_limited": is_rate_limit_error(e),
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
            model=GROQ_MODEL,
            input=prompt,
            max_output_tokens=1000,
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

Найди до 8 кандидатов, если это возможно. Не заполняй список выдуманными новостями.

Пиши максимально компактно, без повторов и лишних пояснений.

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
            model=GROQ_MODEL,
            input=prompt,
            max_output_tokens=700,
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
            "❌ Не удалось опубликовать пост. "
            "Проверь права бота в канале, длину текста и HTML-разметку.\n"
            + str(e)[:400]
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

            model=GROQ_MODEL,
            max_output_tokens=1500,

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
- «СВЕДЕО»;
- «Новости без лишнего шума»;
- дату отдельной строкой.

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
            "❌ " + friendly_api_error(e),
            message.chat.id,
            status.message_id
        )


# ============================================================
# /NEWS
# ============================================================

@bot.message_handler(commands=["news"])
@prevent_concurrent_news
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
        error_text = raw_first[6:]
        bot.edit_message_text(
            "❌ <b>Не удалось выполнить поиск.</b>\n\n"
            + html.escape(friendly_api_error(Exception(error_text))),
            message.chat.id,
            status.message_id,
            parse_mode="HTML",
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
    single_source_candidates = []
    verification_errors = 0
    rate_limited = False
    rejection_reasons = []

    for candidate in candidates[:MAX_VERIFICATION_CANDIDATES]:
        verification = verify_candidate(candidate)

        if verification.get("error"):
            verification_errors += 1
            rate_limited = rate_limited or verification.get("rate_limited", False)

        reason = verification.get("reason", "")
        if reason and not verification.get("verified") and not verification.get("single_source"):
            rejection_reasons.append(reason[:120])

        if verification.get("verified"):
            candidate["verified"] = True
            candidate["single_source"] = False
            candidate["verification_score"] = 10
            candidate["secondary_source"] = verification["secondary_source"]
            candidate["secondary_url"] = verification["secondary_url"]
            candidate["score"] = round(candidate["score"] + 0.45, 2)
            verified_candidates.append(candidate)

        elif verification.get("single_source"):
            candidate["verified"] = False
            candidate["single_source"] = True
            candidate["secondary_source"] = ""
            candidate["secondary_url"] = ""
            candidate["verification_reason"] = reason
            # Не повышаем рейтинг: одиночный источник уступает двойному подтверждению.
            single_source_candidates.append(candidate)

    # Двойное подтверждение всегда имеет приоритет. К одиночным источникам
    # переходим только если ни одна новость не получила независимого подтверждения.
    using_single_source = not verified_candidates and bool(single_source_candidates)

    if not verified_candidates and not single_source_candidates:
        if verification_errors:
            if rate_limited:
                message_text = (
                    "⏳ <b>Groq временно ограничил запросы.</b>\n\n"
                    "Бот нашёл кандидатов, но не смог завершить фактчекинг. "
                    "Это не означает, что новости ложные. Подожди восстановления лимита "
                    "и повтори /news."
                )
            else:
                message_text = (
                    "⚠️ <b>Не удалось завершить проверку новостей.</b>\n\n"
                    "Внешний сервис не вернул корректный результат. "
                    "Бот не будет помечать кандидатов как подтверждённые. Попробуй позже."
                )
        else:
            details = ""
            if rejection_reasons:
                unique_reasons = list(dict.fromkeys(rejection_reasons))[:3]
                details = "\n\nПричины отказа: " + "; ".join(
                    html.escape(reason) for reason in unique_reasons
                )
            message_text = (
                "⚠️ <b>Не удалось подтвердить найденные новости.</b>\n\n"
                "Бот проверил публикации, но не получил достаточных доказательств "
                "для безопасного вывода. Это не доказывает, что новости ложные."
                + details
            )

        bot.edit_message_text(
            message_text,
            message.chat.id,
            status.message_id,
            parse_mode="HTML",
        )
        return

    if using_single_source:
        selected_candidates = single_source_candidates
    else:
        selected_candidates = verified_candidates

    # ========================================================
    # ФИНАЛЬНАЯ СОРТИРОВКА
    # ========================================================

    selected_candidates = sort_candidates(selected_candidates)

    top_news = selected_candidates[:3]

    # ========================================================
    # ФОРМИРУЕМ КОМПАКТНЫЙ ОТВЕТ
    # ========================================================

    output = []

    output.append("🧂 <b>МАРИНАД | РЕДАКЦИОННЫЙ ОТБОР</b>")
    if using_single_source:
        output.append("⚠️ <b>Режим одного источника</b>")
        output.append("Независимое подтверждение не найдено. Проверяй материал перед публикацией.")

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

        safe_title = html.escape(item["title"])
        safe_source = html.escape(item["source"])
        safe_secondary_source = html.escape(item.get("secondary_source", ""))

        output.append(f"<b>{safe_title}</b>")
        output.append(f"📰 {safe_source}")

        output.append(
            f"🕐 {format_msk(item['publication'])} МСК · {age_text}"
        )

        output.append(
            f"📊 Рейтинг: {item['score']}/10"
        )

        if item.get("single_source"):
            output.append("⚠️ <b>Один доверенный источник · без независимого подтверждения</b>")
        else:
            output.append("✅ <b>Подтверждено вторым источником</b>")
            if item.get("secondary_source"):
                output.append(f"↳ {safe_secondary_source}")

        safe_url = html.escape(item["url"], quote=True)
        output.append(f"🔗 {safe_url}")
        if item.get("secondary_url"):
            safe_secondary_url = html.escape(item["secondary_url"], quote=True)
            output.append(f"↳ Подтверждение: {safe_secondary_url}")

        output.append("")

    if using_single_source:
        output.append("⚠️ <i>Эти материалы прошли проверку исходной публикации, но независимое подтверждение не найдено. Не публикуй их как подтверждённые несколькими источниками.</i>")
    else:
        output.append("🔒 <i>Старые, англоязычные и непрошедшие проверку материалы отфильтрованы.</i>")

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
    skip_pending=True,
    timeout=30,
    long_polling_timeout=25,
)
