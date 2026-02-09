"""
Обработчики для покупки номеров телефонов (только для админов/тимлидеров)
"""
import asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import cancel_kb, get_menu_keyboard
from utils import last_messages, delete_last_messages
from config import ADMIN_ID, TEAMLEADER_ID
from services.luboydomen import get_all_phone_numbers, purchase_number

router = Router()

# Задержка между API запросами для избежания rate limit (в секундах)
API_REQUEST_DELAY = 6  # 10 запросов в минуту = 1 запрос каждые 6 секунд минимум

# Максимальная длина сообщения в Telegram
MAX_MESSAGE_LENGTH = 4000

# Фиксированные параметры
COUNTRY_CODE = "GB"
DURATION_MONTHS = 1
AUTO_RENEW = False


def is_admin_or_teamleader(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом или тимлидером"""
    return user_id == ADMIN_ID or user_id == TEAMLEADER_ID


def generate_custom_name() -> str:
    """Генерирует custom_name из текущей даты и времени с миллисекундами"""
    now = datetime.now()
    return now.strftime("%m/%d/%Y_%H:%M:%S.%f")[:-3]


def split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list:
    """Разбивает длинное сообщение на части"""
    if len(text) <= max_length:
        return [text]

    parts = []
    current_part = ""

    for line in text.split("\n"):
        if len(current_part) + len(line) + 1 <= max_length:
            current_part += line + "\n"
        else:
            if current_part:
                parts.append(current_part.strip())
            current_part = line + "\n"

    if current_part:
        parts.append(current_part.strip())

    return parts


@router.message(F.text == "📞 Купить номера")
async def start_purchase_numbers(message: Message, state: FSMContext):
    """Начинает процесс покупки номеров"""
    if not is_admin_or_teamleader(message.from_user.id):
        await message.answer(
            "❌ У вас нет доступа к этой функции.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    m1 = await message.answer(
        "📞 <b>Покупка номеров телефонов (GB)</b>\n\n"
        "Введите количество номеров, которые хотите купить:",
        parse_mode="HTML",
        reply_markup=cancel_kb
    )
    last_messages[message.from_user.id] = [m1.message_id]
    await state.set_state(Form.entering_numbers_quantity)


@router.message(Form.entering_numbers_quantity)
async def process_quantity(message: Message, state: FSMContext):
    """Обрабатывает ввод количества номеров и запускает покупку"""
    if message.text == "❌ Отмена":
        await delete_last_messages(message.from_user.id, message.bot)
        await state.clear()
        await message.answer(
            "Действие отменено. Возвращаю в главное меню ⬅️",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    try:
        quantity = int(message.text.strip())
        if quantity < 1:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Пожалуйста, введите положительное число.",
            reply_markup=cancel_kb
        )
        return

    await delete_last_messages(message.from_user.id, message.bot)

    # Примерное время на покупку (с учетом rate limit)
    estimated_time = quantity * API_REQUEST_DELAY

    # Отправляем сообщение о начале покупки
    progress_msg = await message.answer(
        f"🔄 <b>Покупка номеров началась...</b>\n\n"
        f"🌍 Страна: <b>{COUNTRY_CODE}</b>\n"
        f"📊 Количество: <b>{quantity}</b>\n"
        f"📅 Срок аренды: <b>{DURATION_MONTHS} мес.</b>\n"
        f"⏱️ Примерное время: <b>~{estimated_time} сек.</b>\n\n"
        f"Куплено: <b>0/{quantity}</b>\n"
        f"<i>Пожалуйста, подождите...</i>",
        parse_mode="HTML"
    )

    # Покупаем номера по одному с задержкой
    purchased_numbers = []
    errors = []
    total_cost = 0
    last_purchased_count = 0
    last_errors_count = 0

    for i in range(quantity):
        try:
            # Генерируем уникальное имя с датой и временем
            custom_name = generate_custom_name()

            result = await purchase_number(custom_name, COUNTRY_CODE, DURATION_MONTHS, AUTO_RENEW)

            if result.get("success"):
                numbers = result.get("numbers", [])
                purchased_numbers.extend(numbers)
                total_cost += result.get("cost", 0)
            else:
                error_msg = result.get("error") or result.get("detail") or "Неизвестная ошибка"
                errors.append(f"Номер {i+1}: {error_msg}")

            # Обновляем прогресс только если что-то изменилось
            if len(purchased_numbers) != last_purchased_count or len(errors) != last_errors_count:
                last_purchased_count = len(purchased_numbers)
                last_errors_count = len(errors)
                try:
                    await progress_msg.edit_text(
                        f"🔄 <b>Покупка номеров...</b>\n\n"
                        f"🌍 Страна: <b>{COUNTRY_CODE}</b>\n"
                        f"📊 Количество: <b>{quantity}</b>\n"
                        f"📅 Срок аренды: <b>{DURATION_MONTHS} мес.</b>\n\n"
                        f"Куплено: <b>{len(purchased_numbers)}/{quantity}</b>\n"
                        f"Ошибок: <b>{len(errors)}</b>\n"
                        f"<i>Пожалуйста, подождите...</i>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

            # Задержка между запросами (кроме последнего)
            if i < quantity - 1:
                await asyncio.sleep(API_REQUEST_DELAY)

        except Exception as e:
            errors.append(f"Номер {i+1}: {str(e)}")
            if i < quantity - 1:
                await asyncio.sleep(API_REQUEST_DELAY)

    # Удаляем сообщение о прогрессе
    try:
        await progress_msg.delete()
    except:
        pass

    # Формируем итоговое сообщение
    if purchased_numbers:
        response_text = f"✅ <b>Покупка завершена!</b>\n\n"
        response_text += f"💰 Общая стоимость: <b>{total_cost} кредитов</b>\n"
        response_text += f"📊 Куплено номеров: <b>{len(purchased_numbers)}</b>\n\n"

        await message.answer(response_text, parse_mode="HTML")

        # Формируем список номеров
        numbers_text = f"<b>━━━ Купленные номера ━━━</b>\n\n"

        for i, number in enumerate(purchased_numbers, 1):
            phone = number.get("phone_number", "N/A")
            piv_id = number.get("piv_num_id", "N/A")
            expires = number.get("expires_at", "N/A")
            num_custom_name = number.get("custom_name", "-")

            if expires and expires != "N/A":
                try:
                    dt = datetime.fromisoformat(expires.replace("+00:00", "+00:00"))
                    expires = dt.strftime("%d.%m.%Y")
                except:
                    pass

            numbers_text += f"<b>#{i}</b>\n"
            numbers_text += f"📞 Номер: <code>{phone}</code>\n"
            numbers_text += f"🆔 ID: <code>{piv_id}</code>\n"
            numbers_text += f"📅 Истекает: {expires}\n"
            if num_custom_name and num_custom_name != "-":
                numbers_text += f"📝 Название: {num_custom_name}\n"
            numbers_text += "\n"

        # Разбиваем на части и отправляем
        parts = split_message(numbers_text)
        for part in parts:
            await message.answer(part, parse_mode="HTML")

        if errors:
            errors_text = f"<b>⚠️ Ошибки ({len(errors)}):</b>\n"
            for error in errors[:5]:
                errors_text += f"• {error}\n"
            if len(errors) > 5:
                errors_text += f"<i>...и еще {len(errors) - 5} ошибок</i>\n"
            await message.answer(errors_text, parse_mode="HTML")
    else:
        response_text = f"❌ <b>Не удалось купить номера</b>\n\n"
        if errors:
            response_text += f"<b>Ошибки:</b>\n"
            for error in errors[:10]:
                response_text += f"• {error}\n"
            if len(errors) > 10:
                response_text += f"<i>...и еще {len(errors) - 10} ошибок</i>\n"
        await message.answer(response_text, parse_mode="HTML")

    await message.answer(
        "Выберите действие:",
        reply_markup=get_menu_keyboard(message.from_user.id)
    )

    await state.clear()


@router.message(F.text == "📋 Список номеров")
async def show_numbers_list(message: Message, state: FSMContext):
    """Показывает список всех номеров"""
    if not is_admin_or_teamleader(message.from_user.id):
        await message.answer(
            "❌ У вас нет доступа к этой функции.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    progress_msg = await message.answer("🔄 Загружаю список номеров...")

    try:
        result = await get_all_phone_numbers()
    except Exception as e:
        try:
            await progress_msg.delete()
        except:
            pass
        await message.answer(
            f"❌ Ошибка при получении списка: {str(e)}",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    try:
        await progress_msg.delete()
    except:
        pass

    if not result.get("success"):
        await message.answer(
            "❌ Не удалось получить список номеров.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    numbers = result.get("data", {}).get("numbers", [])
    total = result.get("data", {}).get("pagination", {}).get("total", len(numbers))

    if not numbers:
        await message.answer(
            "📭 Список номеров пуст.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    # Формируем заголовок
    header = f"📋 <b>Список номеров ({total} шт.)</b>\n\n"
    await message.answer(header, parse_mode="HTML")

    # Формируем список номеров
    numbers_text = ""
    for i, number in enumerate(numbers, 1):
        phone = number.get("phone_number", "N/A")
        piv_id = number.get("piv_num_id", "N/A")
        status = number.get("status", "N/A")
        expires = number.get("expires_at", "N/A")
        custom_name = number.get("custom_name", "-")

        if expires and expires != "N/A":
            try:
                dt = datetime.fromisoformat(expires.replace("+00:00", "+00:00"))
                expires = dt.strftime("%d.%m.%Y")
            except:
                pass

        status_emoji = "🟢" if status == "active" else "🔴"

        numbers_text += f"<b>#{i}</b> {status_emoji}\n"
        numbers_text += f"📞 <code>{phone}</code>\n"
        numbers_text += f"🆔 <code>{piv_id}</code>\n"
        numbers_text += f"📅 До: {expires}\n"
        if custom_name and custom_name != "-":
            numbers_text += f"📝 {custom_name}\n"
        numbers_text += "\n"

    # Разбиваем на части и отправляем
    parts = split_message(numbers_text)
    for part in parts:
        await message.answer(part, parse_mode="HTML")

    await message.answer(
        "Выберите действие:",
        reply_markup=get_menu_keyboard(message.from_user.id)
    )
