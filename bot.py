import os
import datetime
import asyncio
import logging
import csv
import io
import aiosqlite
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ErrorEvent,
)
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)
from aiohttp import web

# ===== SETTINGS =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB = "/data/minsk.db" if Path("/data").exists() else "minsk.db"
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK = f"https://{RENDER_HOST}/webhook" if RENDER_HOST else None
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== DATABASE =====
_db_pool = None

async def get_db():
    global _db_pool
    if _db_pool is None or _db_pool.closed:
        _db_pool = await aiosqlite.connect(DB)
        await _db_pool.execute("PRAGMA journal_mode=WAL")
    return _db_pool

async def init_db():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    db = await get_db()
    await db.execute(
        "CREATE TABLE IF NOT EXISTS rests ("
        "id INTEGER PRIMARY KEY, name TEXT, cuisine TEXT, "
        "address TEXT, lat REAL, lon REAL, phone TEXT, "
        "hours TEXT, avg REAL DEFAULT 0)"
    )
    await db.execute(
        "CREATE TABLE IF NOT EXISTS menu ("
        "id INTEGER PRIMARY KEY, rest_id INTEGER, "
        "cat TEXT, dish TEXT, desc TEXT, price REAL)"
    )
    await db.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "user_id INTEGER PRIMARY KEY, username TEXT, "
        "first_name TEXT, last_seen TEXT, lat REAL, lon REAL)"
    )
    await db.execute(
        "CREATE TABLE IF NOT EXISTS reviews ("
        "id INTEGER PRIMARY KEY, user_id INTEGER, "
        "rest_id INTEGER, rating INTEGER, text TEXT, date TEXT)"
    )
    await db.commit()

# ===== BOT =====
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

pending = {}

def user_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти")],
            [KeyboardButton(text="📍 Рядом", request_location=True)],
            [KeyboardButton(text="⭐ Отзывы")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )

def admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Ресторан")],
            [KeyboardButton(text="🍕 Блюдо")],
            [KeyboardButton(text="📤 Импорт")],
            [KeyboardButton(text="📥 Экспорт")],
            [KeyboardButton(text="💰 Цены")],
            [KeyboardButton(text="📋 Список")],
            [KeyboardButton(text="🗑 Сброс БД")],
            [KeyboardButton(text="🔙 Назад")],
        ],
        resize_keyboard=True,
    )

def dist(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def esc_md(text):
    if not text:
        return ""
    chars = r"_\*\[\]\(\)~`>#+\-=|{ }\.!"
    for ch in chars:
        text = text.replace(ch, "\\" + ch)
    return text

def esc_md_light(text):
    if not text:
        return ""
    return text.replace("*", "\*").replace("_", "\_")

async def save_user(uid, uname, fname, lat=None, lon=None):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT user_id FROM users WHERE user_id = ?", (uid,)
    )
    if row:
        await db.execute(
            "UPDATE users SET username = ?, first_name = ?, "
            "last_seen = ?, lat = COALESCE(?, lat), "
            "lon = COALESCE(?, lon) WHERE user_id = ?",
            (uname, fname, now, lat, lon, uid),
        )
    else:
        await db.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
            (uid, uname, fname, now, lat, lon),
        )
    await db.commit()

# ============================================================
# ERROR HANDLER
# ============================================================
@dp.error()
async def error_handler(event: ErrorEvent):
    logger.exception("Ошибка в боте:")
    if event.update.message:
        try:
            await event.update.message.answer("❌ Произошла ошибка. Попробуйте ещё раз.")
        except Exception:
            pass

# ============================================================
# 1. КОМАНДЫ
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    u = message.from_user
    await save_user(u.id, u.username or "", u.first_name or "")
    text = (
        "🍽️ *Бот ресторанов Минска*\n\n"
        "🔍 *Найти* — поиск ресторана или блюда\n"
        "📍 *Рядом* — ближайшие рестораны\n"
        "⭐ *Отзывы* — оценить ресторан\n\n"
        "Админ: /admin"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=user_kb())

@dp.message(Command("help"))
async def cmd_help_cmd(message: Message):
    text = (
        "📌 *Как пользоваться:*\n\n"
        "1️⃣ Отправь геолокацию\n"
        "2️⃣ Напиши название ресторана или блюда\n"
        "3️⃣ Получи цены и маршрут\n\n"
        "Команды админа: /admin"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=user_kb())

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔧 Админка", reply_markup=admin_kb())

@dp.message(Command("addrest"))
async def cmd_addrest(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        p = message.text.split(maxsplit=1)[1].split("|")
        name = p[0].strip()
        cuisine = p[1].strip()
        addr = p[2].strip()
        lat = float(p[3].strip())
        lon = float(p[4].strip())
        phone = p[5].strip() if len(p) > 5 else ""
        hours = p[6].strip() if len(p) > 6 else ""
        avg = float(p[7]) if len(p) > 7 else 0
        db = await get_db()
        await db.execute(
            "INSERT INTO rests VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, cuisine, addr, lat, lon, phone, hours, avg),
        )
        await db.commit()
        await message.answer(f"✅ Ресторан *{esc_md_light(name)}* добавлен", parse_mode="Markdown")
    except Exception as e:
        logger.error(e)
        await message.answer("❌ Ошибка. Проверь формат.")

@dp.message(Command("add"))
async def cmd_add(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        p = message.text.split(maxsplit=1)[1].split("|")
        rname = p[0].strip()
        cat = p[1].strip()
        dish = p[2].strip()
        desc = p[3].strip()
        price = float(p[4].strip())
        db = await get_db()
        r = await db.execute_fetchall(
            "SELECT id FROM rests WHERE lower(name) = ?", (rname.lower(),)
        )
        if not r:
            await message.answer("❌ Ресторан не найден.")
            return
        await db.execute(
            "INSERT INTO menu VALUES (NULL, ?, ?, ?, ?, ?)",
            (r[0][0], cat, dish, desc, price),
        )
        await db.commit()
        await message.answer(f"✅ {esc_md_light(dish)} добавлено ({price} BYN)", parse_mode="Markdown")
    except Exception as e:
        logger.error(e)
        await message.answer("❌ Ошибка. Формат: /add Рест|Кат|Блюдо|Опис|Цена")

@dp.message(Command("price"))
async def cmd_price(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        p = message.text.split()
        mid = int(p[1])
        new = float(p[2])
        db = await get_db()
        await db.execute("UPDATE menu SET price = ? WHERE id = ?", (new, mid))
        await db.commit()
        await message.answer(f"✅ Цена обновлена: {new} BYN")
    except Exception:
        await message.answer("❌ Формат: /price id цена")

@dp.message(Command("bulk"))
async def cmd_bulk(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        p = message.text.split(maxsplit=1)[1].split("|")
        rname = p[0].strip()
        cat = p[1].strip()
        act = p[2].strip()
        db = await get_db()
        q = "SELECT id, price FROM menu WHERE 1=1"
        params = []
        if rname.lower() != "все":
            r = await db.execute_fetchall(
                "SELECT id FROM rests WHERE lower(name) = ?",
                (rname.lower(),),
            )
            if not r:
                await message.answer("❌ Ресторан не найден.")
                return
            q += " AND rest_id = ?"
            params.append(r[0][0])
        if cat.lower() != "все":
            q += " AND lower(cat) = ?"
            params.append(cat.lower())
        rows = await db.execute_fetchall(q, tuple(params))
        if not rows:
            await message.answer("❌ Нет блюд.")
            return
        upd = 0
        for mid, price in rows:
            if act.startswith("+"):
                new = round(price * (1 + float(act[1:-1]) / 100), 2)
            elif act.startswith("-"):
                new = round(price * (1 - float(act[1:-1]) / 100), 2)
            elif act.startswith("="):
                new = float(act[1:])
            else:
                raise ValueError
            await db.execute(
                "UPDATE menu SET price = ? WHERE id = ?", (new, mid)
            )
            upd += 1
        await db.commit()
        await message.answer(f"✅ Обновлено {upd} блюд")
    except Exception as e:
        logger.error(e)
        await message.answer(
            "❌ Формат: /bulk Рест|Кат|+10%\n"
            "Примеры:\n/bulk все|все|+10%\n/bulk все|Пицца|-5%"
        )

@dp.message(Command("del"))
async def cmd_del(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        p = message.text.split()
        table = p[1]
        mid = int(p[2])
        db = await get_db()
        if table == "rest":
            await db.execute("DELETE FROM rests WHERE id = ?", (mid,))
        else:
            await db.execute("DELETE FROM menu WHERE id = ?", (mid,))
        await db.commit()
        await message.answer(f"🗑️ Удалено id={mid}")
    except Exception:
        await message.answer("❌ Формат: /del rest 5 или /del menu 12")

@dp.message(Command("export"))
async def cmd_export(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await do_export(message)

# ============================================================
# 2. КНОПКИ ПОЛЬЗОВАТЕЛЯ
# ============================================================

@dp.message(F.text == "🔍 Найти")
async def ask_search(message: Message):
    await message.answer("Введи название ресторана или блюда:")

@dp.message(F.text == "⭐ Отзывы")
async def ask_review(message: Message):
    pending[message.from_user.id] = {"step": "search"}
    await message.answer("Введи название ресторана для отзыва:")

@dp.message(F.text == "ℹ️ Помощь")
async def cmd_help_btn(message: Message):
    text = (
        "📌 *Как пользоваться:*\n\n"
        "1️⃣ Отправь геолокацию\n"
        "2️⃣ Напиши название ресторана или блюда\n"
        "3️⃣ Получи цены и маршрут\n\n"
        "Команды админа: /admin"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=user_kb())

# ============================================================
# 3. ЛОКАЦИЯ И ДОКУМЕНТЫ
# ============================================================

@dp.message(F.location)
async def on_location(message: Message):
    uid = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    await save_user(
        uid,
        message.from_user.username or "",
        message.from_user.first_name or "",
        lat,
        lon,
    )
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM rests")
    if not rows:
        return await message.answer("❌ База пуста. Админ должен добавить рестораны.")
    nearby = []
    for r in rows:
        d = dist(lat, lon, r[4], r[5])
        nearby.append((d, r))
    nearby.sort(key=lambda x: x[0])
    text = "📍 *Ближайшие рестораны:*\n\n"
    for d, r in nearby[:5]:
        text += f"🍴 *{esc_md_light(r[1])}* ({esc_md_light(r[2]) or 'нет'})\n"
        text += f"📍 {esc_md_light(r[3])} ({d:.1f} км)\n"
        text += f"🗺️ [Маршрут](https://yandex.by/maps/?rtext={lat},{lon}~{r[4]},{r[5]}&rtt=auto)\n\n"
    await message.answer(
        text, parse_mode="Markdown", disable_web_page_preview=True
    )

@dp.message(F.document)
async def on_doc(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    doc = message.document
    if not doc.file_name.endswith(".csv"):
        return await message.answer("❌ Нужен CSV")
    try:
        f = await bot.download(doc)
        content = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        heads = reader.fieldnames or []
        db = await get_db()
        n = 0
        if "name" in heads:
            for row in reader:
                nme = row.get("name", "").strip()
                if not nme:
                    continue
                await db.execute(
                    "INSERT INTO rests VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        nme,
                        row.get("cuisine", ""),
                        row.get("address", ""),
                        float(row.get("lat", 0)),
                        float(row.get("lon", 0)),
                        row.get("phone", ""),
                        row.get("hours", ""),
                        float(row.get("avg", 0) or 0),
                    ),
                )
                n += 1
        elif "dish" in heads:
            for row in reader:
                rname = row.get("restaurant", "").strip()
                if not rname:
                    continue
                r = await db.execute_fetchall(
                    "SELECT id FROM rests WHERE lower(name) = ?",
                    (rname.lower(),),
                )
                if not r:
                    continue
                await db.execute(
                    "INSERT INTO menu VALUES (NULL, ?, ?, ?, ?, ?)",
                    (
                        r[0][0],
                        row.get("cat", "Разное"),
                        row["dish"],
                        row.get("desc", ""),
                        float(row["price"]),
                    ),
                )
                n += 1
        await db.commit()
        await message.answer(f"✅ Импортировано: {n}")
    except Exception as e:
        logger.error(e)
        await message.answer(f"❌ Ошибка импорта: {e}")

# ============================================================
# 4. CALLBACKS
# ============================================================

@dp.callback_query()
async def on_callback(call):
    data = call.data
    uid = call.from_user.id
    db = await get_db()

    if data.startswith("menu_"):
        rid = int(data.split("_")[1])
        rows = await db.execute_fetchall(
            "SELECT cat, dish, desc, price FROM menu "
            "WHERE rest_id = ? ORDER BY cat, price",
            (rid,),
        )
        rname = await db.execute_fetchall(
            "SELECT name FROM rests WHERE id = ?", (rid,)
        )
        if not rows:
            await call.message.answer("Меню пусто.")
            return
        msg = f"📋 *{esc_md_light(rname[0][0])}*\n\n"
        cur = ""
        for row in rows:
            if row[0] != cur:
                msg += f"*{esc_md_light(row[0])}*\n"
                cur = row[0]
            msg += f"• {esc_md_light(row[1])} — {row[3]} BYN"
            if row[2]:
                msg += f" ({esc_md_light(row[2])})"
            msg += "\n"
        await call.message.answer(msg, parse_mode="Markdown")
        return

    if data.startswith("route_"):
        rid = int(data.split("_")[1])
        r = await db.execute_fetchall(
            "SELECT lat, lon, name FROM rests WHERE id = ?", (rid,)
        )
        if not r:
            return
        loc = await db.execute_fetchall(
            "SELECT lat, lon FROM users WHERE user_id = ?", (uid,)
        )
        if loc and loc[0][0]:
            url = (
                f"https://yandex.by/maps/"
                f"?rtext={loc[0][0]},{loc[0][1]}~{r[0][0]},{r[0][1]}&rtt=auto"
            )
        else:
            url = (
                f"https://yandex.by/maps/"
                f"?mode=search&text={r[0][0]},{r[0][1]}"
            )
        await call.message.answer(
            f"🗺️ Маршрут до *{esc_md_light(r[0][2])}*:\n{url}",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    if data.startswith("rev_"):
        rid = int(data.split("_")[1])
        pending[uid] = {"step": "rate", "rid": rid}
        await call.message.answer("Введи оценку от 1 до 5:")
        return

# ============================================================
# 5. GENERIC TEXT HANDLER — ПОСЛЕДНИМ
# ============================================================

@dp.message()
async def on_text(message: Message):
    if not message.text:
        return
    uid = message.from_user.id
    text = message.text.strip()

    # --- REVIEW FLOW ---
    if uid in pending:
        p = pending[uid]
        step = p.get("step")

        if step == "search":
            db = await get_db()
            r = await db.execute_fetchall(
                "SELECT id, name FROM rests WHERE lower(name) LIKE ? LIMIT 1",
                (f"%{text.lower()}%",),
            )
            if r:
                rid, rname = r[0]
                pending[uid] = {"step": "rate", "rid": rid}
                await message.answer(f"Оцени *{esc_md_light(rname)}*:", parse_mode="Markdown")
            else:
                del pending[uid]
                await message.answer(f"❌ Ресторан «{esc_md_light(text)}» не найден. Попробуй ещё раз или напиши название для поиска.")
            return

        if step == "rate":
            try:
                rating = int(text)
                if not 1 <= rating <= 5:
                    raise ValueError
                p["rating"] = rating
                p["step"] = "text"
                await message.answer("Напиши текст (или '-'):" )
            except ValueError:
                await message.answer("Введи число от 1 до 5:")
            return

        if step == "text":
            txt = text if text != "-" else ""
            db = await get_db()
            await db.execute(
                "INSERT INTO reviews VALUES (NULL, ?, ?, ?, ?, ?)",
                (
                    uid,
                    p["rid"],
                    p["rating"],
                    txt,
                    datetime.datetime.now().isoformat(),
                ),
            )
            await db.commit()
            del pending[uid]
            await message.answer("✅ Отзыв сохранён!")
            return

    # --- ADMIN BUTTONS ---
    if uid == ADMIN_ID:
        if text == "➕ Ресторан":
            return await message.answer(
                "Формат:\n`/addrest Название|Кухня|Адрес|lat|lon|Телефон|Часы|Средний чек`",
                parse_mode="Markdown",
            )
        if text == "🍕 Блюдо":
            return await message.answer(
                "Формат:\n`/add Ресторан|Категория|Блюдо|Описание|Цена`",
                parse_mode="Markdown",
            )
        if text == "📤 Импорт":
            return await message.answer("Отправь CSV файл.")
        if text == "📥 Экспорт":
            return await do_export(message)
        if text == "💰 Цены":
            return await message.answer(
                "Формат:\n`/bulk Ресторан|Категория|+10%`\n"
                "Или: `/price 5 18.50`",
                parse_mode="Markdown",
            )
        if text == "📋 Список":
            return await do_list(message)
        if text == "🗑 Сброс БД":
            return await do_reset(message)
        if text == "🔙 Назад":
            return await message.answer("Меню", reply_markup=user_kb())

    # --- SEARCH ---
    db = await get_db()
    t = f"%{text.lower()}%"
    rests = await db.execute_fetchall(
        "SELECT * FROM rests WHERE lower(name) LIKE ? LIMIT 5", (t,)
    )
    dishes = await db.execute_fetchall(
        "SELECT m.*, r.name FROM menu m "
        "JOIN rests r ON m.rest_id = r.id "
        "WHERE lower(m.dish) LIKE ? ORDER BY m.price LIMIT 5",
        (t,),
    )

    if rests:
        for r in rests:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📋 Меню", callback_data=f"menu_{r[0]}"
                        ),
                        InlineKeyboardButton(
                            text="⭐ Отзыв", callback_data=f"rev_{r[0]}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="🗺️ Маршрут",
                            callback_data=f"route_{r[0]}",
                        )
                    ],
                ]
            )
            msg = f"🍴 *{esc_md_light(r[1])}*\n🍳 {esc_md_light(r[2]) or '-'}\n📍 {esc_md_light(r[3])}\n"
            if r[6]:
                msg += f"📞 {esc_md_light(r[6])}\n"
            if r[7]:
                msg += f"🕐 {esc_md_light(r[7])}\n"
            if r[8]:
                msg += f"💰 Средний чек: {r[8]} BYN\n"
            msg += f"🗺️ [На карте](https://yandex.by/maps/?mode=search&text={r[4]},{r[5]})"
            await message.answer(
                msg, parse_mode="Markdown", reply_markup=kb
            )
        return

    if dishes:
        msg = f"🔍 *Блюда по запросу «{esc_md_light(text)}»:*\n\n"
        for d in dishes:
            msg += f"• *{esc_md_light(d[3])}* — {d[5]} BYN\n"
            msg += f"  Ресторан: {esc_md_light(d[6])}\n"
            if d[4]:
                msg += f"  ({esc_md_light(d[4])})\n"
            msg += "\n"
        await message.answer(msg, parse_mode="Markdown")
        return

    await message.answer(f"❌ Ничего не найдено по «{esc_md_light(text)}»", parse_mode="Markdown")

# ===== ADMIN HELPERS =====

async def do_list(message: Message):
    db = await get_db()
    rests = await db.execute_fetchall(
        "SELECT id, name, cuisine FROM rests ORDER BY id DESC LIMIT 20"
    )
    if not rests:
        return await message.answer("База пуста.")
    msg = "📋 *Рестораны:*\n"
    for r in rests:
        msg += f"{r[0]}. {esc_md_light(r[1])} ({esc_md_light(r[2])})\n"
    await message.answer(msg, parse_mode="Markdown")

async def do_reset(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        if Path(DB).exists():
            Path(DB).unlink()
        global _db_pool
        if _db_pool:
            await _db_pool.close()
            _db_pool = None
        await init_db()
        await message.answer("✅ База сброшена!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

async def do_export(message: Message):
    try:
        db = await get_db()
        rests = await db.execute_fetchall("SELECT * FROM rests")
        menu = await db.execute_fetchall(
            "SELECT m.*, r.name FROM menu m JOIN rests r ON m.rest_id = r.id"
        )

        o1 = io.StringIO()
        w1 = csv.writer(o1)
        w1.writerow(["id", "name", "cuisine", "address", "lat", "lon", "phone", "hours", "avg"])
        for r in rests:
            w1.writerow(r)
        o1.seek(0)

        o2 = io.StringIO()
        w2 = csv.writer(o2)
        w2.writerow(["id", "rest_id", "cat", "dish", "desc", "price", "restaurant"])
        for m in menu:
            w2.writerow(m)
        o2.seek(0)

        await message.answer_document(
            document=BufferedInputFile(o1.getvalue().encode("utf-8-sig"), filename="rests.csv")
        )
        await message.answer_document(
            document=BufferedInputFile(o2.getvalue().encode("utf-8-sig"), filename="menu.csv")
        )
        logger.info(f"Экспорт выполнен админом {message.from_user.id}")
    except Exception as e:
        logger.exception("Ошибка экспорта:")
        await message.answer(f"❌ Ошибка экспорта: {e}")

# ===== STARTUP =====
async def on_startup(bot: Bot):
    await init_db()
    if WEBHOOK:
        await bot.set_webhook(WEBHOOK)

async def on_shutdown(bot: Bot):
    if WEBHOOK:
        await bot.delete_webhook()
    global _db_pool
    if _db_pool:
        await _db_pool.close()

dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

async def run_polling():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

def main():
    if WEBHOOK:
        app = web.Application()
        app.router.add_get("/health", lambda r: web.Response(text="OK"))
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(
            app, path="/webhook"
        )
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        asyncio.run(run_polling())

if __name__ == "__main__":
    main()
