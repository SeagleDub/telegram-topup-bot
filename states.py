"""
Состояния FSM для бота
"""
from aiogram.fsm.state import State, StatesGroup

class Form(StatesGroup):
    # Пополнение баланса
    waiting_for_bank = State()
    waiting_for_amount = State()
    waiting_for_type = State()

    # Расходники
    choosing_supply_category = State()
    choosing_account_type = State()
    entering_account_quantity = State()
    entering_domain_quantity = State()

    # Лендинги
    choosing_offer_category = State()
    writing_offer_name = State()
    writing_specification = State()
    entering_canvas_link = State()
    uploading_multiple_zip_files = State()

    # Уникализация изображений
    images_unicalization = State()
    unicalization_copies = State()

    # Рассылка
    broadcast_collecting = State()

    # Пиксель система
    entering_pixel_id = State()
    entering_pixel_key = State()

    # Перевод лендинга
    choosing_target_language = State()
    choosing_target_country = State()
    entering_landing_id = State()
    entering_offer_details = State()

    # Получение расхода по байеру (для админов)
    entering_buyer_id = State()

    # Получение SMS для Google Ads
    waiting_for_phone_query = State()
    waiting_for_sms_count = State()
    waiting_for_sms_request = State()

    # Покупка номеров (для админов)
    entering_numbers_quantity = State()

