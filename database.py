import os
# pyrefly: ignore [missing-import]
import asyncpg
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("DATABASE_URL")
TZ = ZoneInfo("Europe/Moscow")

pool: asyncpg.Pool = None

async def init_db():
    global pool
    if not DATABASE_URL:
        print("DATABASE_URL is not set!")
        return
    pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shops (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                open_time TIME DEFAULT '09:00',
                close_time TIME DEFAULT '21:00'
            );
        """)
        await conn.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS open_time TIME DEFAULT '09:00'")
        await conn.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS close_time TIME DEFAULT '21:00'")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                full_name TEXT NOT NULL,
                shop_id INT REFERENCES shops(id) ON DELETE SET NULL,
                lang TEXT DEFAULT 'ru'
            );
        """)
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS lang TEXT DEFAULT 'ru'")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS dice_enabled BOOLEAN DEFAULT TRUE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS barcode_file_id TEXT DEFAULT NULL")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN DEFAULT FALSE")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                shop_id INT REFERENCES shops(id) ON DELETE CASCADE,
                work_date DATE NOT NULL,
                worker_text TEXT,
                PRIMARY KEY (shop_id, work_date)
            );
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS breaks (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                start_time TIMESTAMP WITH TIME ZONE NOT NULL,
                end_time TIMESTAMP WITH TIME ZONE,
                base_tag TEXT DEFAULT '',
                is_delivery BOOLEAN DEFAULT FALSE
            )
        """)
        await conn.execute("""
            ALTER TABLE breaks ADD COLUMN IF NOT EXISTS custom_text TEXT DEFAULT '';
        """)
        await conn.execute("""
            ALTER TABLE breaks ADD COLUMN IF NOT EXISTS is_transfer BOOLEAN DEFAULT FALSE;
        """)

def now_tz() -> datetime:
    return datetime.now(TZ)

# --- USERS & SHOPS ---

async def get_user(user_id: int) -> dict:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def create_user(user_id: int, full_name: str, shop_id: int, lang: str = 'ru'):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, full_name, shop_id, lang) 
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE 
            SET full_name = EXCLUDED.full_name, shop_id = EXCLUDED.shop_id, lang = EXCLUDED.lang
        """, user_id, full_name, shop_id, lang)

async def update_user_lang(user_id: int, lang: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET lang = $1 WHERE user_id = $2", lang, user_id)

async def update_user_name(user_id: int, full_name: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET full_name = $1 WHERE user_id = $2", full_name, user_id)

async def update_user_shop(user_id: int, shop_id: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET shop_id = $1 WHERE user_id = $2", shop_id, user_id)

async def set_barcode(user_id: int, file_id: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET barcode_file_id = $1 WHERE user_id = $2", file_id, user_id)

async def get_barcode(user_id: int) -> str | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT barcode_file_id FROM users WHERE user_id = $1", user_id)
        return row["barcode_file_id"] if row else None

async def toggle_dice(user_id: int) -> bool:
    """Toggles dice_enabled flag and returns the NEW value."""
    async with pool.acquire() as conn:
        new_val = await conn.fetchval(
            "UPDATE users SET dice_enabled = NOT dice_enabled WHERE user_id = $1 RETURNING dice_enabled",
            user_id
        )
        return new_val

async def get_shops() -> list:
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM shops ORDER BY name ASC")

async def get_shop(shop_id: int) -> dict:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM shops WHERE id = $1", shop_id)

async def update_shop_times(shop_id: int, open_time: dtime, close_time: dtime):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE shops SET open_time = $1, close_time = $2 WHERE id = $3", open_time, close_time, shop_id)

async def create_shop(name: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("""
            INSERT INTO shops (name) VALUES ($1) 
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """, name)

async def get_shop_users(shop_id: int) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM users WHERE shop_id = $1 ORDER BY full_name ASC", shop_id)

async def create_offline_user(full_name: str, shop_id: int):
    fake_id = -int(now_tz().timestamp() * 1000)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, full_name, shop_id, lang)
            VALUES ($1, $2, $3, 'ru')
        """, fake_id, full_name, shop_id)

async def get_offline_users(shop_id: int) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM users WHERE shop_id = $1 AND user_id < 0 ORDER BY full_name ASC", shop_id)

async def delete_offline_user(user_id: int, shop_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE user_id = $1 AND shop_id = $2 AND user_id < 0", user_id, shop_id)

async def get_all_users_grouped() -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.name as shop_name, u.full_name, u.user_id
            FROM users u
            LEFT JOIN shops s ON u.shop_id = s.id
            ORDER BY s.name ASC, u.full_name ASC
        """)
        grouped = {}
        for r in rows:
            shop = r["shop_name"] or "Без магазина"
            if shop not in grouped:
                grouped[shop] = []
            grouped[shop].append({"name": r["full_name"], "id": r["user_id"]})
        return grouped

async def get_all_users_for_admin() -> list:
    """Returns all real (non-offline) users with their shop name."""
    async with pool.acquire() as conn:
        return await conn.fetch("""
            SELECT u.user_id, u.full_name, u.is_blocked, s.name as shop_name, s.id as shop_id
            FROM users u
            LEFT JOIN shops s ON u.shop_id = s.id
            WHERE u.user_id > 0
            ORDER BY s.name ASC, u.full_name ASC
        """)

async def block_user(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_blocked = TRUE WHERE user_id = $1", user_id)

async def unblock_user(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_blocked = FALSE WHERE user_id = $1", user_id)

async def get_user_block_status(user_id: int) -> bool:
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT is_blocked FROM users WHERE user_id = $1", user_id)
        return bool(val)

# --- SCHEDULES ---

async def get_weekly_schedule(shop_id: int, start_date: date, end_date: date) -> dict:
    """Returns a dict mapping work_date to worker_text"""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT work_date, worker_text 
            FROM schedules 
            WHERE shop_id = $1 AND work_date >= $2 AND work_date <= $3
        """, shop_id, start_date, end_date)
        return {row["work_date"]: row["worker_text"] for row in rows}

async def set_schedule(shop_id: int, work_date: date, worker_text: str):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO schedules (shop_id, work_date, worker_text) 
            VALUES ($1, $2, $3)
            ON CONFLICT (shop_id, work_date) DO UPDATE 
            SET worker_text = EXCLUDED.worker_text
        """, shop_id, work_date, worker_text)

# --- BREAKS ---

async def start_break(user_id: int) -> tuple[bool, datetime | None]:
    now = now_tz()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM breaks WHERE user_id = $1 AND end_time IS NULL",
            user_id
        )
        if row:
            return False, None

        await conn.execute(
            "INSERT INTO breaks (user_id, start_time) VALUES ($1, $2)",
            user_id, now
        )
        return True, now

async def end_break(user_id: int) -> tuple[bool, int, str, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, start_time FROM breaks WHERE user_id = $1 AND end_time IS NULL",
            user_id
        )
        if not row:
            return False, 0, "", 0
        
        break_id = row["id"]
        start_time = row["start_time"].astimezone(TZ)
        end_time = now_tz()
        
        st_floor = start_time.replace(second=0, microsecond=0)
        et_floor = end_time.replace(second=0, microsecond=0)
        duration_minutes = int((et_floor - st_floor).total_seconds() // 60)
        
        base_tag = ""
        if 11 <= duration_minutes <= 15:
            base_tag = "(Задержка)"
        elif 16 <= duration_minutes <= 20:
            base_tag = "(Обед)"
        elif 21 <= duration_minutes <= 25:
            base_tag = "(Обед плюс задержка)"
            
        await conn.execute(
            "UPDATE breaks SET end_time = $1, base_tag = $2 WHERE id = $3",
            end_time, base_tag, break_id
        )
        return True, break_id, base_tag, duration_minutes

async def add_delivery_by_id(user_id: int, break_id: int) -> tuple[bool, str]:
    now = now_tz()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id FROM breaks 
            WHERE user_id = $1 AND is_delivery = TRUE 
            AND date(start_time AT TIME ZONE 'Europe/Moscow') = date($2 AT TIME ZONE 'Europe/Moscow')
        """, user_id, now)
        if row and row["id"] != break_id:
            return False, "Вы уже использовали Довоз сегодня."
            
        await conn.execute("UPDATE breaks SET is_delivery = TRUE WHERE id = $1", break_id)
        return True, "Довоз успешно добавлен!"

async def set_custom_text(break_id: int, text: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE breaks SET custom_text = $1 WHERE id = $2", text, break_id)

async def set_transfer_by_id(break_id: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE breaks SET is_transfer = TRUE WHERE id = $1", break_id)

async def get_today_breaks(user_id: int) -> list:
    now = now_tz()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, start_time, end_time, base_tag, is_delivery, is_transfer, custom_text 
            FROM breaks 
            WHERE user_id = $1 
            AND date(start_time AT TIME ZONE 'Europe/Moscow') = date($2 AT TIME ZONE 'Europe/Moscow')
            ORDER BY id ASC
        """, user_id, now)
        return rows

async def get_breaks_by_date(user_id: int, target_date: date) -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, start_time, end_time, base_tag, is_delivery, is_transfer, custom_text 
            FROM breaks 
            WHERE user_id = $1 
            AND date(start_time AT TIME ZONE 'Europe/Moscow') = $2
            ORDER BY id ASC
        """, user_id, target_date)
        return rows

async def update_break_time(break_id: int, start_time: datetime, end_time: datetime):
    st_floor = start_time.replace(second=0, microsecond=0)
    et_floor = end_time.replace(second=0, microsecond=0)
    duration_minutes = int((et_floor - st_floor).total_seconds() // 60)
    
    base_tag = ""
    if 11 <= duration_minutes <= 15:
        base_tag = "(Задержка)"
    elif 16 <= duration_minutes <= 20:
        base_tag = "(Обед)"
    elif 21 <= duration_minutes <= 25:
        base_tag = "(Обед плюс задержка)"
        
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE breaks 
            SET start_time = $1, end_time = $2, base_tag = $3
            WHERE id = $4
        """, start_time, end_time, base_tag, break_id)

async def delete_break(break_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM breaks WHERE id = $1", break_id)

async def update_break_tag(break_id: int, new_tag: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE breaks SET base_tag = $1 WHERE id = $2", new_tag, break_id)

async def get_weekly_stats(user_id: int) -> dict:
    now = now_tz()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT start_time, end_time, base_tag, is_delivery, is_transfer
            FROM breaks
            WHERE user_id = $1
            AND start_time >= current_date - interval '7 days'
        """, user_id)
        
        stats = {
            "total_breaks": len(rows),
            "total_duration": 0,
            "delays": 0,
            "lunches": 0,
            "deliveries": 0,
            "transfers": 0
        }
        
        for r in rows:
            if r["end_time"]:
                st_floor = r["start_time"].astimezone(TZ).replace(second=0, microsecond=0)
                et_floor = r["end_time"].astimezone(TZ).replace(second=0, microsecond=0)
                stats["total_duration"] += int((et_floor - st_floor).total_seconds() // 60)
            
            if r["base_tag"] == "(Задержка)":
                stats["delays"] += 1
            elif r["base_tag"] in ("(Обед)", "(Обед плюс задержка)"):
                stats["lunches"] += 1
                
            if r["is_delivery"]:
                stats["deliveries"] += 1
            if r["is_transfer"]:
                stats["transfers"] += 1
                
        return stats

async def get_idle_users() -> list[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, 
                   MAX(start_time) as max_start, 
                   MAX(end_time) as max_end,
                   COUNT(CASE WHEN end_time IS NULL THEN 1 END) as open_breaks
            FROM breaks
            GROUP BY user_id
        """)
        
        idle_users = []
        now = now_tz()
        for r in rows:
            if r["open_breaks"] > 0:
                continue
            
            last_act = r["max_end"] if r["max_end"] else r["max_start"]
            if last_act:
                last_act = last_act.astimezone(TZ)
                diff = (now - last_act).total_seconds()
                if diff >= 2 * 3600:
                    idle_users.append(r["user_id"])
                    
        return idle_users
