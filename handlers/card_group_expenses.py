"""
Обработчик функции «💸 Расход по группе» (банк eCards, см. API.md).

Каждая карта байера лежит в его группе, и **название группы содержит его
Telegram-ID**. Поэтому байер видит расход только по своим картам: бот тянет
список групп (GET /card-group), берёт те, в имени которых как отдельный числовой
токен присутствует его tg_id, суммирует операции этих групп за текущий
календарный месяц (GET /card-operation с filterCardGroupId[]) и выводит нетто по
валютам. Выбора чужих групп нет — приватность обеспечивается матчингом по tg_id.
"""
import re
import logging

from aiogram import Router, F
from aiogram.types import Message

from keyboards import get_menu_keyboard
from utils import is_user_allowed
import services.ecards as ecards

logger = logging.getLogger(__name__)

router = Router()


def _is_error(result) -> bool:
    return isinstance(result, dict) and (bool(result.get("error")) or result.get("success") is False)


def _name_matches_tg(name, tg_id: int) -> bool:
    """True, если tg_id присутствует в имени группы как отдельный числовой токен.

    Матчим по токенам (\\d+), а не по подстроке, чтобы 123 не совпало со 1234.
    """
    needle = str(tg_id)
    return any(tok == needle for tok in re.findall(r"\d+", str(name or "")))


@router.message(F.text == "💸 Расход по группе")
async def show_group_expenses(message: Message):
    """Считает и показывает нетто-расход по группам байера за текущий месяц."""
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    tg_id = message.from_user.id
    menu_kb = get_menu_keyboard(tg_id)

    progress = await message.answer("🔄 Считаю расход за текущий месяц...")
    groups_result = await ecards.get_card_groups()

    if _is_error(groups_result):
        try:
            await progress.delete()
        except Exception:
            pass
        await message.answer(
            "❌ Не удалось получить список групп. Попробуйте позже.",
            reply_markup=menu_kb,
        )
        return

    # Группы байера — те, в имени которых есть его tg_id.
    my_groups = []
    for g in ecards._as_list(groups_result):
        gid = ecards.group_id(g)
        name = ecards.group_name(g)
        if gid is not None and _name_matches_tg(name, tg_id):
            my_groups.append((gid, name or f"Группа {gid}"))

    if not my_groups:
        try:
            await progress.delete()
        except Exception:
            pass
        await message.answer(
            "📭 Для вас не найдено групп карт. Обратитесь к администратору "
            "(в названии группы должен быть ваш Telegram-ID).",
            reply_markup=menu_kb,
        )
        return

    start, end = ecards.current_month_period()
    operations = []
    failed = False
    for gid, _ in my_groups:
        ops_result = await ecards.get_all_group_operations(gid, start, end)
        if _is_error(ops_result):
            failed = True
            break
        operations.extend(ops_result if isinstance(ops_result, list) else [])

    try:
        await progress.delete()
    except Exception:
        pass

    if failed:
        await message.answer(
            "❌ Не удалось получить операции по картам. Попробуйте позже.",
            reply_markup=menu_kb,
        )
        return

    totals = ecards.sum_spend_by_currency(operations)
    group_titles = ", ".join(name for _, name in my_groups)
    period = f"{start[:10]} — {end[:10]}"

    lines = [
        "💸 <b>Расход по вашим картам за текущий период</b>",
        f"Группы: {group_titles}",
        f"Период: {period}",
        "",
    ]
    if not totals:
        lines.append("Расход за период отсутствует.")
    else:
        for currency, amount in sorted(totals.items()):
            # Нетто = списания минус возвраты; округляем до 2 знаков.
            lines.append(f"<b>{round(amount, 2)}</b> {currency}")

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=menu_kb)
