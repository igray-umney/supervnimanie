import os
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import aiohttp
import uuid
import base64
import psycopg2
from psycopg2.extras import RealDictCursor
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# ========================================
# КОНФИГУРАЦИЯ
# ========================================

BOT_TOKEN = os.getenv('BOT_TOKEN', '8545217909:AAHfZ7NGN2FZ4J1vq6Z-370SYglciu7I5_4')
CHALLENGE_CHANNEL_ID = os.getenv('CHALLENGE_CHANNEL_ID', '-1003265459459')
CLUB_CHANNEL_ID = os.getenv('CLUB_CHANNEL_ID', '-1003185810463')
YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID', '1119525')
YOOKASSA_SECRET_KEY = os.getenv('YOOKASSA_SECRET_KEY', 'live_PrQj_dYYmn3m9LQh4KRytCZc1BUHsbb1pliPD7koiK8')
ADMIN_ID = int(os.getenv('ADMIN_ID', 6266485372))
DATABASE_URL = os.getenv('DATABASE_URL')

# Ссылка на публичный канал челленджа
CHALLENGE_CHANNEL_LINK = "https://t.me/supervnimanie"

# ОБЫЧНЫЕ ТАРИФЫ (для обычных пользователей)
TARIFFS = {
    '1month': {'name': '1 месяц', 'days': 30, 'price': 490, 'old_price': 990},
    '3months': {'name': '3 месяца', 'days': 90, 'price': 1290, 'old_price': 2490},
    'forever': {'name': 'Навсегда', 'days': 36500, 'price': 2990, 'old_price': 5990}
}

# СПЕЦИАЛЬНЫЕ ТАРИФЫ ДЛЯ УЧАСТНИКОВ ЧЕЛЛЕНДЖА
CHALLENGE_TARIFFS = {
    '1month': {'name': '1 месяц', 'days': 30, 'price': 290, 'old_price': 490},
    'forever': {'name': 'Навсегда', 'days': 36500, 'price': 990, 'old_price': 2990}
}

# Цены в Telegram Stars
TARIFFS_STARS = {
    '1month': {'name': '1 month', 'days': 30, 'price': 200, 'old_price': 400},
    '3months': {'name': '3 months', 'days': 90, 'price': 550, 'old_price': 1000},
    'forever': {'name': 'Forever', 'days': 36500, 'price': 480, 'old_price': 2000}
}

# Время отправки сообщений (МСК = UTC+3)
MORNING_HOUR = 6  # 9:00 МСК = 6:00 UTC
EVENING_HOUR = 17  # 20:00 МСК = 17:00 UTC

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========================================
# FSM СОСТОЯНИЯ ДЛЯ ЧЕЛЛЕНДЖА
# ========================================

class ChallengeStates(StatesGroup):
    # Старт
    CHOOSING_AGE = State()
    
    # День 1
    DAY1_WAITING = State()
    DAY1_ASK_TIME = State()
    DAY1_ASK_DIFFICULTY = State()
    DAY1_OFFER_CATEGORY_CHANGE = State()
    
    # День 2
    DAY2_WAITING = State()
    DAY2_ASK_TIME = State()
    
    # День 3
    DAY3_WAITING = State()
    DAY3_ASK_TIME = State()
    DAY3_SHOW_RESULTS = State()

class UploadMaterialStates(StatesGroup):
    CHOOSING_CATEGORY = State()
    CHOOSING_DAY = State()
    CHOOSING_VARIANT = State()
    ENTERING_TITLE = State()
    ENTERING_DESCRIPTION = State()
    UPLOADING_FILE = State()

# ========================================
# БАЗА ДАННЫХ PostgreSQL
# ========================================

def get_db_connection():
    """Создает подключение к PostgreSQL"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    """Инициализация таблиц в PostgreSQL"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Таблица пользователей
    cur.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id BIGINT PRIMARY KEY,
                  username TEXT,
                  started_at TIMESTAMP,
                  day1_completed BOOLEAN DEFAULT FALSE,
                  day2_completed BOOLEAN DEFAULT FALSE,
                  day3_completed BOOLEAN DEFAULT FALSE,
                  subscription_until TIMESTAMP,
                  tariff TEXT,
                  bot_blocked BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT NOW())''')
    
    # Таблица платежей
    cur.execute('''CREATE TABLE IF NOT EXISTS payments
                 (payment_id TEXT PRIMARY KEY,
                  user_id BIGINT,
                  amount REAL,
                  tariff TEXT,
                  status TEXT,
                  yookassa_id TEXT,
                  created_at TIMESTAMP DEFAULT NOW())''')
    
    # Таблица напоминаний
    cur.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id SERIAL PRIMARY KEY,
                  user_id BIGINT,
                  day INTEGER,
                  reminder_type TEXT,
                  sent_at TIMESTAMP,
                  UNIQUE(user_id, day, reminder_type))''')
    
    # Таблица прогресса челленджа
    cur.execute('''CREATE TABLE IF NOT EXISTS challenge_progress (
        user_id BIGINT PRIMARY KEY,
        age INT,
        age_category VARCHAR(10),
        current_day INT DEFAULT 1,
        is_active BOOLEAN DEFAULT TRUE,
        started_at TIMESTAMP DEFAULT NOW(),
        day1_completed BOOLEAN DEFAULT FALSE,
        day1_time VARCHAR(20),
        day1_difficulty VARCHAR(20),
        day1_completed_at TIMESTAMP,
        day2_completed BOOLEAN DEFAULT FALSE,
        day2_time VARCHAR(20),
        day2_completed_at TIMESTAMP,
        day3_completed BOOLEAN DEFAULT FALSE,
        day3_time VARCHAR(20),
        day3_completed_at TIMESTAMP,
        last_reminder_sent TIMESTAMP,
        reminder_count INT DEFAULT 0,
        day1_reminder_sent BOOLEAN DEFAULT FALSE,
        day2_reminder_sent BOOLEAN DEFAULT FALSE,
        day3_reminder_sent BOOLEAN DEFAULT FALSE,
        category_changed BOOLEAN DEFAULT FALSE,
        original_category VARCHAR(10),
        completed_at TIMESTAMP,
        purchased BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )''')
    
    # Таблица материалов челленджа
    cur.execute('''CREATE TABLE IF NOT EXISTS challenge_materials (
        id SERIAL PRIMARY KEY,
        age_category VARCHAR(10) NOT NULL,
        day INT NOT NULL,
        variant INT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        file_id TEXT NOT NULL,
        file_type VARCHAR(20),
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(age_category, day, variant)
    )''')
    
    conn.commit()
    cur.close()
    conn.close()
    logging.info("Database initialized!")

# ========================================
# ФУНКЦИИ РАБОТЫ С БД (ОСНОВНЫЕ)
# ========================================

def add_user(user_id, username):
    """Добавление нового пользователя"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('''INSERT INTO users (user_id, username, started_at, created_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id) DO NOTHING''',
                (user_id, username, datetime.now(), datetime.now()))
    
    conn.commit()
    cur.close()
    conn.close()

def get_user(user_id):
    """Получение данных пользователя"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user

def mark_user_blocked(user_id, blocked=True):
    """Пометить пользователя как заблокировавшего бота"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE users SET bot_blocked = %s WHERE user_id = %s', (blocked, user_id))
    conn.commit()
    cur.close()
    conn.close()

# ========================================
# ФУНКЦИИ РАБОТЫ С ЧЕЛЛЕНДЖЕМ
# ========================================

def determine_age_category(age):
    """Определить категорию по возрасту"""
    if age <= 4:  # 3, 4 года
        return '3-5'
    elif age <= 6:  # 5, 6 лет
        return '4-6'
    else:  # 7+ лет
        return '5-7'

def start_challenge(user_id, age):
    """Начать челлендж для пользователя"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    category = determine_age_category(age)
    
    cur.execute('''INSERT INTO challenge_progress 
                   (user_id, age, age_category, started_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id) 
                   DO UPDATE SET age = %s, age_category = %s, started_at = %s, is_active = TRUE''',
                (user_id, age, category, datetime.now(), age, category, datetime.now()))
    
    conn.commit()
    cur.close()
    conn.close()

def get_challenge_progress(user_id):
    """Получить прогресс челленджа пользователя"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM challenge_progress WHERE user_id = %s', (user_id,))
    progress = cur.fetchone()
    cur.close()
    conn.close()
    return progress

def update_challenge_day(user_id, day, time_spent, difficulty=None):
    """Обновить данные по дню челленджа"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    if day == 1:
        if difficulty:
            cur.execute('''UPDATE challenge_progress 
                           SET day1_completed = TRUE, day1_time = %s, 
                               day1_difficulty = %s, day1_completed_at = %s, current_day = 2
                           WHERE user_id = %s''',
                        (time_spent, difficulty, datetime.now(), user_id))
        else:
            cur.execute('''UPDATE challenge_progress 
                           SET day1_completed = TRUE, day1_time = %s, 
                               day1_completed_at = %s, current_day = 2
                           WHERE user_id = %s''',
                        (time_spent, datetime.now(), user_id))
    elif day == 2:
        cur.execute('''UPDATE challenge_progress 
                       SET day2_completed = TRUE, day2_time = %s, 
                           day2_completed_at = %s, current_day = 3
                       WHERE user_id = %s''',
                    (time_spent, datetime.now(), user_id))
    elif day == 3:
        cur.execute('''UPDATE challenge_progress 
                       SET day3_completed = TRUE, day3_time = %s, 
                           day3_completed_at = %s, completed_at = %s, is_active = FALSE
                       WHERE user_id = %s''',
                    (time_spent, datetime.now(), datetime.now(), user_id))
    
    conn.commit()
    cur.close()
    conn.close()

def change_age_category(user_id, new_category):
    """Сменить категорию возраста"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Сохраняем оригинальную категорию если это первая смена
    cur.execute('''UPDATE challenge_progress 
                   SET age_category = %s, category_changed = TRUE
                   WHERE user_id = %s''',
                (new_category, user_id))
    
    conn.commit()
    cur.close()
    conn.close()

def get_challenge_materials(age_category, day):
    """Получить материалы для дня челленджа"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''SELECT * FROM challenge_materials 
                   WHERE age_category = %s AND day = %s
                   ORDER BY variant''',
                (age_category, day))
    materials = cur.fetchall()
    cur.close()
    conn.close()
    return materials

def is_challenge_participant(user_id):
    """Проверить является ли пользователь участником челленджа"""
    progress = get_challenge_progress(user_id)
    if progress and progress.get('day3_completed'):
        return True
    return False

def save_material(age_category, day, variant, title, description, file_id, file_type):
    """Сохранить материал в БД"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Проверяем существует ли уже такой материал
    cur.execute('''SELECT id FROM challenge_materials 
                   WHERE age_category = %s AND day = %s AND variant = %s''',
                (age_category, day, variant))
    
    existing = cur.fetchone()
    
    if existing:
        # Обновляем существующий
        cur.execute('''UPDATE challenge_materials 
                       SET title = %s, description = %s, file_id = %s, file_type = %s
                       WHERE age_category = %s AND day = %s AND variant = %s''',
                    (title, description, file_id, file_type, age_category, day, variant))
        result = "updated"
    else:
        # Создаём новый
        cur.execute('''INSERT INTO challenge_materials 
                       (age_category, day, variant, title, description, file_id, file_type)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                    (age_category, day, variant, title, description, file_id, file_type))
        result = "created"
    
    conn.commit()
    cur.close()
    conn.close()
    
    return result

# ========================================
# КЛАВИАТУРЫ ДЛЯ ЧЕЛЛЕНДЖА
# ========================================

def get_age_keyboard():
    """Клавиатура выбора возраста"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="3 года", callback_data="age_3"),
            InlineKeyboardButton(text="4 года", callback_data="age_4"),
            InlineKeyboardButton(text="5 лет", callback_data="age_5")
        ],
        [
            InlineKeyboardButton(text="6 лет", callback_data="age_6"),
            InlineKeyboardButton(text="7 лет", callback_data="age_7")
        ]
    ])
    return keyboard

def get_day_completed_keyboard_new(day):
    """Кнопки завершения дня"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнил", callback_data=f"day{day}_done")],
        [InlineKeyboardButton(text="❌ Не получилось", callback_data=f"day{day}_failed")]
    ])
    return keyboard

def get_time_keyboard(day):
    """Клавиатура выбора времени"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Менее 5 мин", callback_data=f"time{day}_<5")],
        [InlineKeyboardButton(text="5-10 мин", callback_data=f"time{day}_5-10")],
        [InlineKeyboardButton(text="10-15 мин", callback_data=f"time{day}_10-15")],
        [InlineKeyboardButton(text="Более 15 мин", callback_data=f"time{day}_>15")]
    ])
    return keyboard

def get_difficulty_keyboard():
    """Клавиатура оценки сложности"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😊 Легко, справился быстро", callback_data="diff_easy")],
        [InlineKeyboardButton(text="👍 Нормально, подходит", callback_data="diff_normal")],
        [InlineKeyboardButton(text="😓 Сложно, не получилось", callback_data="diff_hard")]
    ])
    return keyboard

def get_category_change_keyboard(new_category):
    """Клавиатура предложения смены категории"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, перейти", callback_data=f"change_cat_{new_category}")],
        [InlineKeyboardButton(text="Нет, оставить текущий", callback_data="keep_category")]
    ])
    return keyboard

def get_category_keyboard():
    """Клавиатура выбора категории"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3-5 лет", callback_data="upload_cat_3-5")],
        [InlineKeyboardButton(text="4-6 лет", callback_data="upload_cat_4-6")],
        [InlineKeyboardButton(text="5-7 лет", callback_data="upload_cat_5-7")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")]
    ])
    return keyboard


def get_day_keyboard():
    """Клавиатура выбора дня"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="День 1", callback_data="upload_day_1")],
        [InlineKeyboardButton(text="День 2", callback_data="upload_day_2")],
        [InlineKeyboardButton(text="День 3", callback_data="upload_day_3")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")]
    ])
    return keyboard


def get_variant_keyboard():
    """Клавиатура выбора варианта"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вариант 1", callback_data="upload_var_1")],
        [InlineKeyboardButton(text="Вариант 2", callback_data="upload_var_2")],
        [InlineKeyboardButton(text="Вариант 3", callback_data="upload_var_3")],
        [InlineKeyboardButton(text="Вариант 4", callback_data="upload_var_4")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")]
    ])
    return keyboard

# ========================================
# ХЭНДЛЕРЫ ЧЕЛЛЕНДЖА
# ========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработчик команды /start - автоматически запускает челлендж"""
    user_id = message.from_user.id
    username = message.from_user.username or "unknown"
    
    # Добавляем пользователя в БД
    add_user(user_id, username)
    
    # Проверяем есть ли уже прогресс в челлендже
    progress = get_challenge_progress(user_id)
    
    if progress and progress.get('is_active'):
        # Челлендж уже идет
        current_day = progress.get('current_day', 1)
        await message.answer(
            f"👋 С возвращением, {message.from_user.first_name}!\n\n"
            f"Вы проходите челлендж «Супервнимание»!\n"
            f"📅 Текущий день: {current_day}\n\n"
            "Продолжайте занятия! 💪",
            reply_markup=get_main_menu()
        )
        return
    
    # Проверяем завершен ли челлендж
    if progress and progress.get('day3_completed'):
        user = get_user(user_id)
        # Проверяем есть ли активная подписка
        if user and user.get('subscription_until'):
            if datetime.now() < user['subscription_until']:
                await message.answer(
                    f"👋 Привет, {message.from_user.first_name}!\n\n"
                    "У вас уже есть доступ к полному курсу! 🎉\n\n"
                    "Переходите в клуб и продолжайте занятия!",
                    reply_markup=get_main_menu()
                )
                return
        
        # Челлендж пройден, но подписки нет
        await message.answer(
            f"👋 Привет, {message.from_user.first_name}!\n\n"
            "Вы прошли челлендж! 🏆\n\n"
            "Готовы продолжить с полным курсом?",
            reply_markup=get_main_menu()
        )
        return
    
    # Новый пользователь - начинаем челлендж
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "🎯 <b>Добро пожаловать в 3-дневный челлендж</b>\n"
        "<b>«Супервнимание»!</b>\n\n"
        "За 3 дня вы:\n"
        "✅ Улучшите концентрацию ребенка\n"
        "✅ Получите готовые задания на каждый день\n"
        "✅ Увидите первые результаты\n"
        "✅ Научитесь развивать внимание через игру\n\n"
        "💡 Все материалы уже готовы - начнем прямо сейчас!\n\n"
        "📝 <b>Первый вопрос:</b>\n"
        "Сколько лет вашему ребенку?",
        reply_markup=get_age_keyboard(),
        parse_mode="HTML"
    )
    
    await state.set_state(ChallengeStates.CHOOSING_AGE)

@dp.callback_query(F.data.startswith("age_"), StateFilter(ChallengeStates.CHOOSING_AGE))
async def process_age_selection(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора возраста"""
    user_id = callback.from_user.id
    age = int(callback.data.split("_")[1])
    
    # Определяем категорию
    category = determine_age_category(age)
    
    # Сохраняем в БД
    start_challenge(user_id, age)
    
    # Формируем текст в зависимости от категории
    category_text = {
        '3-5': '3-5 лет',
        '4-6': '4-6 лет',
        '5-7': '5-7 лет'
    }
    
    await callback.message.edit_text(
        f"Отлично! Для ребенка {age} лет я подобрал категорию <b>{category_text[category]}</b>.\n\n"
        "🎯 <b>ДЕНЬ 1: Тестирование</b>\n\n"
        "Сегодня проверим текущий уровень концентрации ребенка.\n\n"
        "Готовы начать?\n\n"
        "👇 Нажмите кнопку ниже, когда будете готовы получить задания!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать День 1!", callback_data="start_day1")]
        ]),
        parse_mode="HTML"
    )
    
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "start_day1")
async def start_day1(callback: types.CallbackQuery):
    """Начало Дня 1"""
    user_id = callback.from_user.id
    progress = get_challenge_progress(user_id)
    
    if not progress:
        await callback.answer("Ошибка! Начните с /start", show_alert=True)
        return
    
    category = progress['age_category']
    
    # Получаем материалы для этой категории
    materials = get_challenge_materials(category, 1)
    
    # Формируем список вариантов
    if category == '3-5':
        variants_text = (
            "🟢 Вариант 1: «Найди отличия»\n"
            "🟢 Вариант 2: «Лабиринт»\n"
            "🟢 Вариант 3: «Найди пару»"
        )
    elif category == '4-6':
        variants_text = (
            "🟢 Вариант 1: «Найди спрятанные объекты»\n"
            "🟢 Вариант 2: «Дорисуй половинку»\n"
            "🟢 Вариант 3: «Сортировка по категориям»\n"
            "🟢 Вариант 4: «Лабиринт»"
        )
    else:  # 5-7
        variants_text = (
            "🟢 Вариант 1: «Соедини точки по числам (1-20)»\n"
            "🟢 Вариант 2: «Раскраски с категориями»\n"
            "🟢 Вариант 3: «Задания на классификацию»"
        )
    
    text = (
        "🎯 <b>ДЕНЬ 1: Тестирование</b>\n\n"
        "Предложите ребенку на выбор — пусть сам решит, что ему интереснее:\n\n"
        f"{variants_text}\n\n"
        "Ребенок может выбрать один вариант или попробовать все, если ему понравится!\n\n"
        "⏱ <b>ВАЖНО:</b> Засеките время - сколько долго ребенок будет вовлечен в процесс.\n\n"
    )
    
    # Если есть материалы - отправляем
    if materials:
        text += "📎 Сейчас отправлю вам все материалы...\n\n"
    else:
        text += "⚠️ <i>Материалы для этого дня еще загружаются. Пока вы можете использовать свои задания.</i>\n\n"
    
    await callback.message.edit_text(text, parse_mode="HTML")
    
    # Отправляем материалы
    if materials:
        for material in materials:
            try:
                caption = f"📄 <b>{material['title']}</b>"
                if material.get('description'):
                    caption += f"\n\n{material['description']}"
                
                if material['file_type'] == 'photo':
                    await bot.send_photo(user_id, material['file_id'], caption=caption, parse_mode="HTML")
                elif material['file_type'] == 'document':
                    await bot.send_document(user_id, material['file_id'], caption=caption, parse_mode="HTML")
                
                await asyncio.sleep(0.5)
            except Exception as e:
                logging.error(f"Error sending material: {e}")
    
    # Отправляем кнопки завершения
    await bot.send_message(
        user_id,
        "Выполнили задание?",
        reply_markup=get_day_completed_keyboard_new(1)
    )
    
    await callback.answer()

@dp.callback_query(F.data == "day1_done")
async def day1_completed(callback: types.CallbackQuery):
    """День 1 выполнен - спрашиваем время"""
    await callback.message.edit_text(
        "Отлично! 👏\n\n"
        "Сколько времени ребенок был увлечен заданием?",
        reply_markup=get_time_keyboard(1)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("time1_"))
async def day1_time_selected(callback: types.CallbackQuery):
    """Время Дня 1 выбрано - спрашиваем сложность"""
    user_id = callback.from_user.id
    time_value = callback.data.replace("time1_", "")
    
    # Сохраняем временно в state (пока не спросили сложность)
    await callback.message.edit_text(
        "Хорошо! Записал. ✍️\n\n"
        "Как ребенку далось задание?",
        reply_markup=get_difficulty_keyboard()
    )
    
    # Сохраняем время в БД временно
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE challenge_progress SET day1_time = %s WHERE user_id = %s', 
                (time_value, user_id))
    conn.commit()
    cur.close()
    conn.close()
    
    await callback.answer()

@dp.callback_query(F.data.startswith("diff_"))
async def day1_difficulty_selected(callback: types.CallbackQuery):
    """Сложность Дня 1 выбрана"""
    user_id = callback.from_user.id
    difficulty = callback.data.replace("diff_", "")
    
    progress = get_challenge_progress(user_id)
    time_spent = progress.get('day1_time')
    
    # Обновляем БД
    update_challenge_day(user_id, 1, time_spent, difficulty)
    
    # В зависимости от сложности - предлагаем смену категории или просто хвалим
    if difficulty == 'easy':
        # Предлагаем повысить сложность
        current_category = progress['age_category']
        
        if current_category == '3-5':
            new_category = '4-6'
        elif current_category == '4-6':
            new_category = '5-7'
        else:
            new_category = None
        
        if new_category:
            await callback.message.edit_text(
                "Вижу что ребенок справляется легко! 💪\n\n"
                f"Хотите попробовать задания посложнее (категория {new_category} лет)?\n\n"
                "Это поможет лучше развивать навыки!",
                reply_markup=get_category_change_keyboard(new_category)
            )
        else:
            # Уже максимальная сложность
            await callback.message.edit_text(
                "🎉 <b>Поздравляю! День 1 пройден!</b>\n\n"
                "Отличное начало! Ребенок справился легко! 💪\n\n"
                "📅 <b>Завтра:</b> День 2 - продолжим развивать внимание!\n\n"
                "Я напомню вам утром. А пока - отдохните и гордитесь собой! 😊",
                parse_mode="HTML",
                reply_markup=get_main_menu()
            )
    
    elif difficulty == 'hard':
        # Предлагаем понизить сложность
        current_category = progress['age_category']
        
        if current_category == '5-7':
            new_category = '4-6'
        elif current_category == '4-6':
            new_category = '3-5'
        else:
            new_category = None
        
        if new_category:
            await callback.message.edit_text(
                "Понимаю, бывает сложно! 😊\n\n"
                f"Хотите попробовать задания попроще (категория {new_category} лет)?\n\n"
                "Главное - чтобы ребенку было интересно!",
                reply_markup=get_category_change_keyboard(new_category)
            )
        else:
            # Уже минимальная сложность
            await callback.message.edit_text(
                "🎉 <b>Поздравляю! День 1 пройден!</b>\n\n"
                "Всё хорошо! Не расстраивайтесь - такие задания развивают упорство. 💪\n\n"
                "📅 <b>Завтра:</b> День 2 - будет легче!\n\n"
                "Я напомню вам утром. Отдохните! 😊",
                parse_mode="HTML",
                reply_markup=get_main_menu()
            )
    
    else:  # normal
        await callback.message.edit_text(
            "🎉 <b>Поздравляю! День 1 пройден!</b>\n\n"
            "Отличное начало! Идеальный уровень сложности! 💪\n\n"
            "📅 <b>Завтра:</b> День 2 - продолжим развивать навыки!\n\n"
            "Я напомню вам утром. А пока - отдохните и гордитесь собой! 😊",
            parse_mode="HTML",
            reply_markup=get_main_menu()
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("change_cat_"))
async def change_category(callback: types.CallbackQuery):
    """Смена категории"""
    user_id = callback.from_user.id
    new_category = callback.data.replace("change_cat_", "")
    
    # Обновляем категорию
    change_age_category(user_id, new_category)
    
    await callback.message.edit_text(
        f"Отлично! Перевёл вас в категорию {new_category} лет. ✅\n\n"
        "🎉 <b>День 1 пройден!</b>\n\n"
        "📅 <b>Завтра:</b> День 2 с новыми заданиями под новый уровень!\n\n"
        "Я напомню вам утром. Отдохните! 😊",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )
    
    await callback.answer()

@dp.callback_query(F.data == "keep_category")
async def keep_category(callback: types.CallbackQuery):
    """Оставить текущую категорию"""
    await callback.message.edit_text(
        "Хорошо! Оставляем текущий уровень. ✅\n\n"
        "🎉 <b>День 1 пройден!</b>\n\n"
        "📅 <b>Завтра:</b> День 2 - продолжим!\n\n"
        "Я напомню вам утром. Отдохните! 😊",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )
    
    await callback.answer()

@dp.callback_query(F.data == "day1_failed")
async def day1_failed(callback: types.CallbackQuery):
    """День 1 не получился"""
    user_id = callback.from_user.id
    progress = get_challenge_progress(user_id)
    
    if not progress:
        await callback.answer("Ошибка! Начните с /start", show_alert=True)
        return
    
    current_category = progress['age_category']
    
    # Предлагаем смену категории
    keyboard_buttons = [
        [InlineKeyboardButton(text="🔄 Попробую еще раз", callback_data="start_day1")]
    ]
    
    # Добавляем кнопку "Сделать легче" если не минимальная сложность
    if current_category == '5-7':
        keyboard_buttons.append([InlineKeyboardButton(text="⬇️ Сделать легче (4-6 лет)", callback_data="change_cat_4-6")])
    elif current_category == '4-6':
        keyboard_buttons.append([InlineKeyboardButton(text="⬇️ Сделать легче (3-5 лет)", callback_data="change_cat_3-5")])
    
    # Добавляем кнопку "Сделать сложнее" если не максимальная сложность
    if current_category == '3-5':
        keyboard_buttons.append([InlineKeyboardButton(text="⬆️ Сделать сложнее (4-6 лет)", callback_data="change_cat_4-6")])
    elif current_category == '4-6':
        keyboard_buttons.append([InlineKeyboardButton(text="⬆️ Сделать сложнее (5-7 лет)", callback_data="change_cat_5-7")])
    
    keyboard_buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="back")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    await callback.message.edit_text(
        "Не расстраивайтесь! Бывает. 😊\n\n"
        "Что помешало?\n"
        "• Нет времени?\n"
        "• Ребенок не захотел?\n"
        "• Задание показалось сложным?\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )
    
    await callback.answer()


    
# ========================================
# СТАРЫЕ ФУНКЦИИ (сохраняем для совместимости)
# ========================================

def get_main_menu():
    """Главное меню"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ℹ️ Мой прогресс", callback_data="my_progress")],
        [InlineKeyboardButton(text="💎 Полный курс", callback_data="show_tariffs")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="faq")]
    ])
    return keyboard

# ПРОДОЛЖЕНИЕ ФАЙЛА bot_v2_part1.py
# Эту часть нужно добавить после строки "# ... (остальной код продолжение следует)"

# ========================================
# ОПЛАТА И YOOKASSA (без изменений)
# ========================================

def create_payment(user_id, amount, tariff, yookassa_id):
    """Создание записи о платеже"""
    conn = get_db_connection()
    cur = conn.cursor()
    payment_id = f"{user_id}_{int(datetime.now().timestamp())}"
    
    cur.execute('''INSERT INTO payments (payment_id, user_id, amount, tariff, status, yookassa_id, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                (payment_id, user_id, amount, tariff, 'pending', yookassa_id, datetime.now()))
    
    conn.commit()
    cur.close()
    conn.close()
    return payment_id

def update_payment_status(yookassa_id, status):
    """Обновление статуса платежа"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE payments SET status = %s WHERE yookassa_id = %s', (status, yookassa_id))
    conn.commit()
    cur.close()
    conn.close()

def get_payment_by_yookassa_id(yookassa_id):
    """Получение платежа по ID ЮКассы"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM payments WHERE yookassa_id = %s', (yookassa_id,))
    payment = cur.fetchone()
    cur.close()
    conn.close()
    return payment

def grant_subscription(user_id, tariff_code):
    """Выдать подписку пользователю"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Проверяем в каком словаре искать тариф
    if tariff_code in TARIFFS:
        tariff = TARIFFS[tariff_code]
    elif tariff_code in CHALLENGE_TARIFFS:
        tariff = CHALLENGE_TARIFFS[tariff_code]
    else:
        tariff = TARIFFS_STARS[tariff_code]
    
    subscription_until = datetime.now() + timedelta(days=tariff['days'])
    
    cur.execute('''UPDATE users 
                   SET subscription_until = %s, tariff = %s 
                   WHERE user_id = %s''',
                (subscription_until, tariff_code, user_id))
    
    # Отмечаем что участник челленджа купил
    cur.execute('''UPDATE challenge_progress 
                   SET purchased = TRUE 
                   WHERE user_id = %s''',
                (user_id,))
    
    conn.commit()
    cur.close()
    conn.close()

async def create_yookassa_payment(amount, description, user_id):
    """Создание платежа в ЮKassa"""
    url = "https://api.yookassa.ru/v3/payments"
    
    idempotence_key = str(uuid.uuid4())
    auth_string = f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}"
    auth_bytes = auth_string.encode('utf-8')
    auth_b64 = base64.b64encode(auth_bytes).decode('utf-8')
    
    headers = {
        "Idempotence-Key": idempotence_key,
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth_b64}"
    }
    
    data = {
        "amount": {
            "value": f"{amount:.2f}",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{(await bot.get_me()).username}"
        },
        "capture": True,
        "description": description,
        "metadata": {
            "user_id": str(user_id)
        }
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                return result
            else:
                logging.error(f"YooKassa error: {response.status}, {await response.text()}")
                return None

async def check_yookassa_payment(payment_id):
    """Проверка статуса платежа в ЮKassa"""
    url = f"https://api.yookassa.ru/v3/payments/{payment_id}"
    
    auth_string = f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}"
    auth_bytes = auth_string.encode('utf-8')
    auth_b64 = base64.b64encode(auth_bytes).decode('utf-8')
    
    headers = {
        "Authorization": f"Basic {auth_b64}"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                return result
            else:
                logging.error(f"YooKassa check error: {response.status}")
                return None

# ========================================
# КЛАВИАТУРЫ ДЛЯ ОПЛАТЫ
# ========================================

def get_tariffs_menu(use_stars=False, is_challenge_participant=False):
    """Меню выбора тарифов"""
    if use_stars:
        # Меню для Stars (международные пользователи)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"1️⃣ 1 month - {TARIFFS_STARS['1month']['price']} ⭐",
                callback_data="stars_1month"
            )],
            [InlineKeyboardButton(
                text=f"3️⃣ 3 months - {TARIFFS_STARS['3months']['price']} ⭐",
                callback_data="stars_3months"
            )],
            [InlineKeyboardButton(
                text=f"♾️ FOREVER - {TARIFFS_STARS['forever']['price']} ⭐ 🔥 BEST!",
                callback_data="stars_forever"
            )],
            [InlineKeyboardButton(text="◀️ Back", callback_data="back")]
        ])
    elif is_challenge_participant:
        # СПЕЦИАЛЬНЫЕ ЦЕНЫ ДЛЯ УЧАСТНИКОВ ЧЕЛЛЕНДЖА
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"1️⃣ 1 месяц - {CHALLENGE_TARIFFS['1month']['price']}₽ 🔥 -40%!",
                callback_data="challenge_1month"
            )],
            [InlineKeyboardButton(
                text=f"♾️ НАВСЕГДА - {CHALLENGE_TARIFFS['forever']['price']}₽ 🔥 ВЫГОДНЕЕ!",
                callback_data="challenge_forever"
            )],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
        ])
    else:
        # Обычные цены
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"1️⃣ 1 месяц - {TARIFFS['1month']['price']}₽",
                callback_data="1month"
            )],
            [InlineKeyboardButton(
                text=f"3️⃣ 3 месяца - {TARIFFS['3months']['price']}₽",
                callback_data="3months"
            )],
            [InlineKeyboardButton(
                text=f"♾️ НАВСЕГДА - {TARIFFS['forever']['price']}₽",
                callback_data="forever"
            )],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
        ])
    return keyboard

# ========================================
# ОБРАБОТЧИКИ МЕНЮ И ПРОГРЕССА
# ========================================

@dp.callback_query(F.data == "my_progress")
async def my_progress(callback: types.CallbackQuery):
    """Показать прогресс пользователя"""
    user_id = callback.from_user.id
    progress = get_challenge_progress(user_id)
    
    if not progress:
        await callback.answer("Начните челлендж с /start", show_alert=True)
        return
    
    # Формируем текст
    text = "📊 <b>Ваш прогресс в челлендже:</b>\n\n"
    text += f"Категория: {progress['age_category']} лет\n"
    text += f"Возраст ребенка: {progress['age']} лет\n\n"
    
    text += f"День 1: {'✅' if progress.get('day1_completed') else '⏳'}"
    if progress.get('day1_time'):
        text += f" ({progress['day1_time']} мин)\n"
    else:
        text += "\n"
    
    text += f"День 2: {'✅' if progress.get('day2_completed') else '⏳'}"
    if progress.get('day2_time'):
        text += f" ({progress['day2_time']} мин)\n"
    else:
        text += "\n"
    
    text += f"День 3: {'✅' if progress.get('day3_completed') else '⏳'}"
    if progress.get('day3_time'):
        text += f" ({progress['day3_time']} мин)\n"
    else:
        text += "\n"
    
    # Считаем прогресс
    completed = 0
    if progress.get('day1_completed'):
        completed += 1
    if progress.get('day2_completed'):
        completed += 1
    if progress.get('day3_completed'):
        completed += 1
    
    text += f"\nПройдено: {completed}/3 дней\n"
    
    if progress.get('started_at'):
        days_passed = (datetime.now() - progress['started_at']).days
        text += f"С начала: {days_passed} дн.\n\n"
    
    if completed == 3:
        text += "🏆 Челлендж завершен! Поздравляем!\n\n"
        text += "Готовы продолжить с полным курсом?"
    else:
        text += "💪 Продолжайте в том же духе!"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )
    
    await callback.answer()

@dp.callback_query(F.data == "show_tariffs")
async def show_tariffs(callback: types.CallbackQuery):
    """Показать тарифы"""
    user_id = callback.from_user.id
    
    # Проверяем является ли участником челленджа
    is_participant = is_challenge_participant(user_id)
    
    if is_participant:
        text = (
            "💎 <b>Полный курс «Супервнимание»</b>\n\n"
            "🎉 <b>СПЕЦИАЛЬНАЯ ЦЕНА ДЛЯ ВАС!</b>\n"
            "Вы прошли челлендж - получите скидку 40%!\n\n"
            "🎯 Что вы получите:\n\n"
            "📚 Полный курс на год\n"
            "🎮 1000+ материалов\n"
            "🎨 Новые игры каждую неделю\n"
            "💬 Поддержка в чате\n"
            "📅 Готовые планы на каждый день\n\n"
            "⏰ <b>Предложение действует 24 часа!</b>\n\n"
            "💳 <b>Выберите способ оплаты:</b>"
        )
    else:
        text = (
            "💎 <b>Полный курс «Супервнимание»</b>\n\n"
            "🎯 Что вы получите:\n\n"
            "📚 Полный курс на год\n"
            "🎮 1000+ материалов\n"
            "🎨 Новые игры каждую неделю\n"
            "💬 Поддержка в чате\n"
            "📅 Готовые планы на каждый день\n\n"
            "💳 <b>Выберите способ оплаты:</b>"
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Карта РФ (рубли)", callback_data="payment_rub")],
            [InlineKeyboardButton(text="⭐ Карта не РФ (Telegram Stars)", callback_data="payment_stars")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
        ]),
        parse_mode="HTML"
    )
    
    await callback.answer()

@dp.callback_query(F.data == "payment_rub")
async def show_tariffs_rub(callback: types.CallbackQuery):
    """Показать тарифы для оплаты рублями"""
    user_id = callback.from_user.id
    is_participant = is_challenge_participant(user_id)
    
    if is_participant:
        text = (
            "💎 <b>Специальная цена для участников челленджа!</b>\n\n"
            "🔥 Скидка 40% только для вас!\n\n"
            "⏰ Действует 24 часа после прохождения челленджа!"
        )
    else:
        text = "💎 <b>Полный курс «Супервнимание»</b>\n\n💰 <b>Оплата картой РФ:</b>"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_tariffs_menu(use_stars=False, is_challenge_participant=is_participant),
        parse_mode="HTML"
    )
    
    await callback.answer()

@dp.callback_query(F.data == "payment_stars")
async def show_tariffs_stars(callback: types.CallbackQuery):
    """Показать тарифы для оплаты Stars"""
    await callback.message.edit_text(
        "💎 <b>Full Course 'Super Attention'</b>\n\n"
        "⭐ <b>Payment with Telegram Stars:</b>",
        reply_markup=get_tariffs_menu(use_stars=True),
        parse_mode="HTML"
    )
    
    await callback.answer()

# ========================================
# ОБРАБОТЧИКИ ОПЛАТЫ
# ========================================

@dp.callback_query(F.data.startswith("challenge_"))
async def process_challenge_tariff(callback: types.CallbackQuery):
    """Обработка выбора тарифа со скидкой для участников челленджа"""
    user_id = callback.from_user.id
    tariff_code = callback.data.replace("challenge_", "")
    tariff = CHALLENGE_TARIFFS[tariff_code]
    
    await callback.answer("⏳ Создаём платёж...", show_alert=False)
    
    payment = await create_yookassa_payment(
        amount=tariff['price'],
        description=f"Полный курс (челлендж): {tariff['name']}",
        user_id=user_id
    )
    
    if not payment:
        await callback.message.edit_text(
            "❌ Ошибка создания платежа. Попробуйте позже.",
            reply_markup=get_main_menu()
        )
        return
    
    create_payment(user_id, tariff['price'], tariff_code, payment['id'])
    confirmation_url = payment['confirmation']['confirmation_url']
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url)],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_{payment['id']}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
    ])
    
    await callback.message.edit_text(
        f"📦 <b>СПЕЦИАЛЬНАЯ ЦЕНА!</b>\n\n"
        f"Вы выбрали: {tariff['name']}\n\n"
        f"💰 Обычная цена: <s>{tariff['old_price']}₽</s>\n"
        f"🔥 Цена для вас: <b>{tariff['price']}₽</b>\n"
        f"💎 Экономия: {tariff['old_price'] - tariff['price']}₽!\n\n"
        f"⏰ <b>Предложение действует 24 часа!</b>\n\n"
        f"1️⃣ Нажмите «Оплатить»\n"
        f"2️⃣ Завершите оплату\n"
        f"3️⃣ Вернитесь и нажмите «Проверить оплату»\n\n"
        f"⚠️ Доступ откроется автоматически!",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.in_(['1month', '3months', 'forever']))
async def process_tariff(callback: types.CallbackQuery):
    """Обработка выбора обычного тарифа"""
    user_id = callback.from_user.id
    tariff_code = callback.data
    tariff = TARIFFS[tariff_code]
    
    await callback.answer("⏳ Создаём платёж...", show_alert=False)
    
    payment = await create_yookassa_payment(
        amount=tariff['price'],
        description=f"Полный курс: {tariff['name']}",
        user_id=user_id
    )
    
    if not payment:
        await callback.message.edit_text(
            "❌ Ошибка создания платежа. Попробуйте позже.",
            reply_markup=get_main_menu()
        )
        return
    
    create_payment(user_id, tariff['price'], tariff_code, payment['id'])
    confirmation_url = payment['confirmation']['confirmation_url']
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url)],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_{payment['id']}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
    ])
    
    await callback.message.edit_text(
        f"📦 <b>Вы выбрали: {tariff['name']}</b>\n\n"
        f"💰 К оплате: <b>{tariff['price']}₽</b>\n\n"
        f"1️⃣ Нажмите «Оплатить»\n"
        f"2️⃣ Завершите оплату\n"
        f"3️⃣ Вернитесь и нажмите «Проверить оплату»",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("check_"))
async def check_payment(callback: types.CallbackQuery):
    """Проверка оплаты"""
    yookassa_payment_id = callback.data.replace("check_", "")
    await callback.answer("⏳ Проверяем оплату...", show_alert=False)
    
    payment_info = await check_yookassa_payment(yookassa_payment_id)
    
    if not payment_info:
        await callback.answer("❌ Ошибка проверки платежа", show_alert=True)
        return
    
    status = payment_info.get('status')
    
    if status == 'succeeded':
        payment = get_payment_by_yookassa_id(yookassa_payment_id)
        if payment:
            user_id = payment['user_id']
            tariff_code = payment['tariff']
            
            # Определяем из какого словаря тариф
            if tariff_code in CHALLENGE_TARIFFS:
                tariff = CHALLENGE_TARIFFS[tariff_code]
            else:
                tariff = TARIFFS[tariff_code]
            
            update_payment_status(yookassa_payment_id, 'completed')
            grant_subscription(user_id, tariff_code)
            
            try:
                # Создаём инвайт в клуб
                if tariff_code == 'forever' or 'forever' in tariff_code:
                    invite_link = await bot.create_chat_invite_link(CLUB_CHANNEL_ID, member_limit=1)
                else:
                    invite_link = await bot.create_chat_invite_link(
                        CLUB_CHANNEL_ID,
                        member_limit=1,
                        expire_date=datetime.now() + timedelta(days=tariff['days'])
                    )
                
                await callback.message.edit_text(
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"🎉 Поздравляем! Вы получили полный доступ!\n"
                    f"📅 Тариф: {tariff['name']}\n\n"
                    f"Переходите в клуб:\n{invite_link.invite_link}",
                    reply_markup=get_main_menu(),
                    parse_mode="HTML"
                )
                
                # Уведомление админу
                if ADMIN_ID:
                    await bot.send_message(
                        ADMIN_ID,
                        f"💰 Новая оплата!\n"
                        f"👤 @{callback.from_user.username or 'unknown'} (ID: {user_id})\n"
                        f"📦 Тариф: {tariff['name']}\n"
                        f"💵 Сумма: {tariff['price']}₽"
                    )
            
            except Exception as e:
                logging.error(f"Error creating invite: {e}")
                await callback.message.edit_text(
                    "✅ Оплата получена!\n"
                    "❌ Ошибка создания приглашения.\n"
                    "Обратитесь к администратору.",
                    reply_markup=get_main_menu()
                )
    
    elif status == 'pending':
        await callback.answer("⏳ Платёж в обработке. Попробуйте через минуту.", show_alert=True)
    else:
        await callback.answer(f"❌ Статус платежа: {status}", show_alert=True)

@dp.callback_query(F.data == "back")
async def go_back(callback: types.CallbackQuery):
    """Возврат в главное меню"""
    await callback.message.edit_text(
        f"👋 Привет, {callback.from_user.first_name}!\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "faq")
async def show_faq(callback: types.CallbackQuery):
    """FAQ"""
    await callback.message.edit_text(
        "❓ <b>Частые вопросы</b>\n\n"
        "<b>Q: Что такое челлендж?</b>\n"
        "A: 3 дня интенсивных занятий с ребёнком по развитию внимания.\n\n"
        "<b>Q: Это бесплатно?</b>\n"
        "A: Да! Челлендж полностью бесплатный.\n\n"
        "<b>Q: Что после челленджа?</b>\n"
        "A: Вы получите специальную скидку 40% на полный курс!\n\n"
        "<b>Q: Как получить доступ к клубу?</b>\n"
        "A: Пройдите челлендж и купите полный курс со скидкой.\n\n"
        "💬 Остались вопросы? Напишите @razvitie_dety",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()

# ========================================
# АДМИН КОМАНДЫ
# ========================================

@dp.message(Command("stats"))
async def admin_stats(message: types.Message):
    """Статистика (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Общая статистика
    cur.execute('SELECT COUNT(*) as count FROM users')
    total_users = cur.fetchone()['count']
    
    # Статистика челленджа
    cur.execute('SELECT COUNT(*) as count FROM challenge_progress')
    challenge_started = cur.fetchone()['count']
    
    cur.execute('SELECT COUNT(*) as count FROM challenge_progress WHERE day1_completed = TRUE')
    day1_completed = cur.fetchone()['count']
    
    cur.execute('SELECT COUNT(*) as count FROM challenge_progress WHERE day2_completed = TRUE')
    day2_completed = cur.fetchone()['count']
    
    cur.execute('SELECT COUNT(*) as count FROM challenge_progress WHERE day3_completed = TRUE')
    day3_completed = cur.fetchone()['count']
    
    cur.execute('SELECT COUNT(*) as count FROM challenge_progress WHERE purchased = TRUE')
    challenge_purchased = cur.fetchone()['count']
    
    # Оплаты
    cur.execute('SELECT COUNT(*) as count FROM users WHERE subscription_until > NOW()')
    paid_users = cur.fetchone()['count']
    
    cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM payments WHERE status = %s', ('completed',))
    revenue = cur.fetchone()['total']
    
    cur.close()
    conn.close()
    
    # Конверсии
    if challenge_started > 0:
        conv_day1 = (day1_completed / challenge_started * 100)
        conv_day2 = (day2_completed / challenge_started * 100)
        conv_day3 = (day3_completed / challenge_started * 100)
    else:
        conv_day1 = conv_day2 = conv_day3 = 0
    
    if day3_completed > 0:
        conv_purchase = (challenge_purchased / day3_completed * 100)
    else:
        conv_purchase = 0
    
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n\n"
        "<b>Челлендж:</b>\n"
        f"🚀 Начали: {challenge_started}\n"
        f"✅ День 1: {day1_completed} ({conv_day1:.1f}%)\n"
        f"✅ День 2: {day2_completed} ({conv_day2:.1f}%)\n"
        f"✅ День 3: {day3_completed} ({conv_day3:.1f}%)\n"
        f"💳 Купили: {challenge_purchased} ({conv_purchase:.1f}% от завершивших)\n\n"
        f"💎 Всего оплатили: {paid_users}\n"
        f"💰 Общий доход: {revenue:.0f}₽"
    )
    
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("upload_material"))
async def cmd_upload_material(message: types.Message, state: FSMContext):
    """Команда для загрузки материалов (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Эта команда доступна только администратору.")
        return
    
    await message.answer(
        "📤 <b>Загрузка материалов челленджа</b>\n\n"
        "Выберите категорию возраста:",
        reply_markup=get_category_keyboard(),
        parse_mode="HTML"
    )
    
    await state.set_state(UploadMaterialStates.CHOOSING_CATEGORY)


@dp.callback_query(F.data.startswith("upload_cat_"), StateFilter(UploadMaterialStates.CHOOSING_CATEGORY))
async def upload_category_selected(callback: types.CallbackQuery, state: FSMContext):
    """Выбрана категория"""
    category = callback.data.replace("upload_cat_", "")
    
    await state.update_data(category=category)
    
    await callback.message.edit_text(
        f"✅ Категория: <b>{category} лет</b>\n\n"
        "Теперь выберите день:",
        reply_markup=get_day_keyboard(),
        parse_mode="HTML"
    )
    
    await state.set_state(UploadMaterialStates.CHOOSING_DAY)
    await callback.answer()


@dp.callback_query(F.data.startswith("upload_day_"), StateFilter(UploadMaterialStates.CHOOSING_DAY))
async def upload_day_selected(callback: types.CallbackQuery, state: FSMContext):
    """Выбран день"""
    day = int(callback.data.replace("upload_day_", ""))
    
    await state.update_data(day=day)
    
    data = await state.get_data()
    category = data.get('category')
    
    await callback.message.edit_text(
        f"✅ Категория: <b>{category} лет</b>\n"
        f"✅ День: <b>{day}</b>\n\n"
        "Теперь выберите номер варианта:",
        reply_markup=get_variant_keyboard(),
        parse_mode="HTML"
    )
    
    await state.set_state(UploadMaterialStates.CHOOSING_VARIANT)
    await callback.answer()


@dp.callback_query(F.data.startswith("upload_var_"), StateFilter(UploadMaterialStates.CHOOSING_VARIANT))
async def upload_variant_selected(callback: types.CallbackQuery, state: FSMContext):
    """Выбран вариант"""
    variant = int(callback.data.replace("upload_var_", ""))
    
    await state.update_data(variant=variant)
    
    data = await state.get_data()
    category = data.get('category')
    day = data.get('day')
    
    await callback.message.edit_text(
        f"✅ Категория: <b>{category} лет</b>\n"
        f"✅ День: <b>{day}</b>\n"
        f"✅ Вариант: <b>{variant}</b>\n\n"
        "Теперь введите <b>название задания</b>:\n"
        "Например: <i>Найди отличия</i>",
        parse_mode="HTML"
    )
    
    await state.set_state(UploadMaterialStates.ENTERING_TITLE)
    await callback.answer()


@dp.message(StateFilter(UploadMaterialStates.ENTERING_TITLE))
async def upload_title_entered(message: types.Message, state: FSMContext):
    """Введено название"""
    title = message.text.strip()
    
    if len(title) > 200:
        await message.answer("❌ Название слишком длинное! Максимум 200 символов.")
        return
    
    await state.update_data(title=title)
    
    data = await state.get_data()
    category = data.get('category')
    day = data.get('day')
    variant = data.get('variant')
    
    await message.answer(
        f"✅ Категория: <b>{category} лет</b>\n"
        f"✅ День: <b>{day}</b>\n"
        f"✅ Вариант: <b>{variant}</b>\n"
        f"✅ Название: <b>{title}</b>\n\n"
        "Теперь введите <b>описание</b> (необязательно):\n"
        "Или напишите <code>пропустить</code> чтобы пропустить.\n\n"
        "Например: <i>Найди 5 отличий между картинками</i>",
        parse_mode="HTML"
    )
    
    await state.set_state(UploadMaterialStates.ENTERING_DESCRIPTION)


@dp.message(StateFilter(UploadMaterialStates.ENTERING_DESCRIPTION))
async def upload_description_entered(message: types.Message, state: FSMContext):
    """Введено описание"""
    description = message.text.strip()
    
    if description.lower() in ['пропустить', 'skip', '-']:
        description = None
    elif len(description) > 500:
        await message.answer("❌ Описание слишком длинное! Максимум 500 символов.")
        return
    
    await state.update_data(description=description)
    
    data = await state.get_data()
    category = data.get('category')
    day = data.get('day')
    variant = data.get('variant')
    title = data.get('title')
    
    await message.answer(
        f"✅ Категория: <b>{category} лет</b>\n"
        f"✅ День: <b>{day}</b>\n"
        f"✅ Вариант: <b>{variant}</b>\n"
        f"✅ Название: <b>{title}</b>\n"
        f"✅ Описание: <b>{description or 'без описания'}</b>\n\n"
        "📎 Теперь отправьте <b>файл</b> (фото или PDF):",
        parse_mode="HTML"
    )
    
    await state.set_state(UploadMaterialStates.UPLOADING_FILE)


@dp.message(StateFilter(UploadMaterialStates.UPLOADING_FILE), F.photo)
async def upload_photo_received(message: types.Message, state: FSMContext):
    """Получено фото"""
    # Берём фото наибольшего размера
    photo = message.photo[-1]
    file_id = photo.file_id
    file_type = 'photo'
    
    data = await state.get_data()
    category = data.get('category')
    day = data.get('day')
    variant = data.get('variant')
    title = data.get('title')
    description = data.get('description')
    
    # Сохраняем в БД
    result = save_material(category, day, variant, title, description, file_id, file_type)
    
    action = "обновлён" if result == "updated" else "создан"
    
    await message.answer(
        f"✅ <b>Материал {action}!</b>\n\n"
        f"📋 Детали:\n"
        f"• Категория: {category} лет\n"
        f"• День: {day}\n"
        f"• Вариант: {variant}\n"
        f"• Название: {title}\n"
        f"• Описание: {description or 'нет'}\n"
        f"• Тип: Фото\n"
        f"• File ID: <code>{file_id}</code>\n\n"
        "Загрузить ещё материал?\n"
        "Используй /upload_material",
        parse_mode="HTML"
    )
    
    await state.clear()


@dp.message(StateFilter(UploadMaterialStates.UPLOADING_FILE), F.document)
async def upload_document_received(message: types.Message, state: FSMContext):
    """Получен документ (PDF)"""
    document = message.document
    file_id = document.file_id
    file_type = 'document'
    
    data = await state.get_data()
    category = data.get('category')
    day = data.get('day')
    variant = data.get('variant')
    title = data.get('title')
    description = data.get('description')
    
    # Сохраняем в БД
    result = save_material(category, day, variant, title, description, file_id, file_type)
    
    action = "обновлён" if result == "updated" else "создан"
    
    await message.answer(
        f"✅ <b>Материал {action}!</b>\n\n"
        f"📋 Детали:\n"
        f"• Категория: {category} лет\n"
        f"• День: {day}\n"
        f"• Вариант: {variant}\n"
        f"• Название: {title}\n"
        f"• Описание: {description or 'нет'}\n"
        f"• Тип: Документ (PDF)\n"
        f"• Имя файла: {document.file_name}\n"
        f"• File ID: <code>{file_id}</code>\n\n"
        "Загрузить ещё материал?\n"
        "Используй /upload_material",
        parse_mode="HTML"
    )
    
    await state.clear()


@dp.message(StateFilter(UploadMaterialStates.UPLOADING_FILE))
async def upload_wrong_file_type(message: types.Message, state: FSMContext):
    """Неправильный тип файла"""
    await message.answer(
        "❌ Пожалуйста, отправьте <b>фото</b> или <b>PDF документ</b>!\n\n"
        "Или напишите /cancel чтобы отменить.",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "upload_cancel")
async def upload_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Отмена загрузки"""
    await callback.message.edit_text("❌ Загрузка отменена.")
    await state.clear()
    await callback.answer()


@dp.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Отмена любого процесса"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нечего отменять.")
        return
    
    await state.clear()
    await message.answer("❌ Действие отменено.")


# ====== КОМАНДА ДЛЯ ПРОСМОТРА МАТЕРИАЛОВ ======

@dp.message(Command("list_materials"))
async def cmd_list_materials(message: types.Message):
    """Показать список всех материалов (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Эта команда доступна только администратору.")
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('''SELECT age_category, day, variant, title, file_type
                   FROM challenge_materials
                   ORDER BY age_category, day, variant''')
    
    materials = cur.fetchall()
    cur.close()
    conn.close()
    
    if not materials:
        await message.answer("📭 Материалов пока нет.")
        return
    
    # Группируем по категориям
    text = "📚 <b>Загруженные материалы:</b>\n\n"
    
    current_category = None
    current_day = None
    
    for mat in materials:
        category = mat['age_category']
        day = mat['day']
        variant = mat['variant']
        title = mat['title']
        file_type = mat['file_type']
        
        if category != current_category:
            text += f"\n<b>📂 Категория {category} лет:</b>\n"
            current_category = category
            current_day = None
        
        if day != current_day:
            text += f"\n  <b>📅 День {day}:</b>\n"
            current_day = day
        
        icon = "🖼" if file_type == 'photo' else "📄"
        text += f"    {icon} Вариант {variant}: {title}\n"
    
    text += f"\n<b>Всего:</b> {len(materials)} материалов"
    
    await message.answer(text, parse_mode="HTML")


# ====== КОМАНДА ДЛЯ УДАЛЕНИЯ МАТЕРИАЛА ======

@dp.message(Command("delete_material"))
async def cmd_delete_material(message: types.Message):
    """Удалить материал (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Эта команда доступна только администратору.")
        return
    
    # Парсим аргументы: /delete_material 3-5 1 1
    parts = message.text.split()
    
    if len(parts) != 4:
        await message.answer(
            "❌ Неверный формат!\n\n"
            "Используй: <code>/delete_material категория день вариант</code>\n\n"
            "Например: <code>/delete_material 3-5 1 1</code>",
            parse_mode="HTML"
        )
        return
    
    try:
        category = parts[1]
        day = int(parts[2])
        variant = int(parts[3])
    except ValueError:
        await message.answer("❌ Неверный формат! День и вариант должны быть числами.")
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('''DELETE FROM challenge_materials 
                   WHERE age_category = %s AND day = %s AND variant = %s
                   RETURNING title''',
                (category, day, variant))
    
    deleted = cur.fetchone()
    
    conn.commit()
    cur.close()
    conn.close()
    
    if deleted:
        await message.answer(
            f"✅ Материал удалён!\n\n"
            f"• Категория: {category} лет\n"
            f"• День: {day}\n"
            f"• Вариант: {variant}\n"
            f"• Название: {deleted['title']}"
        )
    else:
        await message.answer("❌ Материал не найден!")

# ========================================
# ЗАПУСК БОТА
# ========================================

async def main():
    """Главная функция"""
    init_db()
    logging.info("Bot started successfully!")
    
    # Polling
    while True:
        try:
            logging.info("Starting polling...")
            await dp.start_polling(bot, timeout=30, request_timeout=20)
        except Exception as e:
            logging.error(f"Polling crashed: {e}")
            logging.info("Restarting in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == '__main__':
    asyncio.run(main())
