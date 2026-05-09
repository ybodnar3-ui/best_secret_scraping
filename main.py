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
from storage import init_db, get_seen, upsert_seen
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


async def _interruptible_sleep(seconds: float):
    """Sleep in small chunks so SIGTERM is handled promptly."""
    end = asyncio.get_event_loop().time() + seconds
    while not _shutdown:
        remaining = end - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(5.0, remaining))


async def run_once():
    log.info("Запуск перевірки...")
    browser = None

    async with async_playwright() as pw:
        try:
            browser, context, page = await get_authenticated_context(pw)
        except Exception as e:
            log.error(f"Не вдалось авторизуватись: {e}")
            await send_alert(f"⚠️ BestSecret Monitor: помилка авторизації\n{e}")
            return

        try:
            total_new = 0
            total_restock = 0
            total_skipped = 0

            for url in MONITOR_URLS:
                if _shutdown:
                    break

                gender = _gender_from_url(url)
                log.info(f"Перевіряю ({gender}): {url}")

                products = await get_products(page, url)
                log.info(f"Знайдено: {len(products)} товарів")

                for product in products:
                    if _shutdown:
                        break

                    pid = product.get("id")
                    if not pid:
                        log.debug("Пропускаю товар без ID")
                        continue

                    product["gender"] = gender

                    if not matches(product):
                        total_skipped += 1
                        seen = get_seen(pid)
                        if seen is None:
                            upsert_seen(pid, [], gender)
                        continue

                    try:
                        enriched = await enrich_with_sizes(page, product)
                    except Exception as e:
                        log.error(f"Помилка при отриманні розмірів {pid}: {e}")
                        enriched = product

                    current_sizes = _normalize_sizes(enriched.get("sizes", []))
                    enriched["sizes"] = current_sizes
                    seen = get_seen(pid)

                    if seen is None:
                        enriched["notify_type"] = "new"
                        try:
                            await send_product(enriched)
                            total_new += 1
                            log.info(f"🆕 {enriched.get('brand')} — {enriched.get('name')} ({gender})")
                        except Exception as e:
                            log.error(f"Помилка відправки нового товару {pid}: {e}")
                        upsert_seen(pid, current_sizes, gender)
                    else:
                        stored_sizes = set(_normalize_size(s) for s in seen.get("sizes", []))
                        new_sizes = [s for s in current_sizes if s not in stored_sizes]
                        upsert_seen(pid, current_sizes, gender)

                        if new_sizes:
                            enriched["notify_type"] = "restock"
                            enriched["new_sizes"] = new_sizes
                            try:
                                await send_product(enriched)
                                total_restock += 1
                                log.info(f"🔄 {enriched.get('brand')} — нові розміри: {new_sizes} ({gender})")
                            except Exception as e:
                                log.error(f"Помилка відправки рестоку {pid}: {e}")

            log.info(
                f"Перевірку завершено. "
                f"Нових: {total_new}, рестоків: {total_restock}, "
                f"відфільтровано: {total_skipped}"
            )

        except Exception as e:
            log.error(f"Помилка в циклі перевірки:\n{traceback.format_exc()}")
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
