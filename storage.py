import json
import sqlite3
from pathlib import Path
from logger import get_logger

log = get_logger("storage")

DB_PATH   = Path(__file__).parent / "state" / "seen_products.db"
KEEP_DAYS = 60


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                product_id  TEXT PRIMARY KEY,
                sizes_json  TEXT    DEFAULT '[]',
                gender      TEXT    DEFAULT '',
                first_seen  DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrate old schema that had no sizes_json column
        cols = [r[1] for r in con.execute("PRAGMA table_info(seen)").fetchall()]
        if "sizes_json" not in cols:
            con.execute("ALTER TABLE seen ADD COLUMN sizes_json TEXT DEFAULT '[]'")
        if "gender" not in cols:
            con.execute("ALTER TABLE seen ADD COLUMN gender TEXT DEFAULT ''")
        if "first_seen" not in cols:
            con.execute("ALTER TABLE seen ADD COLUMN first_seen DATETIME DEFAULT CURRENT_TIMESTAMP")
        if "last_seen" not in cols:
            con.execute("ALTER TABLE seen ADD COLUMN last_seen DATETIME DEFAULT CURRENT_TIMESTAMP")
    _cleanup_old()


def _cleanup_old():
    with _conn() as con:
        deleted = con.execute(
            f"DELETE FROM seen WHERE last_seen < DATETIME('now', '-{KEEP_DAYS} days')"
        ).rowcount
    if deleted:
        log.info(f"Очищено {deleted} старих записів (старше {KEEP_DAYS} днів).")


def get_seen(product_id: str) -> dict | None:
    """Returns stored record or None if never seen."""
    with _conn() as con:
        row = con.execute(
            "SELECT sizes_json, gender, first_seen FROM seen WHERE product_id = ?",
            (product_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "sizes":      json.loads(row[0] or "[]"),
        "gender":     row[1],
        "first_seen": row[2],
    }


def upsert_seen(product_id: str, sizes: list[str], gender: str):
    """Insert new or update sizes + last_seen timestamp."""
    with _conn() as con:
        con.execute("""
            INSERT INTO seen (product_id, sizes_json, gender)
            VALUES (?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                sizes_json = excluded.sizes_json,
                gender     = excluded.gender,
                last_seen  = CURRENT_TIMESTAMP
        """, (product_id, json.dumps(sizes, ensure_ascii=False), gender))


def is_seen(product_id: str) -> bool:
    return get_seen(product_id) is not None
