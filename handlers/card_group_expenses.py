"""
Обработчик функции «💸 Расход по группе» (банк eCards, см. API.md).

Каждая карта байера лежит в его группе, и **название группы содержит его
Telegram-ID**. Поэтому байер видит расход только по своим картам: бот тянет
список групп (GET /card-group), берёт те, в имени которых как отдельный числовой
токен присутствует его tg_id, суммирует операции этих групп за выбранный период
(GET /card-operation с filterCardGroupId[]) и выводит нетто по валютам. Выбора
чужих групп нет — приватность обеспечивается матчингом по tg_id.

Период выбирается пользователем: сегодня, вчера, текущий зарплатный период
(4-недельный цикл) или свой диапазон дат.
"""
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import get_menu_keyboard, get_group_expenses_period_keyboard, cancel_kb
from utils import is_user_allowed, last_messages, delete_last_messages
import services.ecards as ecards

logger = logging.getLogger(__name__)

router = Router()

# Пресеты периода → функция, возвращающая (start, end) в UTC-ISO.
_PERIOD_PRESETS = {
    "today": ecards.today_period,
    "yesterday": ecards.yesterday_period,
    "cycle": ecards.current_cycle_period,
}

_PERIOD_LABELS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "cycle": "За зп период",
}


def _is_error(result) -> bool:
    return isinstance(result, dict) and (bool(result.get("error")) or result.get("success") is False)


async def _safe_delete(msg) -> None:
    try:
        await msg.delete()
    except Exception:
        pass


@router.message(F.text == "💸 Расход по группе")
async def start_group_expenses(message: Message, state: FSMContext):
    """Предлагает выбрать период, за который считать расход."""
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    m1 = await message.answer(
        "💸 <b>Расход по вашим картам</b>\n\nВыберите период:",
        parse_mode="HTML",
        reply_markup=get_group_expenses_period_keyboard(),
    )
    m2 = await message.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.group_expenses_choose_period)


@router.callback_query(F.data.startswith("gexp:"), Form.group_expenses_choose_period)
async def group_expenses_period_selected(query: CallbackQuery, state: FSMContext):
    """Пресет → сразу считаем; «Свой диапазон» → просим ввести даты."""
    choice = query.data.split(":", 1)[1]
    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)

    if choice == "custom":
        m1 = await query.message.answer(
            "✍️ Введите период — две даты через пробел:\n"
            "<b>ДД.ММ.ГГГГ ДД.ММ.ГГГГ</b>  (например 01.07.2026 14.07.2026)",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        last_messages[query.from_user.id] = [m1.message_id]
        await state.set_state(Form.group_expenses_enter_period)
        return

    fn = _PERIOD_PRESETS.get(choice)
    if not fn:
        # Неизвестный пресет — состояние остаётся, клавиатуру показываем заново.
        logger.warning("[group_expenses] неизвестный пресет периода: %r (user_id=%s)",
                       choice, query.from_user.id)
        await query.message.answer(
            "❌ Неизвестный период. Выберите из списка:",
            reply_markup=get_group_expenses_period_keyboard(),
        )
        return

    start, end = fn()
    await _run_group_spend(query.message, query.from_user.id, state,
                           start, end, _PERIOD_LABELS.get(choice, ""))


@router.message(Form.group_expenses_enter_period)
async def group_expenses_custom_period(message: Message, state: FSMContext):
    """Парсит введённый диапазон дат и считает расход."""
    # "❌ Отмена" перехватывается глобальным обработчиком в common.py
    parsed = ecards.parse_period(message.text)
    if not parsed:
        await message.answer(
            "❌ Неверный формат. Пример: <b>01.07.2026 14.07.2026</b> (две даты через пробел).",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return

    start, end = parsed
    await delete_last_messages(message.from_user.id, message.bot)
    await _run_group_spend(message, message.from_user.id, state, start, end, "Свой диапазон")


async def _run_group_spend(target, user_id: int, state: FSMContext,
                           start: str, end: str, label: str) -> None:
    """Считает нетто-расход по группам байера за период и выводит результат."""
    menu_kb = get_menu_keyboard(user_id)
    progress = await target.answer("🔄 Считаю расход...")

    groups_result = await ecards.get_buyer_groups(user_id)
    if _is_error(groups_result):
        await _safe_delete(progress)
        await state.clear()
        await target.answer(
            "❌ Не удалось получить список групп. Попробуйте позже.",
            reply_markup=menu_kb,
        )
        return

    my_groups = [(ecards.group_id(g), ecards.group_name(g) or f"Группа {ecards.group_id(g)}")
                 for g in groups_result if ecards.group_id(g) is not None]

    if not my_groups:
        await _safe_delete(progress)
        await state.clear()
        await target.answer(
            "📭 Для вас не найдено групп карт. Обратитесь к администратору "
            "(в названии группы должен быть ваш Telegram-ID).",
            reply_markup=menu_kb,
        )
        return

    operations = []
    failed = False
    for gid, _ in my_groups:
        ops_result = await ecards.get_all_group_operations(gid, start, end)
        if _is_error(ops_result):
            failed = True
            break
        operations.extend(ops_result if isinstance(ops_result, list) else [])

    await _safe_delete(progress)
    await state.clear()

    if failed:
        await target.answer(
            "❌ Не удалось получить операции по картам. Попробуйте позже.",
            reply_markup=menu_kb,
        )
        return

    totals = ecards.sum_spend_by_currency(operations)
    group_titles = ", ".join(name for _, name in my_groups)
    period = f"{ecards.kyiv_date(start)} — {ecards.kyiv_date(end)}"
    period_line = f"Период: {label} ({period})" if label else f"Период: {period}"

    lines = [
        "💸 <b>Расход по вашим картам</b>",
        f"Группы: {group_titles}",
        period_line,
        "",
    ]
    if not totals:
        lines.append("Расход за период отсутствует.")
    else:
        for currency, amount in sorted(totals.items()):
            # Нетто = списания минус возвраты; округляем до 2 знаков.
            lines.append(f"<b>{round(amount, 2)}</b> {currency}")

    await target.answer("\n".join(lines), parse_mode="HTML", reply_markup=menu_kb)
