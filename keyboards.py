"""
Клавиатуры для бота
"""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_ID, TEAMLEADER_ID

# Основные клавиатуры
# Часть пунктов временно скрыта (закомментирована) — обработчики остаются рабочими.
menu_kb_user = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="💰 Заказать пополнение")],
    # [KeyboardButton(text="📂 Запросить расходники")],  # отключено
    [KeyboardButton(text="💸 Получить данные по расходу (multicards + расходники)")],
    # [KeyboardButton(text="📱 Получить SMS Google Ads")],  # временно скрыто
    # [KeyboardButton(text="📞 Купить номера"), KeyboardButton(text="📋 Список номеров")],  # временно скрыто
    [KeyboardButton(text="💳 Действия с картами (ecards)")],
    [KeyboardButton(text="💸 Расход по группе")],
    [KeyboardButton(text="🌐 Создать/починить лендинг")],
    [KeyboardButton(text="🖼️ Уникализатор")],
    # [KeyboardButton(text="🌍 Перевод лендинга")]  # временно скрыто
])

menu_kb_admin_teamleader = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="💰 Заказать пополнение")],
    # [KeyboardButton(text="📂 Запросить расходники")],  # отключено
    [KeyboardButton(text="💸 Получить данные по расходу (multicards + расходники)")],
    [KeyboardButton(text="📊 Получить расход по байеру")],
    # [KeyboardButton(text="📱 Получить SMS Google Ads")],  # временно скрыто
    # [KeyboardButton(text="📞 Купить номера"), KeyboardButton(text="📋 Список номеров")],  # временно скрыто
    # [KeyboardButton(text="🔄 Автопродление номеров")],  # временно скрыто
    [KeyboardButton(text="💳 Действия с картами (ecards)")],
    [KeyboardButton(text="💸 Расход по группе")],
    [KeyboardButton(text="🌐 Создать/починить лендинг")],
    [KeyboardButton(text="🖼️ Уникализатор")],
    # [KeyboardButton(text="🌍 Перевод лендинга")]  # временно скрыто
])

cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
    [KeyboardButton(text="❌ Отмена")]
])

# Текст кнопки "другая карта" (используется в фильтре хендлера)
ANOTHER_CARD_TEXT = "🔄 Другая карта"

# Клавиатура внутри флоу "Действия с картами": отмена + переход к выбору банка
card_flow_kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text=ANOTHER_CARD_TEXT)],
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


def get_purchase_country_keyboard():
    """Клавиатура выбора страны для покупки номеров"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇬🇧 GB", callback_data="purchase_country:GB")],
        [InlineKeyboardButton(text="🇺🇸 US", callback_data="purchase_country:US")],
        [InlineKeyboardButton(text="🇨🇦 CA", callback_data="purchase_country:CA")]
    ])


def get_card_action_keyboard():
    """Клавиатура выбора действия с картой (eCards).

    Смены лимита в API eCards нет (см. API.md) — доступны блокировка,
    транзакции и 3DS-код из ленты уведомлений.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data="card_action:block")],
        [InlineKeyboardButton(text="📜 Последние транзакции", callback_data="card_action:transactions")],
        [InlineKeyboardButton(text="🔐 3DS код", callback_data="card_action:otp")],
    ])




def get_tx_pagination_keyboard(page: int, pages: int):
    """Стрелки навигации по страницам транзакций. None, если страница одна."""
    if pages <= 1:
        return None
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"txpage:{page - 1}"))
    row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="txpage:noop"))
    if page < pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"txpage:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def get_ecards_group_keyboard():
    """Действия eCards по картам байера (технически по его группе, tg_id в названии).

    Расход вынесен в отдельный пункт меню «💸 Расход по группе».
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Последние транзакции", callback_data="card_group:transactions")],
    ])


def get_group_expenses_period_keyboard():
    """Выбор периода для «💸 Расход по группе»."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Сегодня", callback_data="gexp:today"),
         InlineKeyboardButton(text="📅 Вчера", callback_data="gexp:yesterday")],
        [InlineKeyboardButton(text="💰 За зп период", callback_data="gexp:cycle")],
        [InlineKeyboardButton(text="✍️ Свой диапазон", callback_data="gexp:custom")],
    ])


def get_card_block_confirm_keyboard():
    """Клавиатура подтверждения блокировки карты"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить блокировку", callback_data="card_block_confirm:yes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="card_block_confirm:no")]
    ])


