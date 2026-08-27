"""
Ограничение частоты действий.

Зачем: у эндпоинтов, принимающих номер карты или запускающих платные операции,
нет естественного тормоза. Без лимита номер карты подбирается перебором, а
покупка номеров и запрос SMS упираются только в терпение отправителя.

Хранилище — в памяти процесса. Бот однопроцессный (MemoryStorage в main.py),
поэтому этого достаточно. При переходе на несколько инстансов лимит перестанет
быть общим и потребуется вынести счётчики в Redis — здесь этого сознательно нет,
чтобы не тянуть зависимость ради одного процесса.
"""
import logging
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, Tuple

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)

# (лимит действий, окно в секундах)
DEFAULT_LIMIT: Tuple[int, int] = (20, 60)

# Точечные лимиты для чувствительных операций.
# Ключ — имя "бакета", см. _bucket_for.
LIMITS: Dict[str, Tuple[int, int]] = {
    "card_lookup": (5, 60),     # поиск карты по номеру — защита от перебора
    "card_otp": (3, 60),        # выдача 3DS-кода
    "card_block": (3, 60),      # блокировка карты (необратима)
    "purchase": (3, 60),        # покупка номеров — тратит деньги
    "sms": (10, 60),            # запрос SMS
    "group_expenses": (10, 60),  # расход по группе — тяжёлый запрос с пагинацией
}

THROTTLE_TEXT = "⏳ Слишком часто. Подождите немного и повторите."

# user_id -> bucket -> времена последних попаданий
_hits: Dict[int, Dict[str, Deque[float]]] = defaultdict(lambda: defaultdict(deque))

# Тексты кнопок, по которым определяем бакет для message-событий.
_TEXT_BUCKETS = {
    "📞 Купить номера": "purchase",
    "📋 Список номеров": "purchase",
    "📱 Получить SMS Google Ads": "sms",
    "💸 Расход по группе": "group_expenses",
}

# Префиксы callback_data -> бакет.
_CALLBACK_BUCKETS = {
    "card_block_confirm:": "card_block",
    "card_action:otp": "card_otp",
    "gexp:": "group_expenses",
}


def _bucket_for(event: TelegramObject, data: Dict[str, Any]) -> str:
    """Определяет, к какому лимиту относится событие."""
    if isinstance(event, CallbackQuery):
        payload = event.data or ""
        for prefix, bucket in _CALLBACK_BUCKETS.items():
            if payload.startswith(prefix):
                return bucket
        return "default"

    if isinstance(event, Message):
        text = (event.text or "").strip()
        if text in _TEXT_BUCKETS:
            return _TEXT_BUCKETS[text]
        # Ввод номера карты: состояние важнее текста, номер приходит обычным
        # сообщением и по виду не отличается от любого другого ввода.
        state = data.get("raw_state")
        if state and "card_actions_enter_number" in str(state):
            return "card_lookup"
        # Запасной признак на случай, если состояние недоступно: строка из
        # 12+ цифр — это номер карты, и перебор надо тормозить независимо от
        # того, дошло ли до нас состояние FSM.
        if sum(c.isdigit() for c in text) >= 12:
            return "card_lookup"
        return "default"

    return "default"


def _allowed(user_id: int, bucket: str) -> bool:
    limit, window = LIMITS.get(bucket, DEFAULT_LIMIT)
    now = time.monotonic()
    hits = _hits[user_id][bucket]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


class ThrottleMiddleware(BaseMiddleware):
    """Отклоняет события, превысившие лимит частоты."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        bucket = _bucket_for(event, data)
        if _allowed(user.id, bucket):
            return await handler(event, data)

        limit, window = LIMITS.get(bucket, DEFAULT_LIMIT)
        logger.warning(
            "[throttle] лимит превышен: user_id=%s бакет=%s (%s/%sс)",
            user.id, bucket, limit, window,
        )
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(THROTTLE_TEXT, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(THROTTLE_TEXT)
        except Exception as e:
            logger.warning("[throttle] не удалось отправить отказ: %s: %s", type(e).__name__, e)
        return None
