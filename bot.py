import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import asyncpg
import logging

# === Настройки ===
TOKEN = "YOUR_BOT_TOKEN"
DATABASE_URL = "postgresql://user:password@localhost/adbot"

logging.basicConfig(level=logging.INFO)

# === FSM Состояния ===
class AdCreation(StatesGroup):
    waiting_for_text = State()
    waiting_for_media = State()
    waiting_for_button = State()

# === Клавиатуры ===
role_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Я — рекламодатель")],
        [KeyboardButton(text="Я — владелец канала")]
    ],
    resize_keyboard=True
)

advertiser_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Создать объявление")],
        [KeyboardButton(text="Мои кампании")],
        [KeyboardButton(text="Баланс")]
    ],
    resize_keyboard=True
)

# === Подключение к БД ===
async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

# === Инициализация таблиц ===
async def init_db():
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                role TEXT,  -- 'advertiser' or 'publisher'
                balance DECIMAL DEFAULT 0.0
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ads (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                text TEXT,
                media_id TEXT,
                button TEXT,
                status TEXT DEFAULT 'pending',  -- pending, approved, rejected
                views INT DEFAULT 0,
                clicks INT DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
    await pool.close()

# === Бот ===
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    pool = await create_db_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT role FROM users WHERE id = $1", user_id)
        if row:
            role = row['role']
            if role == 'advertiser':
                await message.answer("Добро пожаловать, рекламодатель!", reply_markup=advertiser_kb)
            else:
                await message.answer("Добро пожаловать, владелец канала!")
        else:
            await conn.execute(
                "INSERT INTO users (id, role, balance) VALUES ($1, NULL, 0.0)", user_id
            )
            await message.answer(
                "🚀 Добро пожаловать в AdBot TG!\n"
                "Выберите свою роль:",
                reply_markup=role_kb
            )
    await pool.close()

# === Выбор роли ===
@dp.message(F.text == "Я — рекламодатель")
async def select_advertiser(message: Message):
    user_id = message.from_user.id
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET role = 'advertiser' WHERE id = $1", user_id)
    await message.answer("✅ Вы зарегистрированы как рекламодатель!", reply_markup=advertiser_kb)
    await pool.close()

@dp.message(F.text == "Я — владелец канала")
async def select_publisher(message: Message):
    user_id = message.from_user.id
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET role = 'publisher' WHERE id = $1", user_id)
    await message.answer("✅ Вы зарегистрированы как владелец канала!")
    await pool.close()

# === Создать объявление ===
@dp.message(F.text == "Создать объявление")
async def create_ad(message: Message, state: FSMContext):
    await message.answer("📝 Введите текст объявления:")
    await state.set_state(AdCreation.waiting_for_text)

@dp.message(AdCreation.waiting_for_text)
async def ad_text_received(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await message.answer("🖼 Отправьте фото или видео (или пропустите — /skip):")
    await state.set_state(AdCreation.waiting_for_media)

@dp.message(AdCreation.waiting_for_media, F.photo)
async def ad_photo_received(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(media_id=photo_id, media_type="photo")
    await message.answer("🔗 Добавьте кнопку (например: «Перейти — https://site.ru») или /skip:")
    await state.set_state(AdCreation.waiting_for_button)

@dp.message(AdCreation.waiting_for_media, F.video)
async def ad_video_received(message: Message, state: FSMContext):
    video_id = message.video.file_id
    await state.update_data(media_id=video_id, media_type="video")
    await message.answer("🔗 Добавьте кнопку или /skip:")
    await state.set_state(AdCreation.waiting_for_button)

@dp.message(AdCreation.waiting_for_media, F.text == "/skip")
async def skip_media(message: Message, state: FSMContext):
    await state.update_data(media_id=None, media_type=None)
    await message.answer("🔗 Добавьте кнопку или /skip:")
    await state.set_state(AdCreation.waiting_for_button)

@dp.message(AdCreation.waiting_for_button)
async def ad_button_received(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data['text']
    media_id = data.get('media_type')
    button = None
    if message.text and message.text != "/skip":
        try:
            label, url = message.text.split(" — ", 1)
            button = {"label": label, "url": url}
        except:
            await message.answer("❌ Неверный формат. Используйте: Текст — https://...")
            return

    # Сохраняем в БД
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ads (user_id, text, media_id, button, status)
            VALUES ($1, $2, $3, $4, 'pending')
        """, message.from_user.id, text, data.get('media_id'), str(button))
    await pool.close()

    await message.answer("✅ Объявление отправлено на модерацию!")
    await state.clear()

# === Мои кампании ===
@dp.message(F.text == "Мои кампании")
async def my_campaigns(message: Message):
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, text, status, views, clicks FROM ads WHERE user_id = $1", message.from_user.id)
    await pool.close()

    if not rows:
        await message.answer("У вас пока нет объявлений.")
        return

    for row in rows:
        btn_text = f"Статус: {row['status'].upper()}\n📊 Показы: {row['views']} | Клики: {row['clicks']}"
        await message.answer(f"📌 ID: {row['id']}\n{row['text']}\n\n{btn_text}")

# === Баланс ===
@dp.message(F.text == "Баланс")
async def balance(message: Message):
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT balance FROM users WHERE id = $1", message.from_user.id)
    await pool.close()
    await message.answer(f"💰 Ваш баланс: {row['balance']} ₽")

# === Запуск бота ===
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())