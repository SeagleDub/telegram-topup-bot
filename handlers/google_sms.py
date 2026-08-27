"""
Обработчики для получения SMS кодов Google Ads
"""
from datetime import datetime
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import cancel_kb, get_menu_keyboard, get_google_sms_keyboard
from utils import last_messages, delete_last_messages
from services.luboydomen import get_all_phone_numbers, get_sms_messages

logger = logging.getLogger(__name__)

router = Router()


async def find_number_by_query(query: str) -> dict | None:
    """Ищет номер по номеру телефона или custom_name"""
    result = await get_all_phone_numbers()

    if not result.get("success") or not result.get("data", {}).get("numbers"):
        return None

    query_lower = query.lower().strip()
    # Удаляем пробелы и дефисы для сравнения номеров
    query_clean = query_lower.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    for number in result["data"]["numbers"]:
        phone = number.get("phone_number", "").replace(" ", "").replace("-", "")
        custom_name = number.get("custom_name", "").lower()

        # Поиск по номеру телефона
        if query_clean in phone or phone.endswith(query_clean):
            return number

        # Поиск по custom_name
        if query_lower == custom_name or query_lower in custom_name:
            return number

    return None


@router.message(F.text == "📱 Получить SMS Google Ads")
async def start_google_sms(message: Message, state: FSMContext):
    """Начинает процесс получения SMS для Google Ads"""
    m1 = await message.answer(
        "📱 <b>Получение SMS для Google Ads</b>\n\n"
        "Введите номер телефона или название номера (custom_name):\n\n"
        "<i>Примеры:</i>\n"
        "• +447426917510\n"
        "• number3",
        parse_mode="HTML",
        reply_markup=cancel_kb
    )
    last_messages[message.from_user.id] = [m1.message_id]
    await state.set_state(Form.waiting_for_phone_query)


@router.message(Form.waiting_for_phone_query)
async def process_phone_query(message: Message, state: FSMContext):
    """Обрабатывает введенный номер или название"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено. Возвращаю в главное меню ⬅️",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    await delete_last_messages(message.from_user.id, message.bot)

    query = message.text.strip()

    # Ищем номер
    m1 = await message.answer("🔍 Ищу номер...", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id]

    try:
        number_data = await find_number_by_query(query)
    except Exception as e:
        logger.exception("[google_sms] поиск номера не удался: query=%r", query)
        await message.answer(
            "❌ Не удалось найти номер. Попробуйте позже.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        await state.clear()
        return

    if not number_data:
        await delete_last_messages(message.from_user.id, message.bot)
        m1 = await message.answer(
            "❌ Номер не найден. Попробуйте ввести другой номер или название.",
            reply_markup=cancel_kb
        )
        last_messages[message.from_user.id] = [m1.message_id]
        return

    await delete_last_messages(message.from_user.id, message.bot)

    # Сохраняем данные о номере
    await state.update_data(
        number_id=number_data["piv_num_id"],
        phone_number=number_data["phone_number"],
        custom_name=number_data.get("custom_name", "")
    )

    # Показываем информацию о номере и просим ввести количество SMS
    custom_name_text = f"\n📝 Название: <b>{number_data.get('custom_name', '-')}</b>" if number_data.get("custom_name") else ""

    m1 = await message.answer(
        f"✅ <b>Номер найден!</b>\n\n"
        f"📞 Номер: <b>{number_data['phone_number']}</b>{custom_name_text}\n"
        f"🌍 Страна: <b>{number_data.get('country_code', '-')}</b>\n"
        f"📊 Статус: <b>{number_data.get('status', '-')}</b>\n\n"
        f"Введите сколько последних SMS показать (от 1 до 10):",
        parse_mode="HTML",
        reply_markup=cancel_kb
    )
    last_messages[message.from_user.id] = [m1.message_id]
    await state.set_state(Form.waiting_for_sms_count)


@router.message(Form.waiting_for_sms_count)
async def process_sms_count(message: Message, state: FSMContext):
    """Обрабатывает ввод количества SMS"""
    if message.text == "❌ Отмена":
        await delete_last_messages(message.from_user.id, message.bot)
        await state.clear()
        await message.answer(
            "Действие отменено. Возвращаю в главное меню ⬅️",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    # Проверяем что введено число от 1 до 10
    try:
        sms_count = int(message.text.strip())
        if sms_count < 1 or sms_count > 10:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Пожалуйста, введите число от 1 до 10.",
            reply_markup=cancel_kb
        )
        return

    await delete_last_messages(message.from_user.id, message.bot)
    await state.update_data(sms_count=sms_count)

    data = await state.get_data()
    phone_number = data.get("phone_number")

    m1 = await message.answer(
        f"📱 <b>Номер: {phone_number}</b>\n"
        f"📊 Показывать: <b>{sms_count}</b> последних SMS\n\n"
        f"Нажмите кнопку ниже, чтобы получить SMS код:",
        parse_mode="HTML",
        reply_markup=get_google_sms_keyboard()
    )
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.waiting_for_sms_request)


@router.callback_query(F.data == "get_google_sms", Form.waiting_for_sms_request)
async def get_google_sms_code(query: CallbackQuery, state: FSMContext):
    """Получает SMS код для Google Ads"""
    data = await state.get_data()
    number_id = data.get("number_id")
    phone_number = data.get("phone_number")
    sms_count = data.get("sms_count", 5)

    if not number_id:
        await query.answer("❌ Ошибка: номер не найден", show_alert=True)
        return

    await query.answer("🔄 Получаю SMS...")

    try:
        sms_result = await get_sms_messages(number_id)
    except Exception as e:
        logger.exception("[google_sms] получение SMS не удалось: number_id=%s", number_id)
        await query.message.answer(
            "❌ Не удалось получить SMS. Попробуйте позже.",
            reply_markup=get_menu_keyboard(query.from_user.id)
        )
        return

    if not sms_result.get("success"):
        await query.message.answer(
            "❌ Не удалось получить SMS. Попробуйте позже.",
            reply_markup=get_google_sms_keyboard()
        )
        return

    messages = sms_result.get("data", {}).get("messages", [])

    if not messages:
        await query.message.answer(
            f"📭 <b>SMS для номера {phone_number} не найдены</b>\n\n"
            "Возможно, сообщение еще не пришло. Попробуйте нажать кнопку еще раз через несколько секунд.",
            parse_mode="HTML",
            reply_markup=get_google_sms_keyboard()
        )
        return

    # Формируем ответ с SMS сообщениями (количество выбирает пользователь)
    response_text = f"📬 <b>SMS для номера {phone_number}:</b>\n\n"

    for i, sms in enumerate(messages[:sms_count], 1):
        verification_code = sms.get("verification_code")
        from_number = sms.get("from_number", "Неизвестно")
        received_at = sms.get("received_at", "")
        message_body = sms.get("message_body", "")

        # Форматируем время
        if received_at:
            try:
                dt = datetime.fromisoformat(received_at.replace("+00:00", "+00:00"))
                time_str = dt.strftime("%d.%m.%Y %H:%M:%S")
            except:
                time_str = received_at
        else:
            time_str = "Неизвестно"

        response_text += f"<b>━━━ SMS #{i} ━━━</b>\n"
        response_text += f"📤 От: <b>{from_number}</b>\n"
        response_text += f"⏰ Время: <b>{time_str}</b>\n"

        if verification_code:
            response_text += f"🔑 <b>КОД: {verification_code}</b>\n"

        response_text += f"💬 Текст: {message_body}\n\n"

    total_sms = sms_result.get("data", {}).get("pagination", {}).get("total", len(messages))
    if total_sms > sms_count:
        response_text += f"<i>Показаны последние {min(sms_count, len(messages))} из {total_sms} сообщений</i>"

    await query.message.answer(
        response_text,
        parse_mode="HTML",
        reply_markup=get_google_sms_keyboard()
    )


@router.message(Form.waiting_for_sms_request)
async def handle_sms_request_text(message: Message, state: FSMContext):
    """Обрабатывает текстовые сообщения в состоянии ожидания SMS"""
    if message.text == "❌ Отмена":
        await delete_last_messages(message.from_user.id, message.bot)
        await state.clear()
        await message.answer(
            "Действие отменено. Возвращаю в главное меню ⬅️",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
