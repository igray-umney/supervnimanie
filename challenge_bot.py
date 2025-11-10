import os
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import aiohttp
import uuid
import base64
import psycopg2
from psycopg2.extras import RealDictCursor

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# ========================================
# КОНФИГУРАЦИЯ
# ========================================

BOT_TOKEN = os.getenv('BOT_TOKEN', '8545217909:AAHfZ7NGN2FZ4J1vq6Z-370SYglciu7I5_4')
CHALLENGE_CHANNEL_ID = os.getenv('CHALLENGE_CHANNEL_ID', '-1003265459459')
CLUB_CHANNEL_ID = os.getenv('CLUB_CHANNEL_ID', '-1003185810463')
YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID', '1119525')
YOOKASSA_SECRET_KEY = os.getenv('YOOKASSA_SECRET_KEY', 'live_qkWu9Kao2ozys7nUT7R0pxORcc7YvVX8144U4FWG8LU')
ADMIN_ID = int(os.getenv('ADMIN_ID', 6266485372))
DATABASE_URL = os.getenv('DATABASE_URL')

# Ссылка на публичный канал челленджа
CHALLENGE_CHANNEL_LINK = "https://t.me/supervnimanie"

# Тарифы (с Decoy Pricing для увеличения конверсии в "Навсегда")
TARIFFS = {
    '1month': {'name': '1 месяц', 'days': 30, 'price': 290, 'old_price': 590},
    '3months': {'name': '3 месяца', 'days': 90, 'price': 790, 'old_price': 1490},  # DECOY - делает "Навсегда" выгоднее!
    'forever': {'name': 'Навсегда', 'days': 36500, 'price': 690, 'old_price': 2990}
}

# Время отправки сообщений (МСК = UTC+3)
MORNING_HOUR = 6  # 9:00 МСК = 6:00 UTC
EVENING_HOUR = 17  # 20:00 МСК = 17:00 UTC

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

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
    
    conn.commit()
    cur.close()
    conn.close()
    logging.info("Database initialized!")

# ========================================
# ФУНКЦИИ РАБОТЫ С БД
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

def mark_day_completed(user_id, day):
    """Отметить день как пройденный"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    if day == 1:
        cur.execute('UPDATE users SET day1_completed = TRUE WHERE user_id = %s', (user_id,))
    elif day == 2:
        cur.execute('UPDATE users SET day2_completed = TRUE WHERE user_id = %s', (user_id,))
    elif day == 3:
        cur.execute('UPDATE users SET day3_completed = TRUE WHERE user_id = %s', (user_id,))
    
    conn.commit()
    cur.close()
    conn.close()

def get_users_for_reminders(day, reminder_type):
    """Получить пользователей для отправки напоминаний"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Вычисляем временной диапазон для дня
    if day == 1:
        time_start = datetime.now() - timedelta(hours=24)
        time_end = datetime.now()
    else:
        time_start = datetime.now() - timedelta(days=day)
        time_end = datetime.now() - timedelta(days=day-1)
    
    # Находим пользователей которым нужно отправить напоминание
    cur.execute('''
        SELECT u.user_id, u.username, u.day1_completed, u.day2_completed, u.day3_completed
        FROM users u
        LEFT JOIN reminders r ON u.user_id = r.user_id 
            AND r.day = %s 
            AND r.reminder_type = %s
        WHERE u.started_at >= %s 
          AND u.started_at < %s
          AND r.user_id IS NULL
          AND u.subscription_until IS NULL
    ''', (day, reminder_type, time_start, time_end))
    
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users

def mark_reminder_sent(user_id, day, reminder_type):
    """Отметить что напоминание отправлено"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('''INSERT INTO reminders (user_id, day, reminder_type, sent_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id, day, reminder_type) DO NOTHING''',
                (user_id, day, reminder_type, datetime.now()))
    
    conn.commit()
    cur.close()
    conn.close()

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
    
    tariff = TARIFFS[tariff_code]
    subscription_until = datetime.now() + timedelta(days=tariff['days'])
    
    cur.execute('''UPDATE users 
                   SET subscription_until = %s, tariff = %s 
                   WHERE user_id = %s''',
                (subscription_until, tariff_code, user_id))
    
    conn.commit()
    cur.close()
    conn.close()

# ========================================
# ЮKASSA API
# ========================================

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
# КЛАВИАТУРЫ
# ========================================

def get_main_menu():
    """Главное меню"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать челлендж", callback_data="start_challenge")],
        [InlineKeyboardButton(text="ℹ️ Мой прогресс", callback_data="my_progress")],
        [InlineKeyboardButton(text="💎 Полный курс", callback_data="show_tariffs")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="faq")]
    ])
    return keyboard

def get_day_completed_keyboard(day):
    """Кнопка отметки дня"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ День {day} пройден!", callback_data=f"complete_day_{day}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
    ])
    return keyboard

def get_tariffs_menu():
    """Меню выбора тарифов с Decoy Pricing"""
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
            text=f"♾️ НАВСЕГДА - {TARIFFS['forever']['price']}₽ 🔥 ВЫГОДНЕЕ!",
            callback_data="forever"
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
    ])
    return keyboard

# ========================================
# ОБРАБОТЧИКИ КОМАНД
# ========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username or "unknown"
    
    # Добавляем пользователя в БД
    add_user(user_id, username)
    
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
    
    # Новый пользователь или без подписки
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "🎯 <b>Добро пожаловать в 3-дневный интенсив</b>\n"
        "<b>«Супервнимание»</b>\n\n"
        "За 3 дня вы:\n"
        "✅ Научитесь играть с ребёнком в развивающие игры\n"
        "✅ Получите 10 готовых игр\n"
        "✅ Увидите первые результаты\n"
        "✅ Поймёте как составлять план на день\n\n"
        "💡 Все материалы уже готовы - начните прямо сейчас!",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "start_challenge")
async def start_challenge(callback: types.CallbackQuery):
    """Начало челленджа"""
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    if not user:
        add_user(user_id, callback.from_user.username or "unknown")
    
    await callback.message.edit_text(
        "🚀 <b>Отлично! Начинаем!</b>\n\n"
        "📚 <b>Шаг 1:</b> Присоединитесь к каналу челленджа\n\n"
        f"👉 {CHALLENGE_CHANNEL_LINK}\n\n"
        "Там вас ждут все материалы на 3 дня:\n"
        "• День 1: Видео + задание\n"
        "• День 2: Материалы + практика\n"
        "• День 3: Финальное задание\n\n"
        "После подписки возвращайтесь сюда - я буду напоминать о занятиях и помогать! 💪",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я подписался!", callback_data="check_subscription")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
        ]),
        parse_mode="HTML"
    )
    
    await callback.answer()

@dp.callback_query(F.data == "check_subscription")
async def check_subscription(callback: types.CallbackQuery):
    """Проверка подписки на канал"""
    user_id = callback.from_user.id
    
    try:
        # Проверяем подписку
        member = await bot.get_chat_member(CHALLENGE_CHANNEL_ID, user_id)
        
        if member.status in ['member', 'administrator', 'creator']:
            await callback.message.edit_text(
                "🎉 <b>Отлично! Вы подписаны!</b>\n\n"
                "Теперь вы в челлендже!\n\n"
                "📅 <b>Что дальше:</b>\n\n"
                "• Каждое утро (9:00) я буду напоминать о занятии\n"
                "• Каждый вечер (20:00) спрошу о прогрессе\n"
                "• После 3 дней - сюрприз! 🎁\n\n"
                "💡 Начните прямо сейчас с Дня 1 в канале!",
                reply_markup=get_main_menu(),
                parse_mode="HTML"
            )
            
            # Уведомление админу
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"🎯 Новый участник челленджа!\n"
                    f"👤 @{callback.from_user.username or 'unknown'} (ID: {user_id})"
                )
        else:
            await callback.answer(
                "❌ Вы ещё не подписались на канал! Подпишитесь и возвращайтесь.",
                show_alert=True
            )
    
    except Exception as e:
        logging.error(f"Error checking subscription: {e}")
        await callback.answer(
            "❌ Ошибка проверки подписки. Попробуйте позже.",
            show_alert=True
        )

@dp.callback_query(F.data.startswith("complete_day_"))
async def complete_day(callback: types.CallbackQuery):
    """Отметка прохождения дня"""
    user_id = callback.from_user.id
    day = int(callback.data.split("_")[-1])
    
    user = get_user(user_id)
    
    if not user:
        await callback.answer("Ошибка! Начните с /start", show_alert=True)
        return
    
    # Отмечаем день
    mark_day_completed(user_id, day)
    
    # Поздравление в зависимости от дня
    if day == 1:
        text = (
            "🎉 <b>Поздравляю! День 1 пройден!</b>\n\n"
            "Отличное начало! 💪\n\n"
            "📅 <b>Завтра:</b>\n"
            "День 2 - ещё интереснее!\n\n"
            "Я напомню вам утром. А пока - отдохните и гордитесь собой! 😊"
        )
    elif day == 2:
        text = (
            "🎉 <b>Браво! День 2 позади!</b>\n\n"
            "Вы на финишной прямой! 🏃\n\n"
            "📅 <b>Завтра:</b>\n"
            "День 3 - последний рывок!\n\n"
            "Вы уже так много сделали - осталось совсем чуть-чуть! 💪"
        )
    else:  # day == 3
        text = (
            "🎉 <b>ПОЗДРАВЛЯЮ! Вы прошли весь челлендж!</b>\n\n"
            "Вы большой молодец! 🏆\n\n"
            "За 3 дня вы:\n"
            "✅ Научились играть с ребёнком развивающе\n"
            "✅ Освоили 10 готовых игр\n"
            "✅ Увидели первые результаты\n\n"
            "💎 <b>Что дальше?</b>\n\n"
            "Не останавливайтесь на достигнутом!\n\n"
            "Полный курс «Супервнимание» поможет вам:\n"
            "• Пройти 14-дневную программу\n"
            "• Получить 1000+ материалов\n"
            "• Новые игры каждую неделю\n"
            "• Поддержку и советы\n"
            "• Готовые планы на каждый день\n\n"
            "🎁 Специальная цена для участников челленджа!"
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_main_menu() if day == 3 else InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back")]
        ]),
        parse_mode="HTML"
    )
    
    await callback.answer()

@dp.callback_query(F.data == "my_progress")
async def my_progress(callback: types.CallbackQuery):
    """Показать прогресс пользователя"""
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    if not user:
        await callback.answer("Начните с /start", show_alert=True)
        return
    
    # Считаем прогресс
    completed = 0
    if user.get('day1_completed'):
        completed += 1
    if user.get('day2_completed'):
        completed += 1
    if user.get('day3_completed'):
        completed += 1
    
    # Считаем дни с начала
    if user.get('started_at'):
        days_passed = (datetime.now() - user['started_at']).days
    else:
        days_passed = 0
    
    # Формируем текст
    text = "📊 <b>Ваш прогресс:</b>\n\n"
    text += f"День 1: {'✅' if user.get('day1_completed') else '⏳'}\n"
    text += f"День 2: {'✅' if user.get('day2_completed') else '⏳'}\n"
    text += f"День 3: {'✅' if user.get('day3_completed') else '⏳'}\n\n"
    text += f"Пройдено: {completed}/3 дней\n"
    text += f"С начала: {days_passed} дн.\n\n"
    
    if completed == 3:
        text += "🏆 Челлендж пройден! Поздравляем!"
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
    """Показать тарифы с акцентом на выгоду"""
    await callback.message.edit_text(
        "💎 <b>Полный курс «Супервнимание»</b>\n\n"
        "🎯 Что вы получите:\n\n"
        "📚 Полный 14-дневный курс\n"
        "🎮 1000+ материалов (вместо 11)\n"
        "🎨 Новые игры каждую неделю\n"
        "💬 Поддержка и советы\n"
        "📅 Готовые планы на каждый день\n\n"
        "💰 <b>Выберите тариф:</b>\n\n"
        "🔥 <b>Обратите внимание:</b> тариф «Навсегда» выгоднее чем на 3 месяца!",
        reply_markup=get_tariffs_menu(),
        parse_mode="HTML"
    )
    
    await callback.answer()

@dp.callback_query(F.data.in_(['1month', '3months', 'forever']))
async def process_tariff(callback: types.CallbackQuery):
    """Обработка выбора тарифа"""
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
        f"💰 Полная цена: <s>{tariff['old_price']}₽</s>\n"
        f"💳 К оплате: <b>{tariff['price']}₽</b>\n\n"
        f"1️⃣ Нажмите «Оплатить»\n"
        f"2️⃣ Завершите оплату\n"
        f"3️⃣ Вернитесь и нажмите «Проверить оплату»\n\n"
        f"⚠️ Доступ откроется автоматически!",
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
            tariff = TARIFFS[tariff_code]
            
            update_payment_status(yookassa_payment_id, 'completed')
            grant_subscription(user_id, tariff_code)
            
            try:
                # Создаём инвайт в клуб
                if tariff_code == 'forever':
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
        "🎯 <b>3-дневный интенсив «Супервнимание»</b>\n\n"
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
        "A: Вы сможете продолжить в полном курсе (14 дней + 1000 материалов).\n\n"
        "<b>Q: Как получить доступ к клубу?</b>\n"
        "A: Выберите тариф и оплатите - доступ откроется автоматически.\n\n"
        "💬 Остались вопросы? Напишите @razvitie_dety",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()

# ========================================
# ФОНОВЫЕ ЗАДАЧИ (НАПОМИНАНИЯ)
# ========================================

async def send_reminders():
    """Фоновая задача отправки напоминаний"""
    logging.info("Reminders task started!")
    
    while True:
        try:
            current_hour = datetime.utcnow().hour
            
            # Утренние напоминания (9:00 МСК = 6:00 UTC)
            if current_hour == MORNING_HOUR:
                for day in [1, 2, 3]:
                    users = get_users_for_reminders(day, 'morning')
                    
                    for user in users:
                        user_id = user['user_id']
                        
                        try:
                            if day == 1:
                                text = (
                                    "☀️ <b>Доброе утро!</b>\n\n"
                                    "🎯 Сегодня <b>День 1</b> челленджа!\n\n"
                                    "Переходите в канал и начинайте:\n"
                                    "• Посмотрите видео\n"
                                    "• Сделайте задание 1\n\n"
                                    "Это займёт всего 15-20 минут!\n\n"
                                    "💪 Вы справитесь!"
                                )
                            elif day == 2:
                                text = (
                                    "☀️ <b>Доброе утро!</b>\n\n"
                                    "🎯 Сегодня <b>День 2</b>!\n\n"
                                    "Отличный старт вчера! 💪\n\n"
                                    "Сегодня:\n"
                                    "• Изучите материалы\n"
                                    "• Выполните практику\n\n"
                                    "Продолжаем в том же духе!"
                                )
                            else:  # day 3
                                text = (
                                    "☀️ <b>Доброе утро!</b>\n\n"
                                    "🎯 <b>ФИНАЛЬНЫЙ ДЕНЬ!</b>\n\n"
                                    "Вы уже так много сделали! 🏆\n\n"
                                    "Сегодня:\n"
                                    "• Финальное задание\n"
                                    "• Подведение итогов\n\n"
                                    "Последний рывок - и вы победитель! 💪"
                                )
                            
                            await bot.send_message(user_id, text, parse_mode="HTML")
                            mark_reminder_sent(user_id, day, 'morning')
                            logging.info(f"Sent morning reminder day {day} to {user_id}")
                            
                            await asyncio.sleep(0.1)  # Небольшая задержка
                        
                        except Exception as e:
                            logging.error(f"Error sending morning reminder to {user_id}: {e}")
            
            # Вечерние напоминания (20:00 МСК = 17:00 UTC)
            if current_hour == EVENING_HOUR:
                for day in [1, 2, 3]:
                    users = get_users_for_reminders(day, 'evening')
                    
                    for user in users:
                        user_id = user['user_id']
                        
                        try:
                            # Проверяем прошёл ли день
                            if day == 1 and user['day1_completed']:
                                continue
                            if day == 2 and user['day2_completed']:
                                continue
                            if day == 3 and user['day3_completed']:
                                continue
                            
                            text = (
                                "🌙 <b>Добрый вечер!</b>\n\n"
                                f"Как прошёл День {day}?\n\n"
                                f"Если вы завершили все задания - отметьте это! 👇"
                            )
                            
                            await bot.send_message(
                                user_id,
                                text,
                                reply_markup=get_day_completed_keyboard(day),
                                parse_mode="HTML"
                            )
                            mark_reminder_sent(user_id, day, 'evening')
                            logging.info(f"Sent evening reminder day {day} to {user_id}")
                            
                            await asyncio.sleep(0.1)
                        
                        except Exception as e:
                            logging.error(f"Error sending evening reminder to {user_id}: {e}")
            
            # Проверяем каждые 30 минут
            await asyncio.sleep(1800)
        
        except Exception as e:
            logging.error(f"Error in reminders task: {e}")
            await asyncio.sleep(1800)

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
    
    cur.execute('SELECT COUNT(*) as count FROM users')
    total_users = cur.fetchone()['count']
    
    cur.execute('SELECT COUNT(*) as count FROM users WHERE day3_completed = TRUE')
    completed = cur.fetchone()['count']
    
    cur.execute('SELECT COUNT(*) as count FROM users WHERE subscription_until > NOW()')
    paid = cur.fetchone()['count']
    
    cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM payments WHERE status = %s', ('completed',))
    revenue = cur.fetchone()['total']
    
    cur.close()
    conn.close()
    
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"🏆 Прошли челлендж: {completed}\n"
        f"💎 Купили полный курс: {paid}\n"
        f"💰 Общий доход: {revenue}₽\n"
    )
    
    await message.answer(text, parse_mode="HTML")

# ========================================
# ЗАПУСК БОТА
# ========================================

async def main():
    """Главная функция"""
    init_db()
    logging.info("Bot started successfully!")
    
    # Запускаем фоновые задачи
    asyncio.create_task(send_reminders())
    
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
