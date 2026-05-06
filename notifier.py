import os
import asyncio
import httpx
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from logger import get_logger

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

log = get_logger("notifier")

_TOKEN_WOMEN = os.getenv("TELEGRAM_TOKEN_WOMEN") or os.getenv("TELEGRAM_TOKEN", "")
_IDS_WOMEN   = os.getenv("TELEGRAM_CHAT_ID_WOMEN") or os.getenv("TELEGRAM_CHAT_ID", "")
_TOKEN_MEN   = os.getenv("TELEGRAM_TOKEN_MEN") or _TOKEN_WOMEN
_IDS_MEN     = os.getenv("TELEGRAM_CHAT_ID_MEN") or _IDS_WOMEN

SEND_DELAY = 2.0


def _chat_ids(raw: str) -> list[str]:
    return [i.strip() for i in raw.split(",") if i.strip()]


def _bot_for(gender: str) -> tuple[str, list[str]]:
    if gender == "men":
        return _TOKEN_MEN, _chat_ids(_IDS_MEN)
    return _TOKEN_WOMEN, _chat_ids(_IDS_WOMEN)


def _escape(text: str) -> str:
    for ch in r'\<>&"':
        text = text.replace(ch, f"\\{ch}")
    return text


def _build_caption(product: dict) -> str:
    notify_type = product.get("notify_type", "new")
    brand       = _escape(str(product.get("brand", "—")))
    name        = _escape(str(product.get("name", "—")))
    price       = product.get("price", 0)
    original    = product.get("original", 0)
    discount    = product.get("discount", 0)
    href        = product.get("href", "")

    if notify_type == "restock":
        new_sizes = product.get("new_sizes", [])
        new_sizes = new_sizes if isinstance(new_sizes, list) else []
        all_sizes = product.get("sizes", [])
        all_sizes = all_sizes if isinstance(all_sizes, list) else []
        header     = "🔄 <b>З'явились нові розміри!</b>"
        sizes_line = f"Нові розміри: <b>{', '.join(new_sizes) or '—'}</b>\nВсі розміри: {', '.join(all_sizes) or '—'}"
    else:
        all_sizes  = product.get("sizes", [])
        all_sizes  = all_sizes if isinstance(all_sizes, list) else []
        header     = "🆕 <b>Новий товар!</b>"
        sizes_line = f"Розміри: {', '.join(all_sizes) or '—'}"

    orig_str = f" <s>{original:.2f} €</s>" if original > 0 else ""
    return (
        f"{header}\n"
        f"<b>{brand}</b> — {name}\n"
        f"Ціна: <b>{price:.2f} €</b>{orig_str}\n"
        f"Знижка: <b>{discount}%</b>\n"
        f"{sizes_line}\n"
        f'<a href="{href}">Відкрити товар</a>'
    )


async def _send_to_one(
    client: httpx.AsyncClient,
    token: str,
    chat_id: str,
    caption: str,
    image_bytes: Optional[bytes],
):
    api = f"https://api.telegram.org/bot{token}"
    sent = False

    if image_bytes:
        try:
            resp = await client.post(
                f"{api}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("photo.jpg", image_bytes, "image/jpeg")},
            )
            if resp.status_code == 200:
                sent = True
            else:
                log.warning(f"sendPhoto [{chat_id}] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            log.warning(f"sendPhoto [{chat_id}] виняток: {e}")

    if not sent:
        try:
            resp = await client.post(
                f"{api}/sendMessage",
                data={"chat_id": chat_id, "text": caption, "parse_mode": "HTML"},
            )
            if resp.status_code == 200:
                log.debug(f"sendMessage [{chat_id}] OK")
            else:
                log.error(f"sendMessage [{chat_id}] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            log.error(f"sendMessage [{chat_id}] виняток: {e}")


async def send_product(product: dict):
    gender = product.get("gender", "women")
    token, chat_ids = _bot_for(gender)

    if not token or not chat_ids:
        log.warning(f"Telegram не налаштований для gender={gender}, пропускаю товар: {product.get('brand')} {product.get('name')}")
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
                else:
                    log.warning(f"Фото не завантажено: HTTP {img_resp.status_code} для {image[:60]}")
            except Exception as e:
                log.warning(f"Не вдалось завантажити фото ({product.get('brand')} {product.get('name')}): {e}")

        for chat_id in chat_ids:
            try:
                await _send_to_one(client, token, chat_id, caption, image_bytes)
            except Exception as e:
                log.error(f"Критична помилка відправки у чат {chat_id}: {e}")
            await asyncio.sleep(0.5)

    notify_type = product.get("notify_type", "new")
    log.info(f"[{notify_type}] {product.get('brand')} — {product.get('name')} {product.get('price')}€ → {gender}")
    await asyncio.sleep(SEND_DELAY)


async def send_alert(text: str):
    tokens_ids: set[tuple[str, str]] = set()
    if _TOKEN_WOMEN and _IDS_WOMEN:
        for cid in _chat_ids(_IDS_WOMEN):
            tokens_ids.add((_TOKEN_WOMEN, cid))
    if _TOKEN_MEN and _IDS_MEN and _TOKEN_MEN != _TOKEN_WOMEN:
        for cid in _chat_ids(_IDS_MEN):
            tokens_ids.add((_TOKEN_MEN, cid))

    if not tokens_ids:
        log.warning("send_alert: Telegram не налаштований, алерт не надіслано")
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for token, chat_id in tokens_ids:
                try:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        data={"chat_id": chat_id, "text": text},
                    )
                    if resp.status_code != 200:
                        log.warning(f"send_alert [{chat_id}] HTTP {resp.status_code}: {resp.text[:100]}")
                except Exception as e:
                    log.warning(f"send_alert [{chat_id}] виняток: {e}")
    except Exception as e:
        log.error(f"send_alert: критична помилка: {e}")
