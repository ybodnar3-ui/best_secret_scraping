import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Page
from playwright_stealth import stealth_async
from logger import get_logger

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

log = get_logger("auth")

BASE_DIR     = Path(__file__).parent
SESSION_FILE = BASE_DIR / "state" / "session.json"
ENTRANCE_URL = "https://www.bestsecret.com/acquisition/entrance"
SHOP_URL     = "https://www.bestsecret.com/new.htm"

MAX_LOGIN_ATTEMPTS = 3


async def _is_logged_in(page: Page) -> bool:
    try:
        await page.goto(SHOP_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        return "bestsecret.com/new" in page.url or "bestsecret.com/startpage" in page.url
    except Exception as e:
        log.warning(f"Перевірка сесії не вдалась: {e}")
        return False


async def _do_login(page: Page) -> bool:
    email    = os.getenv("BS_EMAIL")
    password = os.getenv("BS_PASSWORD")

    if not email or not password:
        raise ValueError("BS_EMAIL або BS_PASSWORD не задані в .env")

    log.info("Відкриваю сторінку входу...")
    await page.goto(ENTRANCE_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    # Remove cookie overlay via JS (blocks pointer events on server)
    await page.evaluate("""
        () => {
            document.querySelectorAll(
                '.cmp-overlay, .cmp, [id*="cmp"], [class*="cookie"], [class*="consent"]'
            ).forEach(el => el.remove());
        }
    """)
    await asyncio.sleep(0.5)

    # Click login button via JS to bypass any remaining overlays
    await page.evaluate("document.querySelector('#login-button')?.click()")
    await page.wait_for_url("**/login.bestsecret.com/**", timeout=15000)
    await asyncio.sleep(1)

    await page.locator('#username').click()
    await page.locator('#username').fill(email)
    await page.locator('#username').press('Tab')
    await asyncio.sleep(0.5)

    await page.locator('#password').click()
    await page.locator('#password').fill(password)
    await page.locator('#password').press('Tab')
    await asyncio.sleep(0.5)

    await page.locator('button[type="submit"]').first.click()

    try:
        await page.wait_for_url("**/bestsecret.com/**", timeout=30000)
    except Exception:
        pass

    await asyncio.sleep(3)
    success = "bestsecret.com/new" in page.url or "bestsecret.com/startpage" in page.url
    if not success:
        log.warning(f"Логін не вдався, поточна URL: {page.url}")
    return success


async def get_authenticated_context(playwright) -> tuple[Browser, BrowserContext, Page]:
    browser = await playwright.chromium.launch(headless=True)

    context_options = {
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1280, "height": 800},
        "locale": "de-DE",
    }

    if SESSION_FILE.exists():
        context_options["storage_state"] = str(SESSION_FILE)

    context = await browser.new_context(**context_options)
    page    = await context.new_page()
    await stealth_async(page)

    if await _is_logged_in(page):
        log.info("Сесія відновлена з файлу.")
        return browser, context, page

    # Session invalid — delete and re-login
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
        log.info("Стара сесія видалена, перелогінююсь...")
        await page.close()
        await context.close()
        context = await browser.new_context(
            user_agent=context_options["user_agent"],
            viewport=context_options["viewport"],
            locale=context_options["locale"],
        )
        page = await context.new_page()
        await stealth_async(page)

    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        log.info(f"Спроба логіну {attempt}/{MAX_LOGIN_ATTEMPTS}...")
        try:
            success = await _do_login(page)
            if success:
                SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                await context.storage_state(path=str(SESSION_FILE))
                log.info("Залогінився і зберіг сесію.")
                return browser, context, page
        except Exception as e:
            log.error(f"Помилка при спробі логіну {attempt}: {e}")

        if attempt < MAX_LOGIN_ATTEMPTS:
            wait = 30 * attempt
            log.info(f"Чекаю {wait} сек перед наступною спробою...")
            await asyncio.sleep(wait)

    await browser.close()
    raise RuntimeError(f"Не вдалось залогінитись після {MAX_LOGIN_ATTEMPTS} спроб.")
