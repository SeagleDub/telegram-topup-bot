"""
Клавиатуры для бота
"""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_ID, TEAMLEADER_ID

# Основные клавиатуры
menu_kb_user = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="💰 Заказать пополнение")],
    # [KeyboardButton(text="📂 Запросить расходники")],  # отключено
    [KeyboardButton(text="💸 Получить данные по расходу")],
    [KeyboardButton(text="📱 Получить SMS Google Ads")],
    [KeyboardButton(text="📞 Купить номера"), KeyboardButton(text="📋 Список номеров")],
    [KeyboardButton(text="🌐 Создать/починить лендинг")],
    [KeyboardButton(text="🖼️ Уникализатор")],
    [KeyboardButton(text="📊 Добавить пиксель в систему")],
    [KeyboardButton(text="🌍 Перевод лендинга")]
])

menu_kb_admin_teamleader = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="📢 Сделать рассылку")],
    [KeyboardButton(text="💰 Заказать пополнение")],
    # [KeyboardButton(text="📂 Запросить расходники")],  # отключено
    [KeyboardButton(text="💸 Получить данные по расходу")],
    [KeyboardButton(text="📊 Получить расход по байеру")],
    [KeyboardButton(text="📱 Получить SMS Google Ads")],
    [KeyboardButton(text="📞 Купить номера"), KeyboardButton(text="📋 Список номеров")],
    [KeyboardButton(text="🔄 Автопродление номеров")],
    [KeyboardButton(text="🌐 Создать/починить лендинг")],
    [KeyboardButton(text="🖼️ Уникализатор")],
    [KeyboardButton(text="📊 Добавить пиксель в систему")],
    [KeyboardButton(text="🌍 Перевод лендинга")]
])

cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
    [KeyboardButton(text="❌ Отмена")]
])

ready_kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="✅ Готово")],
    [KeyboardButton(text="❌ Отмена")]
])

def get_menu_keyboard(user_id: int):
    """Возвращает подходящую клавиатуру в зависимости от типа пользователя"""
    if user_id == ADMIN_ID or user_id == TEAMLEADER_ID:
        return menu_kb_admin_teamleader
    else:
        return menu_kb_user

# Inline клавиатуры
def get_bank_keyboard():
    """Клавиатура выбора банка для пополнения"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏦 AdsCard (Facebook)", callback_data="bank:adscard_facebook")],
        [InlineKeyboardButton(text="🏦 AdsCard (Google)", callback_data="bank:adscard_google")],
        [InlineKeyboardButton(text="💳 Traffic.cards (не активно)", callback_data="bank:trafficcards_inactive")],
        [InlineKeyboardButton(text="🃏 MultiCards (Google)", callback_data="bank:multicards_google")]
    ])

def get_topup_type_keyboard():
    """Клавиатура выбора типа пополнения"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Срочное", callback_data="type:urgent"),
         InlineKeyboardButton(text="🕘 Не срочное (до 21:00)", callback_data="type:normal")]
    ])

def get_supply_category_keyboard():
    """Клавиатура выбора категории расходников"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Аккаунты", callback_data="supply:accounts")],
        [InlineKeyboardButton(text="🌐 Домены", callback_data="supply:domains")]
    ])

def get_account_type_keyboard():
    """Клавиатура выбора типа аккаунтов"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 TikTok", callback_data="account_type:tiktok")],
        [InlineKeyboardButton(text="📘 Facebook", callback_data="account_type:facebook")],
        [InlineKeyboardButton(text="🔵 Google", callback_data="account_type:google")]
    ])

def get_landing_category_keyboard():
    """Клавиатура выбора категории лендинга"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Создать лендинг", callback_data="landing:create")],
        [InlineKeyboardButton(text="🔧 Починить лендинг", callback_data="landing:repair")]
    ])

def get_admin_action_keyboard(user_id: int):
    """Клавиатура действий администратора для заявок"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"approve:{user_id}")],
        [InlineKeyboardButton(text="❌ Отклонено", callback_data=f"decline:{user_id}")]
    ])

def get_admin_processing_keyboard(user_id: int):
    """Клавиатура для взятия заявки в работу"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Взять в работу", callback_data=f"processing:{user_id}")],
        [InlineKeyboardButton(text="❌ Отклонено", callback_data=f"decline:{user_id}")]
    ])

def get_google_sms_keyboard():
    """Клавиатура для получения SMS кода Google Ads"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Получить код Google Ads", callback_data="get_google_sms")]
    ])


