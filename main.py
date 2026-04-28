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
from storage import init_db, is_seen, mark_seen
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
            for url in MONITOR_URLS:
                if _shutdown:
                    break

                log.info(f"Перевіряю: {url}")
                products = await get_products(page, url)
                log.info(f"Знайдено: {len(products)} товарів")

                new_count = 0
                for product in products:
                    if _shutdown:
                        break

                    pid = product.get("id")
                    if not pid or is_seen(pid):
                        continue

                    mark_seen(pid)
                    new_count += 1

                    if matches(product):
                        try:
                            enriched = await enrich_with_sizes(page, product)
                            await send_product(enriched)
                        except Exception as e:
                            log.error(f"Помилка при відправці товару {pid}: {e}")

                log.info(f"Нових товарів: {new_count}")

        except Exception as e:
            log.error(f"Помилка в циклі перевірки:\n{traceback.format_exc()}")
            await send_alert(f"⚠️ BestSecret Monitor: помилка перевірки\n{e}")
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    log.info("Перевірку завершено.")


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
        await asyncio.sleep(int(delay * 60))

    log.info("Моніторинг зупинено.")
    await send_alert("🛑 BestSecret Monitor зупинено.")


if __name__ == "__main__":
    asyncio.run(loop())
