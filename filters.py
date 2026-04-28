import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

SHOE_KEYWORDS = [
    'sneaker', 'boot', 'shoe', 'sandal', 'loafer', 'pump', 'heel',
    'moccasin', 'slingback', 'platform', 'ballerina', 'espadrille',
    'trainer', 'oxford', 'derby', 'stiletto', 'wedge', 'slip-on',
    'ankle boot', 'high heel', 'flat', 'schuhe', 'stiefel', 'sneakers',
]


def _env_list(key: str) -> list[str]:
    val = os.getenv(key, "")
    return [x.strip().lower() for x in val.split(",") if x.strip()]


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _is_shoe(product: dict) -> bool:
    text = (product.get("name", "") + " " + product.get("brand", "")).lower()
    return any(k in text for k in SHOE_KEYWORDS)


def matches(product: dict) -> bool:
    # If we're monitoring the luxury section, filter to shoes only
    if os.getenv("FILTER_SHOES_ONLY", "false").lower() == "true":
        if not _is_shoe(product):
            return False

    brands    = _env_list("FILTER_BRANDS")
    sizes     = _env_list("FILTER_SIZES")
    max_price = _env_float("FILTER_MAX_PRICE", 0)
    min_disc  = _env_float("FILTER_MIN_DISCOUNT", 0)

    if brands:
        if not any(b in product.get("brand", "").lower() for b in brands):
            return False

    if sizes:
        product_sizes = [s.lower() for s in product.get("sizes", [])]
        if not any(s in product_sizes for s in sizes):
            return False

    if max_price > 0 and product.get("price", 0) > max_price:
        return False

    if min_disc > 0 and product.get("discount", 0) < min_disc:
        return False

    return True
