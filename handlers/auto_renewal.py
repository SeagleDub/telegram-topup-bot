"""
Обработчики для включения/выключения автопродления номеров (только для админа и тимлидера)
"""
import asyncio
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import cancel_kb, get_menu_keyboard
from utils import last_messages, delete_last_messages
from config import ADMIN_ID, TEAMLEADER_ID
from services.luboydomen import get_all_phone_numbers, toggle_auto_renewal

router = Router()
logger = logging.getLogger(__name__)

# Задержка между API запросами для избежания rate limit (в секундах)
API_REQUEST_DELAY = 6

# Максимальная длина сообщения в Telegram
MAX_MESSAGE_LENGTH = 4000


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


def find_numbers_by_queries(queries: list[str], all_numbers: list[dict]) -> tuple[list[dict], list[str]]:
    """Ищет номера по списку запросов (номер телефона или custom_name).
    Возвращает (найденные_номера, ненайденные_запросы)."""
    found = []
    not_found = []
    found_ids = set()

    for query in queries:
        query_lower = query.lower().strip()
        query_clean = query_lower.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

        matched = False
        for number in all_numbers:
            if number["piv_num_id"] in found_ids:
                continue

            phone = number.get("phone_number", "").replace(" ", "").replace("-", "")
            custom_name = number.get("custom_name", "").lower()

            # Поиск по номеру телефона
            if query_clean and (query_clean in phone or phone.endswith(query_clean)):
                found.append(number)
                found_ids.add(number["piv_num_id"])
                matched = True
                break

            # Поиск по custom_name
            if query_lower and (query_lower == custom_name or query_lower in custom_name):
                found.append(number)
                found_ids.add(number["piv_num_id"])
                matched = True
                break

        if not matched:
            not_found.append(query)

    return found, not_found


@router.message(F.text == "🔄 Автопродление номеров")
async def start_auto_renewal(message: Message, state: FSMContext):
    """Начинает процесс управления автопродлением (только для админа и тимлидера)"""
    if message.from_user.id != ADMIN_ID and message.from_user.id != TEAMLEADER_ID:
        await message.answer(
            "❌ У вас нет доступа к этой функции.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Включить автопродление", callback_data="auto_renew:enable")],
        [InlineKeyboardButton(text="❌ Выключить автопродление", callback_data="auto_renew:disable")]
    ])

    m1 = await message.answer(
        "🔄 <b>Управление автопродлением номеров</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=kb
    )
    m2 = await message.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.choosing_auto_renewal_action)


@router.message(Form.choosing_auto_renewal_action, F.text == "❌ Отмена")
async def cancel_auto_renewal_action(message: Message, state: FSMContext):
    """Отменяет выбор действия автопродления"""
    await delete_last_messages(message.from_user.id, message.bot)
    await state.clear()
    await message.answer(
        "Действие отменено. Возвращаю в главное меню ⬅️",
        reply_markup=get_menu_keyboard(message.from_user.id)
    )


@router.callback_query(F.data.startswith("auto_renew:"), Form.choosing_auto_renewal_action)
async def choose_auto_renewal_action(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор включения/выключения автопродления"""
    action = query.data.split(":")[1]
    auto_renew = action == "enable"

    await state.update_data(auto_renew=auto_renew)
    await query.answer()

    await delete_last_messages(query.from_user.id, query.message.bot)

    action_text = "включить" if auto_renew else "выключить"

    m1 = await query.message.answer(
        f"🔄 <b>{action_text.capitalize()} автопродление</b>\n\n"
        f"Введите список номеров телефонов или названий (custom_name), "
        f"каждый с новой строки:\n\n"
        f"<i>Примеры:</i>\n"
        f"<code>+447426917510\n"
        f"+447426917511\n"
        f"number3</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb
    )
    last_messages[query.from_user.id] = [m1.message_id]
    await state.set_state(Form.selecting_auto_renewal_numbers)


@router.message(Form.selecting_auto_renewal_numbers, F.text == "❌ Отмена")
async def cancel_auto_renewal_numbers(message: Message, state: FSMContext):
    """Отменяет ввод номеров"""
    await delete_last_messages(message.from_user.id, message.bot)
    await state.clear()
    await message.answer(
        "Действие отменено. Возвращаю в главное меню ⬅️",
        reply_markup=get_menu_keyboard(message.from_user.id)
    )


@router.message(Form.selecting_auto_renewal_numbers)
async def process_auto_renewal_numbers_input(message: Message, state: FSMContext):
    """Обрабатывает ввод списка номеров для автопродления"""
    data = await state.get_data()
    auto_renew = data.get("auto_renew", False)

    # Парсим введённые номера (каждый с новой строки)
    raw_lines = message.text.strip().split("\n")
    queries = [line.strip() for line in raw_lines if line.strip()]

    if not queries:
        await message.answer(
            "❌ Вы не ввели ни одного номера. Попробуйте снова.",
            reply_markup=cancel_kb
        )
        return

    await delete_last_messages(message.from_user.id, message.bot)

    # Загружаем список всех номеров
    progress_msg = await message.answer("🔄 Загружаю список номеров и ищу совпадения...")

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
        await state.clear()
        return

    try:
        await progress_msg.delete()
    except:
        pass

    if not result.get("success"):
        error_detail = result.get("error", "") or result.get("detail", "")
        await message.answer(
            f"❌ Не удалось получить список номеров.\n{error_detail}",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        await state.clear()
        return

    all_numbers = result.get("data", {}).get("numbers", [])

    if not all_numbers:
        await message.answer(
            "📭 Список номеров пуст.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        await state.clear()
        return

    # Ищем номера по введённым запросам
    found_numbers, not_found_queries = find_numbers_by_queries(queries, all_numbers)

    if not found_numbers:
        not_found_text = "\n".join(f"• {q}" for q in not_found_queries)
        m1 = await message.answer(
            f"❌ Ни один номер не найден.\n\n"
            f"<b>Не найдены:</b>\n{not_found_text}\n\n"
            f"Попробуйте ввести номера заново.",
            parse_mode="HTML",
            reply_markup=cancel_kb
        )
        last_messages[message.from_user.id] = [m1.message_id]
        return

    # Сохраняем найденные номера
    await state.update_data(selected_numbers=[
        {
            "piv_num_id": n.get("piv_num_id"),
            "phone_number": n.get("phone_number"),
            "auto_renew": n.get("auto_renew", False),
            "custom_name": n.get("custom_name", ""),
            "status": n.get("status", ""),
        }
        for n in found_numbers
    ])

    action_text = "включить" if auto_renew else "выключить"

    # Формируем подтверждение
    confirm_text = f"🔄 <b>{action_text.capitalize()} автопродление для {len(found_numbers)} номеров:</b>\n\n"

    for number in found_numbers:
        phone = number.get("phone_number", "N/A")
        custom_name = number.get("custom_name", "")
        current_auto = "✅" if number.get("auto_renew") else "❌"
        status = number.get("status", "")
        status_emoji = "🟢" if status == "active" else "🔴"
        name_text = f" ({custom_name})" if custom_name else ""
        confirm_text += f"{status_emoji} <code>{phone}</code>{name_text} — сейчас: {current_auto}\n"

    if not_found_queries:
        confirm_text += f"\n⚠️ <b>Не найдены ({len(not_found_queries)}):</b>\n"
        for q in not_found_queries:
            confirm_text += f"• {q}\n"

    confirm_text += f"\nНажмите кнопку ниже, чтобы запустить процесс."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🚀 {action_text.capitalize()} автопродление",
            callback_data="auto_renew_confirm"
        )]
    ])

    parts = split_message(confirm_text)
    msg_ids = []
    for i, part in enumerate(parts):
        markup = kb if i == len(parts) - 1 else None
        m = await message.answer(part, parse_mode="HTML", reply_markup=markup)
        msg_ids.append(m.message_id)

    m_cancel = await message.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    msg_ids.append(m_cancel.message_id)
    last_messages[message.from_user.id] = msg_ids

    await state.set_state(Form.confirming_auto_renewal)


@router.message(Form.confirming_auto_renewal, F.text == "❌ Отмена")
async def cancel_auto_renewal_confirm(message: Message, state: FSMContext):
    """Отменяет подтверждение автопродления"""
    await delete_last_messages(message.from_user.id, message.bot)
    await state.clear()
    await message.answer(
        "Действие отменено. Возвращаю в главное меню ⬅️",
        reply_markup=get_menu_keyboard(message.from_user.id)
    )


@router.callback_query(F.data == "auto_renew_confirm", Form.confirming_auto_renewal)
async def execute_auto_renewal(query: CallbackQuery, state: FSMContext):
    """Запускает процесс изменения автопродления"""
    data = await state.get_data()
    auto_renew = data.get("auto_renew", False)
    selected_numbers = data.get("selected_numbers", [])

    if not selected_numbers:
        await query.answer("❌ Список номеров пуст", show_alert=True)
        await state.clear()
        return

    await query.answer("🚀 Запускаю...")

    await delete_last_messages(query.from_user.id, query.message.bot)

    action_text = "Включение" if auto_renew else "Выключение"
    estimated_time = len(selected_numbers) * API_REQUEST_DELAY

    progress_msg = await query.message.answer(
        f"🔄 <b>{action_text} автопродления...</b>\n\n"
        f"📊 Номеров: <b>{len(selected_numbers)}</b>\n"
        f"⏱️ Примерное время: <b>~{estimated_time} сек.</b>\n\n"
        f"Обработано: <b>0/{len(selected_numbers)}</b>\n"
        f"<i>Пожалуйста, подождите...</i>",
        parse_mode="HTML"
    )

    success_list = []
    error_list = []

    for i, number in enumerate(selected_numbers):
        try:
            result = await toggle_auto_renewal(number["piv_num_id"], auto_renew)
            logger.info(f"[auto_renewal] number={number['phone_number']} "
                        f"piv_num_id={number['piv_num_id']} "
                        f"auto_renew={auto_renew} result={result}")

            # Проверяем ошибки
            if result.get("error") or result.get("success") is False:
                error_msg = result.get("error") or result.get("detail") or result.get("details") or str(result)
                error_list.append(f"{number['phone_number']}: {error_msg}")
                logger.warning(f"[auto_renewal] FAILED for {number['phone_number']}: {result}")
            elif result.get("success") is True:
                # Дополнительно проверяем, что auto_renew реально изменился в ответе
                response_data = result.get("data", {})
                actual_auto_renew = response_data.get("auto_renew")
                if actual_auto_renew is not None and actual_auto_renew != auto_renew:
                    error_list.append(
                        f"{number['phone_number']}: API вернул success, но auto_renew={actual_auto_renew} "
                        f"(ожидали {auto_renew})"
                    )
                    logger.warning(f"[auto_renewal] MISMATCH for {number['phone_number']}: "
                                   f"expected auto_renew={auto_renew}, got {actual_auto_renew}")
                else:
                    success_list.append(number)
            else:
                # Неизвестный формат ответа
                error_list.append(f"{number['phone_number']}: Неожиданный ответ: {str(result)[:200]}")
                logger.warning(f"[auto_renewal] UNEXPECTED RESPONSE for {number['phone_number']}: {result}")
        except Exception as e:
            error_list.append(f"{number['phone_number']}: {str(e)}")
            logger.exception(f"[auto_renewal] EXCEPTION for {number['phone_number']}")

        # Обновляем прогресс
        try:
            await progress_msg.edit_text(
                f"🔄 <b>{action_text} автопродления...</b>\n\n"
                f"📊 Номеров: <b>{len(selected_numbers)}</b>\n\n"
                f"Обработано: <b>{i + 1}/{len(selected_numbers)}</b>\n"
                f"✅ Успешно: <b>{len(success_list)}</b>\n"
                f"❌ Ошибок: <b>{len(error_list)}</b>\n"
                f"<i>Пожалуйста, подождите...</i>",
                parse_mode="HTML"
            )
        except Exception:
            pass

        # Задержка между запросами (кроме последнего)
        if i < len(selected_numbers) - 1:
            await asyncio.sleep(API_REQUEST_DELAY)

    # Удаляем сообщение о прогрессе
    try:
        await progress_msg.delete()
    except:
        pass

    # Формируем итоговое сообщение
    action_done = "включено" if auto_renew else "выключено"

    if success_list:
        response = f"✅ <b>Автопродление {action_done} для {len(success_list)} номеров:</b>\n\n"
        for number in success_list:
            name_text = f" ({number['custom_name']})" if number.get("custom_name") else ""
            response += f"• <code>{number['phone_number']}</code>{name_text}\n"

        parts = split_message(response)
        for part in parts:
            await query.message.answer(part, parse_mode="HTML")

    if error_list:
        errors_text = f"❌ <b>Ошибки ({len(error_list)}):</b>\n\n"
        for error in error_list[:10]:
            errors_text += f"• {error}\n"
        if len(error_list) > 10:
            errors_text += f"<i>...и еще {len(error_list) - 10} ошибок</i>\n"
        await query.message.answer(errors_text, parse_mode="HTML")

    if not success_list and not error_list:
        await query.message.answer(
            "❌ Не удалось обработать ни одного номера.",
            parse_mode="HTML"
        )

    await query.message.answer(
        "Выберите действие:",
        reply_markup=get_menu_keyboard(query.from_user.id)
    )
    await state.clear()

