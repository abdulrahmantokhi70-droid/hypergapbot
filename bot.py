
import os
import asyncio
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not configured")

db = sqlite3.connect("hyper_gap.db")
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    age INTEGER,
    city TEXT,
    coins REAL DEFAULT 0
)
""")
db.commit()

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Profile(StatesGroup):
    name = State()
    age = State()
    city = State()


def menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎭 وصل‌شدن به ناشناس")],
            [KeyboardButton(text="🔎 جستجوی کاربران"),
             KeyboardButton(text="👤 پروفایل")],
            [KeyboardButton(text="🪙 سکه"),
             KeyboardButton(text="📖 راهنما")],
            [KeyboardButton(text="🔗 لینک دعوت"),
             KeyboardButton(text="💬 پیشنهادات")],
        ],
        resize_keyboard=True
    )


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    row = db.execute(
        "SELECT name FROM users WHERE user_id = ?",
        (message.from_user.id,)
    ).fetchone()

    if row:
        await message.answer(
            f"خوش آمدی {row[0]} ❤️\nاز منوی پایین انتخاب کن:",
            reply_markup=menu()
        )
    else:
        await state.set_state(Profile.name)
        await message.answer(
            "به هایپر گپ خوش آمدی ❤️\n"
            "برای ساخت پروفایل، نام نمایشی‌ات را بنویس:"
        )


@dp.message(Profile.name)
async def get_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 40:
        await message.answer("یک نام معتبر تا ۴۰ حرف بنویس.")
        return
    await state.update_data(name=name)
    await state.set_state(Profile.age)
    await message.answer("چند سال داری؟ سن خود را به عدد بنویس.")


@dp.message(Profile.age)
async def get_age(message: Message, state: FSMContext):
    try:
        age = int(message.text)
        if not 13 <= age <= 99:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("سن باید عددی بین ۱۳ تا ۹۹ باشد.")
        return

    await state.update_data(age=age)
    await state.set_state(Profile.city)
    await message.answer("نام شهر خود را بنویس.")


@dp.message(Profile.city)
async def get_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if not city or len(city) > 60:
        await message.answer("نام شهر معتبر بنویس.")
        return

    data = await state.get_data()
    db.execute(
        """INSERT OR REPLACE INTO users
        (user_id, name, age, city, coins)
        VALUES (?, ?, ?, ?, 0)""",
        (message.from_user.id, data["name"], data["age"], city)
    )
    db.commit()
    await state.clear()

    await message.answer(
        "☑️ اطلاعات شما ثبت شد.\n"
        "به خانواده بزرگ هایپر گپ خوش آمدی! ❤️\n"
        "از منوی پایین انتخاب کن 👇",
        reply_markup=menu()
    )


@dp.message(F.text == "👤 پروفایل")
async def profile(message: Message):
    row = db.execute(
        "SELECT name, age, city FROM users WHERE user_id = ?",
        (message.from_user.id,)
    ).fetchone()
    if row:
        await message.answer(
            f"👤 نام: {row[0]}\n🎂 سن: {row[1]}\n"
            f"📍 شهر: {row[2]}"
        )
    else:
        await message.answer("برای شروع /start را بزن.")


@dp.message(F.text == "🪙 سکه")
async def wallet(message: Message):
    row = db.execute(
        "SELECT coins FROM users WHERE user_id = ?",
        (message.from_user.id,)
    ).fetchone()
    if row:
        await message.answer(f"🪙 موجودی تو: {row[0]} سکه")
    else:
        await message.answer("ابتدا /start را بزن و پروفایل بساز.")


@dp.message(F.text == "📖 راهنما")
async def help_message(message: Message):
    await message.answer(
        "📖 راهنمای هایپر گپ\n"
        "ابتدا پروفایل بساز.\n"
        "برای چت ناشناس، دکمه مربوطه را بزن.\n"
        "بخش چت ناشناس هنوز در حال ساخت است."
    )


@dp.message(F.text == "🎭 وصل‌شدن به ناشناس")
async def chat_placeholder(message: Message):
    await message.answer(
        "⏳ بخش اتصال ناشناس هنوز آماده نشده؛ "
        "در مرحله بعد آن را می‌سازیم."
    )


@dp.message()
async def other_messages(message: Message):
    await message.answer(
        "از دکمه‌های منو استفاده کن یا /start را بزن.",
        reply_markup=menu()
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

