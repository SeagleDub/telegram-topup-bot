"""
Обработчики для функции "Действия с картами" (банк eCards, см. API.md).

Поток: последние транзакции по картам байера либо ввод полного номера карты
-> действие с картой (заблокировать / транзакции / 3DS-код).

Расход по группе живёт отдельным пунктом меню (handlers/card_group_expenses.py).

Смены лимита нет: в API eCards такого эндпоинта не существует.

Номера карт выводятся маскированно (последние 4 цифры).
"""
import logging

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import (
    card_flow_kb,
    ANOTHER_CARD_TEXT,
    get_menu_keyboard,
    get_card_action_keyboard,
    get_card_block_confirm_keyboard,
    get_ecards_group_keyboard,
    get_tx_pagination_keyboard,
)
import math

# Все состояния флоу "Действия с картами" (для StateFilter)
CARD_STATES = (
    Form.card_actions_enter_number,
    Form.card_actions_choose_action,
    Form.card_actions_confirm_block,
)
from utils import last_messages, delete_last_messages
import services.ecards as ecards

logger = logging.getLogger(__name__)

router = Router()

MAX_TRANSACTIONS = 10

# Сколько последних операций по группе тянуть (API отдаёт до 100/страницу) и
# сколько показывать на одной странице листалки (◀ ▶).
ECARDS_GROUP_TX_LIMIT = 50
ECARDS_TX_PAGE = 5


# --------------------------------------------------------------------------- #
# Хелперы
# --------------------------------------------------------------------------- #
def mask_card_number(number) -> str:
    """Маскирует номер карты, оставляя последние 4 цифры."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    if len(digits) < 4:
        return "****"
    return f"**** **** **** {digits[-4:]}"


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _is_error(result) -> bool:
    """Признак ошибочного ответа сервиса (успех GET /card — коллекция карт)."""
    return isinstance(result, dict) and (bool(result.get("error")) or result.get("success") is False)


def get_card_id(card: dict):
    """ID карты."""
    return ecards.card_id(card)


def get_card_number(card: dict):
    """Полный номер карты."""
    return ecards.card_number(card)


def _has(value) -> bool:
    """Есть ли осмысленное значение (не None и не пустая строка). 0 — есть."""
    return value not in (None, "")


def format_card_summary(card: dict) -> str:
    """Карточка для показа. Поля без значения (None/пусто) пропускаются.

    Чувствительные данные (CVC, 3DS-пароль, token, полный номер, email
    владельца) не выводятся.
    """
    lines = ["💳 <b>Карта найдена</b>", "Банк: eCards"]
    lines.append(f"Номер: <code>{mask_card_number(get_card_number(card))}</code>")

    status = card.get("status")
    if _has(status):
        # Имена статусов eCards в API.md не описаны — показываем как есть.
        lines.append(f"Статус: {status}")

    used = card.get("sharedBalanceUsed")
    if _has(used):
        currency = str(card.get("currency", "")).upper()
        cur = f" {currency}" if currency else ""
        lines.append(f"Использовано: <b>{used}</b>{cur}")

    return "\n".join(lines)


async def _buyer_group_ids(user_id: int) -> list | None:
    """ID групп байера. None — не удалось получить (ошибка API)."""
    groups = await ecards.get_buyer_groups(user_id)
    if _is_error(groups):
        logger.error("[card_actions] не удалось получить группы: user_id=%s", user_id)
        return None
    return [gid for gid in (ecards.group_id(g) for g in groups) if gid is not None]


async def _find_card(number: str, group_ids: list):
    """Ищет карту только среди групп байера (серверный фильтр)."""
    return await ecards.find_card_by_number(number, group_ids=group_ids)


async def _assert_card_access(card_id, user_id: int) -> bool:
    """Подтверждает право на действие с картой в момент действия.

    Проверки при поиске недостаточно: card_id хранится в FSM state и переживает
    кнопку «Другая карта», перезапуск флоу и любой другой путь, которым в state
    может оказаться чужой идентификатор.
    """
    group_ids = await _buyer_group_ids(user_id)
    if not group_ids:
        logger.warning("[card_actions] отказ в доступе: у user_id=%s нет групп", user_id)
        return False
    allowed = await ecards.card_in_groups(card_id, group_ids)
    if not allowed:
        logger.warning(
            "[card_actions] ОТКАЗ В ДОСТУПЕ: user_id=%s пытался действовать с card_id=%s "
            "вне своих групп %s", user_id, card_id, group_ids,
        )
    return allowed


async def _deny_card_access(target, user_id: int, state: FSMContext) -> None:
    """Отказ в доступе к карте: чистим state и возвращаем в меню."""
    await state.clear()
    await target.answer(
        "❌ Эта карта вам недоступна.",
        reply_markup=get_menu_keyboard(user_id),
    )


async def _show_action_menu(target, user_id: int, state: FSMContext, card_number) -> None:
    """Показывает меню действий по выбранной карте и возвращает в состояние выбора.

    Карта (card_id/card_number) остаётся в state — можно сделать ещё одно
    действие без повторного поиска.
    """
    masked = mask_card_number(card_number)
    m1 = await target.answer(
        f"💳 Карта <code>{masked}</code>\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=get_card_action_keyboard(),
    )
    m2 = await target.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=card_flow_kb)
    last_messages[user_id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_choose_action)


# --------------------------------------------------------------------------- #
# Шаг 1. Запуск
# --------------------------------------------------------------------------- #
@router.message(F.text == "💳 Действия с картами (ecards)")
async def start_card_actions(message: Message, state: FSMContext):
    """Начинает флоу: действия по картам байера либо ввод номера карты.

    Остаёмся в состоянии ввода номера — набранный текст обрабатывается как
    номер карты, кнопки — как колбэки card_group:*.
    """
    await state.set_data({})
    m1 = await message.answer(
        "💳 <b>Действия с картами</b>\n\nВыберите действие по вашим картам "
        "или введите полный номер карты:",
        parse_mode="HTML",
        reply_markup=get_ecards_group_keyboard(),
    )
    m2 = await message.answer("Или введите номер карты:", reply_markup=card_flow_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_enter_number)


# --------------------------------------------------------------------------- #
# Шаг 2. «Другая карта» — сброс выбранной карты
# --------------------------------------------------------------------------- #
@router.message(StateFilter(*CARD_STATES), F.text == ANOTHER_CARD_TEXT)
async def card_actions_another(message: Message, state: FSMContext):
    """Кнопка «Другая карта» — сбрасывает выбранную карту и возвращает к вводу.

    Зарегистрирован раньше обработчика ввода номера, чтобы текст кнопки не
    воспринимался как ввод. Фильтр по состояниям флоу — чтобы не срабатывать
    вне него.
    """
    await delete_last_messages(message.from_user.id, message.bot)
    await state.set_data({})
    await _ecards_show_group_menu(message, message.from_user.id, state)


# --------------------------------------------------------------------------- #
# eCards: действия по картам байера (технически по его группе, tg_id в названии)
# --------------------------------------------------------------------------- #
async def _safe_delete(msg) -> None:
    try:
        await msg.delete()
    except Exception:
        pass


async def _ecards_show_group_menu(target, user_id: int, state: FSMContext) -> None:
    """Пере-показывает меню действий eCards (транзакции) + ввод номера."""
    m1 = await target.answer(
        "🏦 <b>eCards</b>\n\nВыберите действие или введите номер карты:",
        parse_mode="HTML",
        reply_markup=get_ecards_group_keyboard(),
    )
    m2 = await target.answer("Или введите номер карты:", reply_markup=card_flow_kb)
    last_messages[user_id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_enter_number)


async def _resolve_buyer_groups(target, user_id: int, state: FSMContext):
    """Группы байера по tg_id. При ошибке/пустоте сообщает и пере-показывает меню.

    Возвращает список пар (id, name) либо None (уже обработано).
    """
    groups = await ecards.get_buyer_groups(user_id)
    if _is_error(groups):
        await target.answer("❌ Не удалось получить список групп. Попробуйте позже.")
        await _ecards_show_group_menu(target, user_id, state)
        return None

    my_groups = [(ecards.group_id(g), ecards.group_name(g) or f"Группа {ecards.group_id(g)}")
                 for g in groups if ecards.group_id(g) is not None]
    if not my_groups:
        await target.answer(
            "📭 Для вас не найдено групп карт. Обратитесь к администратору "
            "(в названии группы должен быть ваш Telegram-ID)."
        )
        await _ecards_show_group_menu(target, user_id, state)
        return None
    return my_groups


async def _run_group_transactions(target, user_id: int, state: FSMContext) -> None:
    """Последние операции по группам байера (до ECARDS_GROUP_TX_LIMIT).

    Период не ограничивается — отдаются просто последние операции.
    """
    progress = await target.answer("🔄 Загружаю транзакции...")
    my_groups = await _resolve_buyer_groups(target, user_id, state)
    if my_groups is None:
        await _safe_delete(progress)
        return

    operations = []
    failed = False
    for gid, _ in my_groups:
        result = await ecards.get_card_operations(
            group_ids=[gid], limit=ECARDS_GROUP_TX_LIMIT
        )
        if _is_error(result):
            failed = True
            break
        operations.extend(ecards._as_list(result))
    await _safe_delete(progress)

    if failed:
        await target.answer("❌ Не удалось получить транзакции. Попробуйте позже.")
        await _ecards_show_group_menu(target, user_id, state)
        return

    operations.sort(key=lambda o: str(ecards.op_date(o) or ""), reverse=True)
    operations = operations[:ECARDS_GROUP_TX_LIMIT]

    if not operations:
        await target.answer("📭 Транзакций по вашим картам не найдено.")
        await _ecards_show_group_menu(target, user_id, state)
        return

    blocks = [_format_transaction("ecards", i, tx) for i, tx in enumerate(operations, 1)]
    header = "📜 <b>Последние транзакции</b>\n"
    # Кладём страницы в state — навигация стрелками редактирует это же сообщение.
    await state.update_data(tx_blocks=blocks, tx_header=header)
    text, page, pages = _render_tx_page(blocks, 0, header)
    await target.answer(text, parse_mode="HTML",
                        reply_markup=get_tx_pagination_keyboard(page, pages))
    await _ecards_show_group_menu(target, user_id, state)


def _render_tx_page(blocks: list, page: int, header: str):
    """Собирает текст страницы транзакций. Возвращает (text, page, pages)."""
    pages = max(1, math.ceil(len(blocks) / ECARDS_TX_PAGE))
    page = max(0, min(page, pages - 1))
    chunk = blocks[page * ECARDS_TX_PAGE:(page + 1) * ECARDS_TX_PAGE]
    text = f"{header}Стр. {page + 1}/{pages}\n\n" + "\n".join(chunk)
    return text, page, pages


@router.callback_query(F.data.startswith("txpage:"))
async def tx_page_nav(query: CallbackQuery, state: FSMContext):
    """Листание страниц транзакций стрелками (редактирует сообщение)."""
    part = query.data.split(":", 1)[1]
    if part == "noop":
        await query.answer()
        return

    data = await state.get_data()
    blocks = data.get("tx_blocks")
    if not blocks:
        await query.answer("Список устарел — откройте транзакции заново.", show_alert=True)
        return

    try:
        target_page = int(part)
    except ValueError:
        await query.answer()
        return

    text, page, pages = _render_tx_page(blocks, target_page, data.get("tx_header", ""))
    try:
        await query.message.edit_text(
            text, parse_mode="HTML", reply_markup=get_tx_pagination_keyboard(page, pages)
        )
    except Exception:
        pass
    await query.answer()


@router.callback_query(F.data == "card_group:transactions", Form.card_actions_enter_number)
async def ecards_group_action(query: CallbackQuery, state: FSMContext):
    """Последние транзакции по картам байера."""
    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)
    await _run_group_transactions(query.message, query.from_user.id, state)


# --------------------------------------------------------------------------- #
# Шаг 3. Ввод номера карты и поиск
# --------------------------------------------------------------------------- #
@router.message(Form.card_actions_enter_number)
async def card_number_entered(message: Message, state: FSMContext):
    """Ищет карту по введённому полному номеру."""
    # "❌ Отмена" перехватывается глобальным обработчиком в common.py
    number = message.text.strip()
    if not any(ch.isdigit() for ch in number):
        await message.answer("❌ Введите номер карты (цифры).", reply_markup=card_flow_kb)
        return

    progress = await message.answer("🔄 Ищу карту...")

    group_ids = await _buyer_group_ids(message.from_user.id)
    if group_ids is None:
        await _safe_delete(progress)
        await message.answer(
            "❌ Не удалось проверить доступ к картам. Попробуйте позже.",
            reply_markup=card_flow_kb,
        )
        return
    if not group_ids:
        await _safe_delete(progress)
        await message.answer(
            "📭 Для вас не найдено групп карт. Обратитесь к администратору "
            "(в названии группы должен быть ваш Telegram-ID).",
            reply_markup=card_flow_kb,
        )
        return

    result = await _find_card(number, group_ids)
    await _safe_delete(progress)

    if isinstance(result, dict) and result.get("error"):
        await message.answer(
            "❌ Не удалось получить список карт. Попробуйте позже.",
            reply_markup=card_flow_kb,
        )
        return

    if not result:
        await message.answer(
            "❌ Карта с таким номером не найдена среди ваших карт. "
            "Проверьте номер и попробуйте снова.",
            reply_markup=card_flow_kb,
        )
        return

    card = result["card"]
    await delete_last_messages(message.from_user.id, message.bot)
    await state.update_data(card_id=get_card_id(card), card_number=get_card_number(card))

    text = format_card_summary(card)
    if result.get("multiple"):
        text += "\n\n⚠️ Найдено несколько карт, показана первая."

    m1 = await message.answer(text, parse_mode="HTML", reply_markup=get_card_action_keyboard())
    m2 = await message.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=card_flow_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_choose_action)


# --------------------------------------------------------------------------- #
# Шаг 4. Выбор действия
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("card_action:"), Form.card_actions_choose_action)
async def card_action_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор действия с картой."""
    action = query.data.split(":", 1)[1]
    data = await state.get_data()
    card_id = data.get("card_id")

    if card_id is None:
        await query.answer()
        await query.message.answer(
            "❌ Карта не выбрана. Начните заново.",
            reply_markup=get_menu_keyboard(query.from_user.id),
        )
        await state.clear()
        return

    # Право на действие подтверждаем здесь, а не полагаемся на проверку при
    # поиске: card_id пришёл из state и мог там оказаться другим путём.
    if not await _assert_card_access(card_id, query.from_user.id):
        await query.answer()
        await _deny_card_access(query.message, query.from_user.id, state)
        return

    if action == "block":
        await query.answer()
        await delete_last_messages(query.from_user.id, query.message.bot)
        m1 = await query.message.answer(
            f"🚫 Заблокировать карту <code>{mask_card_number(data.get('card_number'))}</code>?\n"
            "Действие необратимо.",
            parse_mode="HTML",
            reply_markup=get_card_block_confirm_keyboard(),
        )
        last_messages[query.from_user.id] = [m1.message_id]
        await state.set_state(Form.card_actions_confirm_block)

    elif action == "transactions":
        await query.answer()
        await _show_transactions(query, state, card_id, data.get("card_number"))

    elif action == "otp":
        await query.answer()
        await _show_card_3ds(query, state, card_id, data.get("card_number"))

    else:
        await query.answer("❌ Неизвестное действие", show_alert=True)


# --------------------------------------------------------------------------- #
# 3DS-код (eCards)
# --------------------------------------------------------------------------- #
async def _show_card_3ds(query: CallbackQuery, state: FSMContext, card_id, card_number):
    """Показывает последний 3DS-код по карте из ленты уведомлений eCards."""
    await delete_last_messages(query.from_user.id, query.message.bot)
    progress = await query.message.answer("🔄 Получаю 3DS код...")
    result = await ecards.find_latest_3ds(card_id)
    await _safe_delete(progress)

    if _is_error(result):
        await query.message.answer("❌ Не удалось получить 3DS код. Попробуйте позже.")
        await _show_action_menu(query.message, query.from_user.id, state, card_number)
        return

    if not result:
        await query.message.answer("📭 3DS код для этой карты не найден.")
        await _show_action_menu(query.message, query.from_user.id, state, card_number)
        return

    otp = result.get("otpCode") or "—"
    currency = str(result.get("currency", "")).upper()
    cur = f" {currency}" if currency else ""
    lines = [
        "🔐 <b>3DS код</b>",
        f"Карта: <code>{mask_card_number(result.get('cardNumber') or card_number)}</code>",
        f"Код: <code>{otp}</code>",
    ]
    if _has(result.get("amount")):
        lines.append(f"Сумма: <b>{result.get('amount')}</b>{cur}")
    if _has(result.get("merchant")):
        lines.append(f"🏬 {result.get('merchant')}")
    if _has(result.get("createdAt")):
        lines.append(f"Время: {_pretty_dt(result.get('createdAt'))}")

    await query.message.answer("\n".join(lines), parse_mode="HTML")
    await _show_action_menu(query.message, query.from_user.id, state, card_number)


# --------------------------------------------------------------------------- #
# Транзакции
# --------------------------------------------------------------------------- #
async def _show_transactions(query: CallbackQuery, state: FSMContext, card_id, card_number):
    """Загружает транзакции и выводит последние по выбранной карте."""
    await delete_last_messages(query.from_user.id, query.message.bot)
    progress = await query.message.answer("🔄 Загружаю транзакции...")

    start, end = ecards.current_month_period()
    result = await ecards.get_card_operations(start, end, card_ids=[card_id])

    await _safe_delete(progress)

    if _is_error(result):
        await query.message.answer("❌ Не удалось получить транзакции. Попробуйте позже.")
        await _show_action_menu(query.message, query.from_user.id, state, card_number)
        return

    transactions = ecards._as_list(result)
    transactions = [tx for tx in transactions if _tx_matches(tx, card_id, card_number)]

    if not transactions:
        await query.message.answer("📭 Транзакций по карте за период не найдено.")
        await _show_action_menu(query.message, query.from_user.id, state, card_number)
        return

    lines = ["📜 <b>Последние транзакции</b>\n"]
    for i, tx in enumerate(transactions[:MAX_TRANSACTIONS], 1):
        lines.append(_format_transaction(i, tx))

    await query.message.answer("\n".join(lines), parse_mode="HTML")
    await _show_action_menu(query.message, query.from_user.id, state, card_number)


def _tx_matches(tx: dict, card_id, card_number) -> bool:
    """Транзакция относится к выбранной карте — по id, иначе по последним 4 цифрам.

    eCards уже фильтрует по filterCardId на стороне API, но сверяемся повторно.
    Карта лежит во вложенном объекте операции (op.card.{id,cardNumber}).
    """
    if card_id is not None:
        tx_id = ecards.op_card_id(tx)
        if tx_id is not None:
            return str(tx_id) == str(card_id)
    last4 = _digits(card_number)[-4:]
    tx_digits = _digits(ecards.op_card_number(tx))
    return bool(last4) and bool(tx_digits) and tx_digits[-4:] == last4


def _pretty_dt(value) -> str:
    """ISO 8601 -> 'YYYY-MM-DD HH:MM'; иначе как есть."""
    s = str(value or "")
    if "T" in s:
        return s[:16].replace("T", " ")
    return s


def _format_transaction(i: int, tx: dict) -> str:
    """Подробная строка операции; пустые поля пропускаются.

    Поля операции из /card-operation: сумма в value, валюта берётся у карты.
    """
    currency = str(ecards.op_currency(tx) or "").upper()
    cur = f" {currency}" if currency else ""
    amount = ecards.op_value(tx)
    date = _pretty_dt(ecards.op_date(tx))
    head = ecards.op_type(tx) or "—"
    merchant = ecards.op_merchant(tx)

    lines = [f"<b>#{i}</b> {date}".rstrip()]
    lines.append(f"   {head} — <b>{amount if _has(amount) else '—'}</b>{cur}")
    if _has(merchant):
        lines.append(f"   🏬 {merchant}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Шаг 5. Подтверждение блокировки
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("card_block_confirm:"), Form.card_actions_confirm_block)
async def card_block_confirmed(query: CallbackQuery, state: FSMContext):
    """Обрабатывает подтверждение/отмену блокировки карты."""
    choice = query.data.split(":", 1)[1]
    data = await state.get_data()
    card_id = data.get("card_id")
    card_number = data.get("card_number")
    menu_kb = get_menu_keyboard(query.from_user.id)

    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)

    if card_id is None:
        await query.message.answer("❌ Карта не выбрана. Начните заново.", reply_markup=menu_kb)
        await state.clear()
        return

    masked = mask_card_number(card_number)

    if choice != "yes":
        await query.message.answer("Блокировка отменена.")
        await _show_action_menu(query.message, query.from_user.id, state, card_number)
        return

    # Блокировка необратима — подтверждаем право ещё раз, непосредственно
    # перед вызовом API, а не только на шаге выбора действия.
    if not await _assert_card_access(card_id, query.from_user.id):
        await _deny_card_access(query.message, query.from_user.id, state)
        return

    progress = await query.message.answer("🔄 Блокирую карту...")
    result = await ecards.block_card(card_id)
    await _safe_delete(progress)

    if _is_error(result):
        await query.message.answer("❌ Не удалось заблокировать карту. Попробуйте позже.")
    else:
        # Форма успешного ответа /card/close в API.md не описана; отсутствие
        # ошибки считаем подтверждением блокировки.
        await query.message.answer(
            f"✅ Карта <code>{masked}</code> заблокирована.",
            parse_mode="HTML",
        )

    await _show_action_menu(query.message, query.from_user.id, state, card_number)
