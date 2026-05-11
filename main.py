import asyncio
import os
import random
import signal
import sys
import traceback
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

from auth import get_authenticated_context
from scraper import get_products, enrich_with_sizes
from filters import matches
from notifier import send_product, send_alert
from storage import init_db, get_seen, upsert_seen, get_total_count
from logger import get_logger

log = get_logger("main")

INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "15"))
INTERVAL_MAX = int(os.getenv("INTERVAL_MAX", "30"))
MONITOR_URLS = [u.strip() for u in os.getenv("MONITOR_URLS", "").split(",") if u.strip()]

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    log.info(f"Отримано сигнал {sig}, зупиняюсь після поточного циклу...")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _normalize_size(s: str) -> str:
    return s.replace(",", ".").strip().upper()


def _normalize_sizes(sizes: list[str]) -> list[str]:
    seen = set()
    result = []
    for s in sizes:
        n = _normalize_size(s)
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _gender_from_url(url: str) -> str:
    url_lower = url.lower()
    if "women" in url_lower:
        return "women"
    if "men" in url_lower:
        return "men"
    return "unisex"


def _url_sort_key(url: str) -> int:
    """Men's URLs processed first so unisex products go to men's bot."""
    u = url.lower()
    if "women" in u:
        return 1
    if "men" in u:
        return 0
    return 2


async def _interruptible_sleep(seconds: float):
    """Sleep in small chunks so SIGTERM is handled promptly."""
    end = asyncio.get_event_loop().time() + seconds
    while not _shutdown:
        remaining = end - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(5.0, remaining))


async def _process_url(page, url: str, send: bool,
                       sent_this_run: set[str],
                       counters: dict) -> None:
    """Scan one URL. If send=False, silently populate DB only."""
    gender = _gender_from_url(url)
    log.info(f"{'Перевіряю' if send else 'Тихе сканування'} ({gender}): {url}")

    pause = random.uniform(3, 8)
    await asyncio.sleep(pause)

    products = await get_products(page, url)
    log.info(f"Знайдено: {len(products)} товарів")

    for product in products:
        if _shutdown:
            return

        pid = product.get("id")
        if not pid:
            continue

        product["gender"] = gender

        if not matches(product):
            counters["skipped"] += 1
            try:
                if get_seen(pid) is None:
                    upsert_seen(pid, [], gender)
            except Exception as e:
                log.warning(f"DB write failed for skipped {pid}: {e}")
            continue

        try:
            enriched = await enrich_with_sizes(page, product)
        except Exception as e:
            log.error(f"Помилка при отриманні розмірів {pid}: {e}")
            enriched = product

        current_sizes = _normalize_sizes(enriched.get("sizes", []))
        enriched["sizes"] = current_sizes

        try:
            seen = get_seen(pid)
        except Exception as e:
            log.error(f"DB read failed for {pid}: {e}")
            continue

        if seen is None:
            try:
                upsert_seen(pid, current_sizes, gender)
            except Exception as e:
                log.error(f"DB write failed for new {pid}: {e}")
                continue
            if not send or pid in sent_this_run:
                continue
            enriched["notify_type"] = "new"
            try:
                await send_product(enriched)
                counters["new"] += 1
                sent_this_run.add(pid)
                log.info(f"🆕 {enriched.get('brand')} — {enriched.get('name')} ({gender})")
            except Exception as e:
                log.error(f"Помилка відправки нового товару {pid}: {e}")
        else:
            stored_sizes = set(_normalize_size(s) for s in seen.get("sizes", []))
            new_sizes = [s for s in current_sizes if s not in stored_sizes]
            # Union: never drop previously-seen sizes. A size that sells out
            # and comes back must NOT re-trigger a notification.
            merged_sizes = list(stored_sizes | set(current_sizes))
            try:
                upsert_seen(pid, merged_sizes, gender)
            except Exception as e:
                log.error(f"DB write failed for restock {pid}: {e}")
                continue

            if new_sizes and send and pid not in sent_this_run:
                enriched["notify_type"] = "restock"
                enriched["new_sizes"] = new_sizes
                try:
                    await send_product(enriched)
                    counters["restock"] += 1
                    sent_this_run.add(pid)
                    log.info(f"🔄 {enriched.get('brand')} — нові розміри: {new_sizes} ({gender})")
                except Exception as e:
                    log.error(f"Помилка відправки рестоку {pid}: {e}")


async def run_once(send: bool = True):
    log.info("Запуск перевірки..." if send else "Тихе початкове сканування...")
    browser = None

    # Men's URLs first: unisex products claim by men's bot before women's URL processes them
    sorted_urls = sorted(MONITOR_URLS, key=_url_sort_key)

    async with async_playwright() as pw:
        try:
            browser, context, page = await get_authenticated_context(pw)
        except Exception as e:
            log.error(f"Не вдалось авторизуватись: {e}")
            if send:
                await send_alert(f"⚠️ BestSecret Monitor: помилка авторизації\n{e}")
            return

        try:
            counters = {"new": 0, "restock": 0, "skipped": 0}
            sent_this_run: set[str] = set()

            for url in sorted_urls:
                if _shutdown:
                    break
                await _process_url(page, url, send, sent_this_run, counters)

            if send:
                log.info(
                    f"Перевірку завершено. "
                    f"Нових: {counters['new']}, рестоків: {counters['restock']}, "
                    f"відфільтровано: {counters['skipped']}"
                )
            else:
                log.info(f"Тихе сканування завершено. Збережено в БД.")

        except Exception as e:
            log.error(f"Помилка в циклі перевірки:\n{traceback.format_exc()}")
            if send:
                await send_alert(f"⚠️ BestSecret Monitor: помилка перевірки\n{e}")
        finally:
            try:
                await browser.close()
            except Exception:
                pass


async def loop():
    init_db()

    if not MONITOR_URLS:
        log.error("MONITOR_URLS не задано в .env — виходжу.")
        sys.exit(1)

    # If DB is empty (fresh container / first run), silently catalogue all products
    # before sending any notifications — prevents spam after restarts/redeployments.
    if get_total_count() == 0:
        log.info("БД порожня — виконую тихе початкове сканування без відправки сповіщень...")
        await run_once(send=False)
        log.info("Початкове сканування завершено. Починаю нормальну роботу.")

    log.info(f"Моніторинг запущено. URL: {len(MONITOR_URLS)}, інтервал: {INTERVAL_MIN}–{INTERVAL_MAX} хв.")
    await send_alert("✅ BestSecret Monitor запущено.")

    while not _shutdown:
        try:
            await run_once()
        except Exception as e:
            log.error(f"Критична помилка:\n{traceback.format_exc()}")
            await send_alert(f"⚠️ BestSecret Monitor: критична помилка\n{e}")

        if _shutdown:
            break

        delay = random.uniform(INTERVAL_MIN, INTERVAL_MAX)
        log.info(f"Наступна перевірка через {delay:.1f} хв...")
        await _interruptible_sleep(delay * 60)

    log.info("Моніторинг зупинено.")
    await send_alert("🛑 BestSecret Monitor зупинено.")


if __name__ == "__main__":
    asyncio.run(loop())
