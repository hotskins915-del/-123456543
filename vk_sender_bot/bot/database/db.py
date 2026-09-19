import aiosqlite
from pathlib import Path
from typing import Optional, Dict, Any

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DB_DIR / "users.db"

async def init_db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                vk_id INTEGER,
                vk_name TEXT,
                remixsid TEXT,
                remixnsid TEXT,
                user_token TEXT,
                delay REAL DEFAULT 2.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_user(telegram_id: int) -> Dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            
            # Default new user
            await db.execute(
                "INSERT INTO users (telegram_id, delay) VALUES (?, 2.0)",
                (telegram_id,)
            )
            await db.commit()
            return {
                "telegram_id": telegram_id,
                "vk_id": None,
                "vk_name": None,
                "remixsid": None,
                "remixnsid": None,
                "user_token": None,
                "delay": 2.0
            }

async def save_user_vk_session(telegram_id: int, vk_id: int, vk_name: str, remixsid: str, remixnsid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, vk_id, vk_name, remixsid, remixnsid, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                vk_id = excluded.vk_id,
                vk_name = excluded.vk_name,
                remixsid = excluded.remixsid,
                remixnsid = excluded.remixnsid,
                updated_at = CURRENT_TIMESTAMP
        """, (telegram_id, vk_id, vk_name, remixsid, remixnsid))
        await db.commit()

async def save_user_token(telegram_id: int, vk_id: int, vk_name: str, token: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, vk_id, vk_name, user_token, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                vk_id = excluded.vk_id,
                vk_name = excluded.vk_name,
                user_token = excluded.user_token,
                updated_at = CURRENT_TIMESTAMP
        """, (telegram_id, vk_id, vk_name, token))
        await db.commit()

async def clear_user_vk(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET
                vk_id = NULL,
                vk_name = NULL,
                remixsid = NULL,
                remixnsid = NULL,
                user_token = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
        """, (telegram_id,))
        await db.commit()

async def update_user_delay(telegram_id: int, delay: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, delay, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                delay = excluded.delay,
                updated_at = CURRENT_TIMESTAMP
        """, (telegram_id, delay))
        await db.commit()
