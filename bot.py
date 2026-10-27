import os
import asyncio
import sqlite3
import random
from datetime import date
from contextlib import closing

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip()  # e.g. @yourchannel; leave blank to disable
DB_PATH = os.getenv("DB_PATH", "hyper_gap.db")
REFERRAL_REWARD = 3
DAILY_REWARD = 1.5
VIP_COST = 100
MAX_MESSAGE_LENGTH = 200

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add it as an environment secret; never put it in public code.")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- Database ---
def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

with closing(connect_db()) as db:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        age INTEGER NOT NULL DEFAULT 18,
        gender TEXT NOT NULL DEFAULT 'نامشخص',
        city TEXT NOT NULL DEFAULT 'نامشخص',
        coins REAL NOT NULL DEFAULT 0,
        referred_by INTEGER,
        referral_paid INTEGER NOT NULL DEFAULT 0,
        last_daily TEXT,
        vip_until TEXT,
        joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        banned INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS blocks (
        blocker_id INTEGER NOT NULL,
        blocked_id INTEGER NOT NULL,
        PRIMARY KEY (blocker_id, blocked_id)
    );
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        reported_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        method TEXT NOT NULL,
        destination TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER NOT NULL,
        favorite_id INTEGER NOT NULL,
        PRIMARY KEY (user_id, favorite_id)
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    db.commit()

class Profile(StatesGroup):
    name = State()
    age = State()
    gender = State()
    city = State()

class Suggestion(StatesGroup):
    text = State()

class ReportState(StatesGroup):
    reason = State()

class WithdrawState(StatesGroup):
    amount = State()
    method = State()
    destination = State()

class AdminAdjust(StatesGroup):
    user_id = State()
    amount = State()

class AdminBroadcast(StatesGroup):
    text = State()

# Current chat sessions and waiting queue are in memory. If the bot restarts, users can reconnect.
waiting = []
partner_of = {}


def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎭 چت ناشناس"), KeyboardButton(text="⏭ نفر بعدی")],
        [KeyboardButton(text="🛑 پایان چت"), KeyboardButton(text="👤 پروفایل")],
        [KeyboardButton(text="🔎 جستجوی کاربران"), KeyboardButton(text="⭐ علاقه‌مندی‌ها")],
        [KeyboardButton(text="🪙 کیف پول"), KeyboardButton(text="🎁 پاداش روزانه")],
        [KeyboardButton(text="💎 VIP"), KeyboardButton(text="💸 درخواست برداشت")],
        [KeyboardButton(text="🔗 لینک دعوت"), KeyboardButton(text="🚫 بلاک کاربر")],
        [KeyboardButton(text="⚠️ گزارش کاربر"), KeyboardButton(text="💬 پیشنهادات")],
        [KeyboardButton(text="📖 راهنما")],
    ], resize_keyboard=True)


def db_user(uid):
    with closing(connect_db()) as db:
        return db.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def ensure_user(uid, name=""):
    with closing(connect_db()) as db:
        db.execute("INSERT OR IGNORE INTO users(user_id,name) VALUES(?,?)", (uid, name or "کاربر"))
        db.commit()


def add_coins(uid, amount, reason):
    with closing(connect_db()) as db:
        db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, uid))
        db.execute("INSERT INTO transactions(user_id,amount,reason) VALUES(?,?,?)", (uid, amount, reason))
        db.commit()


def is_blocked(a, b):
    with closing(connect_db()) as db:
        return db.execute("SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)", (a,b,b,a)).fetchone() is not None


async def check_subscription(user_id: int) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return False


async def subscribed_or_prompt(message: Message) -> bool:
    if await check_subscription(message.from_user.id):
        return True
    await message.answer(
        f"برای استفاده از ربات، اول عضو کانال شو: {REQUIRED_CHANNEL}\nبعد از عضویت /start را بزن.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 رفتن به کانال", url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")],
            [InlineKeyboardButton(text="✅ بررسی عضویت", callback_data="check_sub")],
        ])
    )
    return False


async def end_chat(uid, notify=True):
    partner = partner_of.pop(uid, None)
    if uid in waiting:
        waiting.remove(uid)
    if partner is not None:
        partner_of.pop(partner, None)
        if notify:
            try:
                await bot.send_message(partner, "🛑 گفت‌وگو پایان یافت. برای یافتن نفر جدید، «چت ناشناس» را بزن.", reply_markup=main_menu())
            except Exception:
                pass
    return partner


async def find_partner(message: Message):
    uid = message.from_user.id
    await end_chat(uid, notify=False)
    candidates = [x for x in waiting if x != uid and x not in partner_of and not is_blocked(uid, x)]
    random.shuffle(candidates)
    partner = candidates[0] if candidates else None
    if partner:
        waiting.remove(partner)
        partner_of[uid] = partner
        partner_of[partner] = uid
        await message.answer("✅ یک نفر پیدا شد! سلام کن 😊", reply_markup=main_menu())
        try:
            await bot.send_message(partner, "✅ یک نفر پیدا شد! سلام کن 😊", reply_markup=main_menu())
        except Exception:
            await end_chat(uid, notify=False)
            await message.answer("ارتباط برقرار نشد. دوباره تلاش کن.")
    else:
        if uid not in waiting:
            waiting.append(uid)
        await message.answer("🔎 در جستجوی نفر هستی... وقتی کسی پیدا شد وصل می‌شوی. برای لغو «پایان چت» را بزن.", reply_markup=main_menu())


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not await subscribed_or_prompt(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    referrer = None
    if len(parts) == 2 and parts[1].startswith("ref"):
        try:
            referrer = int(parts[1][3:])
        except ValueError:
            referrer = None
    existing = db_user(uid)
    ensure_user(uid, message.from_user.full_name)
    if not existing and referrer and referrer != uid and db_user(referrer):
        with closing(connect_db()) as db:
            db.execute("UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL", (referrer, uid))
            db.commit()
    row = db_user(uid)
    if row and row["name"] and row["city"] != "نامشخص":
        await message.answer(f"خوش آمدی {row['name']} ❤️\nاز منوی پایین انتخاب کن.", reply_markup=main_menu())
    else:
        await state.set_state(Profile.name)
        await message.answer("به Hyper Gap خوش آمدی ❤️\nنام نمایشی خود را بنویس:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="لغو")]], resize_keyboard=True))


@dp.message(F.text == "لغو")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("لغو شد.", reply_markup=main_menu())

@dp.message(Profile.name)
async def profile_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 40:
        await message.answer("نام باید بین ۱ تا ۴۰ حرف باشد.")
        return
    await state.update_data(name=name)
    await state.set_state(Profile.age)
    await message.answer("سن خود را با عدد بنویس (۱۳ تا ۹۹).")

@dp.message(Profile.age)
async def profile_age(message: Message, state: FSMContext):
    try:
        age = int((message.text or "").strip())
        if not 13 <= age <= 99: raise ValueError
    except ValueError:
        await message.answer("سن باید عددی بین ۱۳ تا ۹۹ باشد.")
        return
    await state.update_data(age=age)
    await state.set_state(Profile.gender)
    await message.answer("جنسیت را انتخاب کن:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="مرد"), KeyboardButton(text="زن")],[KeyboardButton(text="ترجیح می‌دهم نگویم")]], resize_keyboard=True))

@dp.message(Profile.gender)
async def profile_gender(message: Message, state: FSMContext):
    gender = (message.text or "").strip()
    if gender not in ("مرد", "زن", "ترجیح می‌دهم نگویم"):
        await message.answer("یکی از گزینه‌های نمایش‌داده‌شده را انتخاب کن.")
        return
    await state.update_data(gender=gender)
    await state.set_state(Profile.city)
    await message.answer("نام شهر یا ولایت خود را بنویس.")

@dp.message(Profile.city)
async def profile_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if not city or len(city) > 60:
        await message.answer("نام شهر/ولایت معتبر بنویس.")
        return
    data = await state.get_data()
    uid = message.from_user.id
    with closing(connect_db()) as db:
        db.execute("UPDATE users SET name=?,age=?,gender=?,city=? WHERE user_id=?", (data["name"], data["age"], data["gender"], city, uid))
        ref = db.execute("SELECT referred_by,referral_paid FROM users WHERE user_id=?", (uid,)).fetchone()
        if ref and ref["referred_by"] and not ref["referral_paid"]:
            db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (REFERRAL_REWARD, ref["referred_by"]))
            db.execute("INSERT INTO transactions(user_id,amount,reason) VALUES(?,?,?)", (ref["referred_by"], REFERRAL_REWARD, "دعوت دوست"))
            db.execute("UPDATE users SET referral_paid=1 WHERE user_id=?", (uid,))
        db.commit()
    await state.clear()
    await message.answer("☑️ پروفایل ثبت شد! به Hyper Gap خوش آمدی ❤️", reply_markup=main_menu())


@dp.message(F.text == "👤 پروفایل")
async def profile_show(message: Message):
    row = db_user(message.from_user.id)
    if not row:
        await message.answer("/start را بزن."); return
    vip = row["vip_until"] or "ندارد"
    await message.answer(f"👤 پروفایل\nنام: {row['name']}\nسن: {row['age']}\nجنسیت: {row['gender']}\nشهر: {row['city']}\n💎 VIP تا: {vip}", reply_markup=main_menu())

@dp.message(F.text == "🎭 چت ناشناس")
async def chat_start(message: Message):
    if not await subscribed_or_prompt(message): return
    row = db_user(message.from_user.id)
    if not row or row["city"] == "نامشخص":
        await message.answer("اول با /start پروفایل خود را کامل کن."); return
    if message.from_user.id in partner_of:
        await message.answer("همین حالا در چت هستی. برای پایان، «پایان چت» را بزن."); return
    await find_partner(message)

@dp.message(F.text.in_({"⏭ نفر بعدی", "/next"}))
async def next_person(message: Message):
    if message.from_user.id not in partner_of and message.from_user.id not in waiting:
        await message.answer("هنوز در چت نیستی. «چت ناشناس» را بزن."); return
    await end_chat(message.from_user.id)
    await find_partner(message)

@dp.message(F.text.in_({"🛑 پایان چت", "/stop"}))
async def stop_chat(message: Message):
    await end_chat(message.from_user.id)
    await message.answer("چت پایان یافت.", reply_markup=main_menu())

@dp.message(F.text == "🪙 کیف پول")
async def wallet(message: Message):
    row = db_user(message.from_user.id)
    if not row: await message.answer("/start را بزن."); return
    with closing(connect_db()) as db:
        tx = db.execute("SELECT amount,reason,created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 5", (message.from_user.id,)).fetchall()
    history = "\n".join(f"{x['amount']:+g} — {x['reason']} ({x['created_at']})" for x in tx) or "هنوز تراکنشی نداری."
    await message.answer(f"🪙 موجودی: {row['coins']:g} سکه\n\nآخرین تراکنش‌ها:\n{history}\n\nنرخ نمایشی موردنظر: هر سکه ۰٫۰۵ دالر؛ این ربات فعلاً پرداخت واقعی انجام نمی‌دهد.")

@dp.message(F.text == "🎁 پاداش روزانه")
async def daily(message: Message):
    uid = message.from_user.id
    today = date.today().isoformat()
    with closing(connect_db()) as db:
        row = db.execute("SELECT last_daily FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row: await message.answer("/start را بزن."); return
        if row["last_daily"] == today:
            await message.answer("پاداش امروز را گرفته‌ای؛ فردا دوباره بیا."); return
        db.execute("UPDATE users SET coins=coins+?,last_daily=? WHERE user_id=?", (DAILY_REWARD,today,uid))
        db.execute("INSERT INTO transactions(user_id,amount,reason) VALUES(?,?,?)", (uid,DAILY_REWARD,"پاداش روزانه"))
        db.commit()
    await message.answer(f"🎁 {DAILY_REWARD:g} سکه گرفتی! فردا دوباره بیا.")

@dp.message(F.text == "🔗 لینک دعوت")
async def referral(message: Message):
    me = await bot.get_me()
    await message.answer(f"🔗 لینک دعوت اختصاصی تو:\nhttps://t.me/{me.username}?start=ref{message.from_user.id}\n\nبرای هر دوست جدیدی که پروفایلش را کامل کند، {REFERRAL_REWARD} سکه می‌گیری. دعوت‌های تکراری پاداش ندارند.")

@dp.message(F.text == "💎 VIP")
async def vip(message: Message):
    row = db_user(message.from_user.id)
    if not row: await message.answer("/start را بزن."); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"خرید VIP یک‌ماهه — {VIP_COST} سکه", callback_data="buy_vip")]])
    await message.answer(f"💎 VIP\nقیمت: {VIP_COST} سکه برای ۳۰ روز.\nویژگی‌های VIP در این نسخه پایه محدود است و پرداخت با پول واقعی فعال نیست.", reply_markup=kb)

@dp.callback_query(F.data == "buy_vip")
async def buy_vip(call: CallbackQuery):
    uid = call.from_user.id
    with closing(connect_db()) as db:
        row = db.execute("SELECT coins,vip_until FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row or row["coins"] < VIP_COST:
            await call.answer("سکه کافی نداری.", show_alert=True); return
        # Extend VIP from today; store ISO date.
        from datetime import datetime, timedelta
        base = date.today()
        if row["vip_until"]:
            try:
                old = date.fromisoformat(row["vip_until"][:10])
                if old > base: base = old
            except ValueError: pass
        until = (base + __import__('datetime').timedelta(days=30)).isoformat()
        db.execute("UPDATE users SET coins=coins-?,vip_until=? WHERE user_id=?", (VIP_COST,until,uid))
        db.execute("INSERT INTO transactions(user_id,amount,reason) VALUES(?,?,?)", (uid,-VIP_COST,"خرید VIP"))
        db.commit()
    await call.answer("VIP فعال شد!")
    await call.message.answer(f"💎 VIP تو تا {until} فعال است.")

@dp.message(F.text == "🔎 جستجوی کاربران")
async def search_users(message: Message):
    with closing(connect_db()) as db:
        rows = db.execute("SELECT user_id,name,age,gender,city FROM users WHERE user_id!=? AND banned=0 ORDER BY joined_at DESC LIMIT 10", (message.from_user.id,)).fetchall()
    if not rows:
        await message.answer("هنوز کاربری برای نمایش نیست."); return
    for r in rows:
        if is_blocked(message.from_user.id, r["user_id"]): continue
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ افزودن به علاقه‌مندی", callback_data=f"fav:{r['user_id']}")]])
        await message.answer(f"👤 {r['name']}\nسن: {r['age']} | جنسیت: {r['gender']}\nشهر: {r['city']}\nشناسه: {r['user_id']}", reply_markup=kb)

@dp.callback_query(F.data.startswith("fav:"))
async def favorite(call: CallbackQuery):
    try: target = int(call.data.split(":",1)[1])
    except ValueError: await call.answer("شناسه نامعتبر است."); return
    if target == call.from_user.id: await call.answer("خودت را نمی‌توانی اضافه کنی."); return
    with closing(connect_db()) as db:
        db.execute("INSERT OR IGNORE INTO favorites(user_id,favorite_id) VALUES(?,?)", (call.from_user.id,target)); db.commit()
    await call.answer("به علاقه‌مندی‌ها اضافه شد.")

@dp.message(F.text == "⭐ علاقه‌مندی‌ها")
async def favorites(message: Message):
    with closing(connect_db()) as db:
        rows = db.execute("SELECT u.name,u.age,u.city,u.user_id FROM favorites f JOIN users u ON u.user_id=f.favorite_id WHERE f.user_id=?", (message.from_user.id,)).fetchall()
    if not rows: await message.answer("لیست علاقه‌مندی‌ها خالی است."); return
    await message.answer("⭐ علاقه‌مندی‌ها:\n" + "\n".join(f"{r['name']} — {r['age']} سال، {r['city']} (ID: {r['user_id']})" for r in rows))

@dp.message(F.text == "🚫 بلاک کاربر")
async def block_help(message: Message):
    await message.answer("برای بلاک، شناسه عددی کاربر را بفرست:")
    await state_block_start(message)

# A small per-user action prompt; next message is interpreted as target only when prefixed.
block_waiting = set()
async def state_block_start(message):
    block_waiting.add(message.from_user.id)

@dp.message(F.text == "⚠️ گزارش کاربر")
async def report_help(message: Message, state: FSMContext):
    await state.set_state(ReportState.reason)
    await message.answer("شناسه کاربر و دلیل گزارش را در یک پیام بنویس، مثال: 123456 اسپم می‌فرستد")

@dp.message(ReportState.reason)
async def report_submit(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    bits = raw.split(maxsplit=1)
    if len(bits) < 2 or not bits[0].isdigit():
        await message.answer("فرمت درست: شناسه دلیل گزارش"); return
    target, reason = int(bits[0]), bits[1][:300]
    with closing(connect_db()) as db:
        db.execute("INSERT INTO reports(reporter_id,reported_id,reason) VALUES(?,?,?)", (message.from_user.id,target,reason)); db.commit()
    await state.clear()
    await message.answer("گزارش ثبت شد. ممنون که کمک می‌کنی.")
    if ADMIN_ID:
        try: await bot.send_message(ADMIN_ID, f"⚠️ گزارش جدید\nگزارش‌دهنده: {message.from_user.id}\nکاربر: {target}\nدلیل: {reason}")
        except Exception: pass

@dp.message(F.text == "💬 پیشنهادات")
async def suggestion_start(message: Message, state: FSMContext):
    await state.set_state(Suggestion.text)
    await message.answer("پیشنهاد یا نظر خود را بنویس:")

@dp.message(Suggestion.text)
async def suggestion_send(message: Message, state: FSMContext):
    text = (message.text or "").strip()[:1000]
    await state.clear()
    await message.answer("✅ پیشنهادت ثبت شد، تشکر!")
    if ADMIN_ID:
        try: await bot.send_message(ADMIN_ID, f"💬 پیشنهاد از {message.from_user.id}:\n{text}")
        except Exception: pass

@dp.message(F.text == "💸 درخواست برداشت")
async def withdraw_start(message: Message, state: FSMContext):
    await state.set_state(WithdrawState.amount)
    await message.answer("مقدار سکه برای برداشت را بنویس (عدد مثبت). درخواست برای بررسی ادمین ثبت می‌شود؛ پرداخت خودکار فعال نیست.")

@dp.message(WithdrawState.amount)
async def withdraw_amount(message: Message, state: FSMContext):
    try: amount = float((message.text or "").strip())
    except ValueError: await message.answer("یک عدد معتبر بنویس."); return
    row = db_user(message.from_user.id)
    if amount <= 0 or not row or row["coins"] < amount:
        await message.answer("مقدار نامعتبر است یا سکه کافی نداری."); return
    await state.update_data(amount=amount)
    await state.set_state(WithdrawState.method)
    await message.answer("روش دریافت را بنویس (مثلاً Momo یا حواله).")

@dp.message(WithdrawState.method)
async def withdraw_method(message: Message, state: FSMContext):
    method = (message.text or "").strip()[:50]
    if not method: await message.answer("روش دریافت را بنویس."); return
    await state.update_data(method=method)
    await state.set_state(WithdrawState.destination)
    await message.answer("شماره/شناسه دریافت را بنویس. اطلاعات حساس بانکی را فقط اگر لازم است ارسال کن.")

@dp.message(WithdrawState.destination)
async def withdraw_destination(message: Message, state: FSMContext):
    dest = (message.text or "").strip()[:150]
    data = await state.get_data()
    uid = message.from_user.id
    with closing(connect_db()) as db:
        # Reserve coins by deducting at request creation; admin may refund if rejected.
        row = db.execute("SELECT coins FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row or row["coins"] < data["amount"]:
            await state.clear(); await message.answer("سکه کافی نیست؛ درخواست ثبت نشد."); return
        db.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (data["amount"],uid))
        db.execute("INSERT INTO transactions(user_id,amount,reason) VALUES(?,?,?)", (uid,-data["amount"],"رزرو برداشت"))
        cur = db.execute("INSERT INTO withdrawals(user_id,amount,method,destination) VALUES(?,?,?,?)", (uid,data["amount"],data["method"],dest))
        wid = cur.lastrowid
        db.commit()
    await state.clear()
    await message.answer(f"درخواست برداشت #{wid} ثبت شد و منتظر بررسی ادمین است. پرداخت تضمین یا خودکار نیست.", reply_markup=main_menu())
    if ADMIN_ID:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ تأیید", callback_data=f"wdok:{wid}"),InlineKeyboardButton(text="❌ رد و برگشت سکه", callback_data=f"wdno:{wid}")]])
        try: await bot.send_message(ADMIN_ID, f"💸 درخواست برداشت #{wid}\nکاربر: {uid}\nمقدار: {data['amount']} سکه\nروش: {data['method']}\nشناسه: {dest}", reply_markup=kb)
        except Exception: pass

@dp.callback_query(F.data.startswith("wdok:") | F.data.startswith("wdno:"))
async def withdrawal_review(call: CallbackQuery):
    if not ADMIN_ID or call.from_user.id != ADMIN_ID:
        await call.answer("فقط ادمین.", show_alert=True); return
    action, sid = call.data.split(":",1)
    wid = int(sid)
    with closing(connect_db()) as db:
        row = db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
        if not row or row["status"] != "pending": await call.answer("این درخواست قبلاً بررسی شده.", show_alert=True); return
        if action == "wdok":
            db.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
            msg = "درخواست برداشتت توسط ادمین تأیید شد؛ هماهنگی پرداخت جداگانه انجام می‌شود."
        else:
            db.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
            db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (row["amount"],row["user_id"]))
            db.execute("INSERT INTO transactions(user_id,amount,reason) VALUES(?,?,?)", (row["user_id"],row["amount"],"برگشت سکه برداشت ردشده"))
            msg = "درخواست برداشت رد شد و سکه‌ها به کیف پولت برگشت."
        db.commit()
    await call.answer("ثبت شد.")
    await call.message.edit_reply_markup(reply_markup=None)
    try: await bot.send_message(row["user_id"], msg)
    except Exception: pass

@dp.message(F.text == "📖 راهنما")
async def help_msg(message: Message):
    await message.answer("📖 راهنمای Hyper Gap\n• چت ناشناس: وصل‌شدن به کاربر دیگر\n• نفر بعدی/پایان چت: مدیریت گفت‌وگو\n• پاداش روزانه: ۱٫۵ سکه در روز\n• دعوت دوستان: ۳ سکه پس از تکمیل پروفایل دوست\n• گزارش و بلاک برای ایمنی\n• برداشت فقط درخواست است و پرداخت واقعی باید جداگانه بررسی شود.\n• لطفاً اطلاعات خصوصی، رمز و کد تأیید را با دیگران شریک نکن.", reply_markup=main_menu())

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery):
    if await check_subscription(call.from_user.id):
        await call.answer("عضویت تأیید شد!")
        await call.message.answer("حالا /start را بزن.", reply_markup=main_menu())
    else:
        await call.answer("هنوز عضویت تأیید نشده است.", show_alert=True)

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("این بخش فقط برای ادمین است."); return
    with closing(connect_db()) as db:
        users = db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        pending = db.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone()["n"]
        reports = db.execute("SELECT COUNT(*) n FROM reports").fetchone()["n"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن/کاستن سکه", callback_data="admin_adjust")],
        [InlineKeyboardButton(text="📊 آمار", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📣 پیام همگانی", callback_data="admin_broadcast")],
    ])
    await message.answer(f"🛠 پنل ادمین\nکاربران: {users}\nبرداشت‌های منتظر: {pending}\nگزارش‌ها: {reports}", reply_markup=kb)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if not ADMIN_ID or call.from_user.id != ADMIN_ID: await call.answer("فقط ادمین.", show_alert=True); return
    with closing(connect_db()) as db:
        users = db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        coins = db.execute("SELECT COALESCE(SUM(coins),0) n FROM users").fetchone()["n"]
        reports = db.execute("SELECT COUNT(*) n FROM reports").fetchone()["n"]
        withdrawals = db.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone()["n"]
    await call.answer()
    await call.message.answer(f"📊 آمار\nکاربران: {users}\nمجموع سکه‌ها: {coins:g}\nگزارش‌ها: {reports}\nبرداشت منتظر: {withdrawals}")

@dp.callback_query(F.data == "admin_adjust")
async def admin_adjust_start(call: CallbackQuery, state: FSMContext):
    if not ADMIN_ID or call.from_user.id != ADMIN_ID: await call.answer("فقط ادمین.", show_alert=True); return
    await state.set_state(AdminAdjust.user_id)
    await call.answer()
    await call.message.answer("شناسه عددی کاربر را بفرست:")

@dp.message(AdminAdjust.user_id)
async def admin_adjust_uid(message: Message, state: FSMContext):
    if not ADMIN_ID or message.from_user.id != ADMIN_ID: await state.clear(); return
    raw = (message.text or "").strip()
    if not raw.isdigit(): await message.answer("شناسه باید عدد باشد."); return
    await state.update_data(target=int(raw))
    await state.set_state(AdminAdjust.amount)
    await message.answer("مقدار سکه را بفرست؛ برای کم‌کردن عدد منفی بنویس. مثال: 10 یا -5")

@dp.message(AdminAdjust.amount)
async def admin_adjust_amount(message: Message, state: FSMContext):
    if not ADMIN_ID or message.from_user.id != ADMIN_ID: await state.clear(); return
    try: amount = float((message.text or "").strip())
    except ValueError: await message.answer("عدد معتبر بنویس."); return
    data = await state.get_data(); target = data["target"]
    with closing(connect_db()) as db:
        row = db.execute("SELECT coins FROM users WHERE user_id=?", (target,)).fetchone()
        if not row or row["coins"] + amount < 0:
            await message.answer("کاربر پیدا نشد یا موجودی برای این تغییر کافی نیست."); return
        db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount,target))
        db.execute("INSERT INTO transactions(user_id,amount,reason) VALUES(?,?,?)", (target,amount,"تغییر موجودی توسط ادمین"))
        db.commit()
    await state.clear()
    await message.answer("موجودی تغییر کرد.")
    try: await bot.send_message(target, f"🪙 تغییر موجودی توسط ادمین: {amount:+g} سکه")
    except Exception: pass

@dp.callback_query(F.data == "admin_broadcast")
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    if not ADMIN_ID or call.from_user.id != ADMIN_ID: await call.answer("فقط ادمین.", show_alert=True); return
    await state.set_state(AdminBroadcast.text)
    await call.answer()
    await call.message.answer("متن پیام همگانی را بفرست. برای لغو /cancel بفرست.")

@dp.message(AdminBroadcast.text)
async def broadcast_send(message: Message, state: FSMContext):
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await state.clear(); return
    body = (message.text or "").strip()
    if not body:
        await message.answer("متن پیام خالی است."); return
    with closing(connect_db()) as db:
        ids = [r["user_id"] for r in db.execute("SELECT user_id FROM users WHERE banned=0").fetchall()]
    await state.clear()
    sent = 0
    for uid in ids:
        try:
            await bot.send_message(uid, body)
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    await message.answer(f"پیام همگانی تمام شد. ارسال موفق: {sent} از {len(ids)}")

@dp.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await state.clear(); await message.answer("لغو شد.", reply_markup=main_menu())

# Chat relay + block action prompt
@dp.message()
async def relay_or_other(message: Message):
    uid = message.from_user.id
    if uid in block_waiting:
        block_waiting.discard(uid)
        raw = (message.text or "").strip()
        if raw.isdigit() and int(raw) != uid:
            target = int(raw)
            with closing(connect_db()) as db:
                db.execute("INSERT OR IGNORE INTO blocks(blocker_id,blocked_id) VALUES(?,?)", (uid,target)); db.commit()
            if partner_of.get(uid) == target: await end_chat(uid)
            await message.answer("کاربر بلاک شد.", reply_markup=main_menu())
        else:
            await message.answer("شناسه نامعتبر است.", reply_markup=main_menu())
        return
    partner = partner_of.get(uid)
    if partner:
        text = message.text or message.caption or ""
        if len(text) > MAX_MESSAGE_LENGTH:
            await message.answer(f"پیامت بیشتر از {MAX_MESSAGE_LENGTH} حرف است؛ کوتاهش کن."); return
        try:
            if message.text:
                await bot.send_message(partner, message.text)
            elif message.photo:
                await bot.send_photo(partner, message.photo[-1].file_id, caption=(message.caption or "")[:MAX_MESSAGE_LENGTH])
            elif message.sticker:
                await bot.send_sticker(partner, message.sticker.file_id)
            elif message.voice:
                await bot.send_voice(partner, message.voice.file_id, caption=(message.caption or "")[:MAX_MESSAGE_LENGTH])
            elif message.video:
                await bot.send_video(partner, message.video.file_id, caption=(message.caption or "")[:MAX_MESSAGE_LENGTH])
            else:
                await message.answer("این نوع پیام فعلاً پشتیبانی نمی‌شود.")
        except Exception:
            await message.answer("پیام ارسال نشد. شاید نفر مقابل چت را بسته باشد.")
        return
    if message.text and message.text.startswith("/"):
        await message.answer("دستور ناشناخته است. از منوی پایین استفاده کن.", reply_markup=main_menu())
    else:
        await message.answer("از منوی پایین انتخاب کن یا «چت ناشناس» را بزن.", reply_markup=main_menu())


async def main():
    print("Hyper Gap bot is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

