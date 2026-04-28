import os
import asyncio
import httpx
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from logger import get_logger

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

log = get_logger("notifier")

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
_raw_ids          = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_IDS = [i.strip() for i in _raw_ids.split(",") if i.strip()]
API_BASE          = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Pause between sends to avoid Telegram rate limits
SEND_DELAY = 2.0


def _escape(text: str) -> str:
    for ch in r'\<>&"':
        text = text.replace(ch, f"\\{ch}")
    return text


def _build_caption(product: dict) -> str:
    brand    = _escape(str(product.get("brand", "—")))
    name     = _escape(str(product.get("name", "—")))
    price    = product.get("price", 0)
    original = product.get("original", 0)
    discount = product.get("discount", 0)
    sizes    = ", ".join(product.get("sizes", [])) or "—"
    href     = product.get("href", "")

    orig_str = f" <s>{original:.2f} €</s>" if original > 0 else ""
    return (
        f"<b>{brand}</b> — {name}\n"
        f"Ціна: <b>{price:.2f} €</b>{orig_str}\n"
        f"Знижка: <b>{discount}%</b>\n"
        f"Розміри: {sizes}\n"
        f'<a href="{href}">Відкрити товар</a>'
    )


async def _send_to_one(client: httpx.AsyncClient, chat_id: str, caption: str, image_bytes: Optional[bytes]):
    sent = False
    if image_bytes:
        try:
            resp = await client.post(
                f"{API_BASE}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("photo.jpg", image_bytes, "image/jpeg")},
            )
            if resp.status_code == 200:
                sent = True
            else:
                log.warning(f"sendPhoto [{chat_id}] помилка: {resp.text[:200]}")
        except Exception as e:
            log.warning(f"sendPhoto [{chat_id}] виняток: {e}")

    if not sent:
        resp = await client.post(
            f"{API_BASE}/sendMessage",
            data={"chat_id": chat_id, "text": caption, "parse_mode": "HTML"},
        )
        if resp.status_code != 200:
            log.error(f"sendMessage [{chat_id}] помилка: {resp.text[:200]}")


async def send_product(product: dict):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        log.warning("TELEGRAM_TOKEN або TELEGRAM_CHAT_ID не задані — пропускаю.")
        return

    caption     = _build_caption(product)
    image       = product.get("image", "")
    image_bytes = None

    async with httpx.AsyncClient(timeout=30) as client:
        if image:
            try:
                img_resp = await client.get(image, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                    "Referer":    "https://www.bestsecret.com/",
                })
                if img_resp.status_code == 200 and img_resp.headers.get("content-type", "").startswith("image/"):
                    image_bytes = img_resp.content
            except Exception as e:
                log.warning(f"Не вдалось завантажити фото: {e}")

        for chat_id in TELEGRAM_CHAT_IDS:
            await _send_to_one(client, chat_id, caption, image_bytes)
            await asyncio.sleep(0.5)

    log.info(f"Надіслано: {product.get('brand')} — {product.get('name')} — {product.get('price')}€")
    await asyncio.sleep(SEND_DELAY)


async def send_alert(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for chat_id in TELEGRAM_CHAT_IDS:
                await client.post(
                    f"{API_BASE}/sendMessage",
                    data={"chat_id": chat_id, "text": text},
                )
    except Exception:
        pass
