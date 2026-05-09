import asyncio
from playwright.async_api import Page
from logger import get_logger

log = get_logger("scraper")


async def _close_cookie_banner(page: Page):
    try:
        await page.click('.closebutton', timeout=3000)
        await asyncio.sleep(0.5)
        log.debug("Cookie banner закритий")
    except Exception:
        pass  # Banner absent or already closed — not an error


async def _scroll_to_bottom(page: Page):
    prev_count = 0
    stable_rounds = 0
    for i in range(80):
        try:
            await page.evaluate("window.scrollBy(0, 900)")
            await asyncio.sleep(1.2)
            count = await page.evaluate(
                "document.querySelectorAll('.product-tile__container').length"
            )
            if count == prev_count:
                stable_rounds += 1
                if stable_rounds >= 3:
                    log.debug(f"Скрол завершено на ітерації {i+1}, товарів: {count}")
                    break
            else:
                stable_rounds = 0
                prev_count = count
        except Exception as e:
            log.warning(f"Помилка при скролі на ітерації {i+1}: {e}")
            break


async def _get_sizes(page: Page, product_url: str) -> list[str]:
    try:
        await page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        try:
            await page.click('.size-selector-button', timeout=4000)
            await asyncio.sleep(1.5)
        except Exception as e:
            log.debug(f"Size selector не знайдено або не вдалось клікнути ({product_url[-40:]}): {e}")

        sizes = await page.evaluate("""
            () => {
                const normalize = s => s.replace(',', '.').replace(/\\s+/g, ' ').trim();
                const seen = new Set();
                const result = [];
                document.querySelectorAll('[role="option"]').forEach(el => {
                    const raw = el.innerText?.trim() || '';
                    const size = normalize(raw.split('\\n')[0]);
                    if (size && size.length <= 12 && !seen.has(size)) {
                        seen.add(size);
                        result.push(size);
                    }
                });
                return result;
            }
        """)

        if not sizes:
            log.debug(f"Розміри не знайдені для {product_url[-40:]}")
        else:
            log.debug(f"Знайдено {len(sizes)} розмірів для {product_url[-40:]}")

        return sizes

    except Exception as e:
        log.warning(f"Не вдалось отримати розміри для {product_url}: {e}")
        return []


_EXTRACT_JS = """
    () => {
        const items = [];
        document.querySelectorAll('.product-tile__container').forEach(card => {
            const link = card.querySelector('a[href]');
            if (!link) return;
            const href = link.href;
            const codeMatch = href.match(/code=(\\d+)/);
            const id = codeMatch ? codeMatch[1] : null;
            if (!id) return;
            const brand = card.querySelector('.designer')?.innerText?.trim() || '';
            const name  = card.querySelector('.name')?.innerText?.trim() || '';
            if (!brand || !name) return;
            const image = card.querySelector('.image-switcher--main-image')?.src || '';
            const toFloat = t => parseFloat(
                (t || '').replace(/[^\\d,]/g, '').replace(',', '.')
            ) || 0;
            const original = toFloat(card.querySelector('.rrp')?.innerText);
            const price    = toFloat(card.querySelector('.sold-price')?.innerText);
            if (price === 0) return;
            const discText  = card.querySelector('.discount-tag')?.innerText || '';
            const discMatch = discText.match(/(\\d+)/);
            const discount  = discMatch ? parseInt(discMatch[1]) :
                (original > 0 ? Math.round((1 - price / original) * 100) : 0);
            items.push({ id, brand, name, price, original, discount, href, image, sizes: [] });
        });
        return items;
    }
"""


async def _get_max_page(page: Page) -> int:
    try:
        return await page.evaluate("""
            () => {
                const nums = [...document.querySelectorAll('a[href]')]
                    .map(a => { const m = a.href.match(/[?&]page=(\\d+)/); return m ? parseInt(m[1]) : 0; });
                return nums.length ? Math.max(...nums) : 1;
            }
        """)
    except Exception:
        return 1


async def _scrape_one_page(page: Page, url: str) -> list[dict]:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        log.error(f"Не вдалось завантажити {url}: {e}")
        return []

    await asyncio.sleep(2)
    await _close_cookie_banner(page)
    await asyncio.sleep(1)
    await _scroll_to_bottom(page)

    try:
        raw = await page.evaluate(_EXTRACT_JS)
        log.debug(f"Розібрано {len(raw)} товарів з {url[-60:]}")
        return raw
    except Exception as e:
        log.error(f"Помилка при парсингу {url}: {e}")
        return []


async def get_products(page: Page, url: str) -> list[dict]:
    # Scrape first page and detect total pages
    first_page = await _scrape_one_page(page, url)

    max_page = await _get_max_page(page)
    log.info(f"Сторінок: {max_page}, товарів на першій: {len(first_page)}")

    if max_page <= 1:
        return first_page

    all_products = list(first_page)
    seen_ids = {p["id"] for p in first_page}

    for page_num in range(2, max_page + 1):
        sep = "&" if "?" in url else "?"
        page_url = f"{url}{sep}page={page_num}"
        products = await _scrape_one_page(page, page_url)
        new = [p for p in products if p["id"] not in seen_ids]
        seen_ids.update(p["id"] for p in new)
        all_products.extend(new)
        log.info(f"Стор. {page_num}/{max_page}: +{len(new)} товарів, всього: {len(all_products)}")

    return all_products


async def enrich_with_sizes(page: Page, product: dict) -> dict:
    href = product.get("href", "")
    log.debug(f"Отримую розміри для {product.get('brand')} — {product.get('name')}")
    sizes = await _get_sizes(page, href)
    return {**product, "sizes": sizes}
