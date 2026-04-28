import sqlite3
from pathlib import Path
from logger import get_logger

log = get_logger("storage")

DB_PATH = Path(__file__).parent / "state" / "seen_products.db"
KEEP_DAYS = 30


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                product_id TEXT PRIMARY KEY,
                seen_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    _cleanup_old()


def _cleanup_old():
    with _conn() as con:
        deleted = con.execute(
            f"DELETE FROM seen WHERE seen_at < DATETIME('now', '-{KEEP_DAYS} days')"
        ).rowcount
    if deleted:
        log.info(f"Очищено {deleted} старих записів з бази (старше {KEEP_DAYS} днів).")


def is_seen(product_id: str) -> bool:
    with _conn() as con:
        row = con.execute("SELECT 1 FROM seen WHERE product_id = ?", (product_id,)).fetchone()
        return row is not None


def mark_seen(product_id: str):
    with _conn() as con:
        con.execute("INSERT OR IGNORE INTO seen (product_id) VALUES (?)", (product_id,))
