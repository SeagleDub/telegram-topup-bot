"""
Синхронизация вайтлиста в Cloudflare KV.

Загрузка видео идёт браузером мимо бота, поэтому AuthMiddleware её не видит:
проверять право доступа приходится Worker'у. До Google Sheets Worker не
дотянется (а класть в него гугловые креды не следует), до хоста — тем более,
входящих соединений туда нет.

Поэтому источник истины остаётся прежним — та же таблица, что читает
utils.get_whitelist, — а в KV кладётся его копия. Второго списка не заводим:
два независимых ответа на вопрос «кому можно» рано или поздно разойдутся.
"""
import asyncio
import logging

import aiohttp
import bugsnag

from config import CF_WORKER_URL, KV_SYNC_TOKEN
from utils import WhitelistUnavailable, get_whitelist

logger = logging.getLogger(__name__)

# Совпадает с WHITELIST_TTL в utils: чаще ходить смысла нет, кэш всё равно
# отдаст прежнее значение.
SYNC_INTERVAL_SEC = 300

# Пауза после ошибки. Короче основного интервала: расхождение вайтлиста —
# это либо закрытый доступ работающему человеку, либо открытый уволенному.
RETRY_INTERVAL_SEC = 60

REQUEST_TIMEOUT_SEC = 20


class WhitelistSyncError(Exception):
    """Не удалось положить вайтлист в KV."""


async def push_whitelist_once() -> int:
    """Читает вайтлист и кладёт его в KV. Возвращает число записанных ID."""
    if not CF_WORKER_URL or not KV_SYNC_TOKEN:
        raise WhitelistSyncError(
            "не заданы CF_WORKER_URL или KV_SYNC_TOKEN — синхронизация невозможна"
        )

    try:
        ids = sorted(get_whitelist())
    except WhitelistUnavailable as e:
        raise WhitelistSyncError(f"вайтлист недоступен: {e}") from e

    # Пустой список в KV закрыл бы доступ всем. Worker такое отклоняет, но
    # отправлять заведомо негодное значение всё равно не будем — и скажем об
    # этом громко: пустая таблица почти наверняка означает сбой настройки.
    if not ids:
        raise WhitelistSyncError(
            "вайтлист пуст — синхронизация отменена, иначе Mini App закроется для всех"
        )

    url = f"{CF_WORKER_URL.rstrip('/')}/api/whitelist"
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            url,
            json={"ids": ids},
            headers={"Authorization": f"Bearer {KV_SYNC_TOKEN}"},
        ) as response:
            body = await response.text()
            if response.status != 200:
                raise WhitelistSyncError(
                    f"Worker ответил {response.status}: {body[:300]}"
                )

    logger.info("[whitelist-sync] в KV записано ID: %s", len(ids))
    return len(ids)


async def run_whitelist_sync() -> None:
    """Фоновая задача: периодически обновляет вайтлист в KV.

    Первый проход делается сразу при старте: без него Mini App будет отвечать
    отказом всем до первого срабатывания таймера.
    """
    logger.info("[whitelist-sync] запущена, интервал %s с", SYNC_INTERVAL_SEC)

    while True:
        try:
            await push_whitelist_once()
            delay = SYNC_INTERVAL_SEC
        except asyncio.CancelledError:
            logger.info("[whitelist-sync] остановлена")
            raise
        except Exception as e:
            # Молчать нельзя: расхождение вайтлиста внешне никак не проявится,
            # пока кто-нибудь не пожалуется, что Mini App его не пускает.
            logger.error(
                "[whitelist-sync] не удалось обновить KV (%s: %s). Повтор через %s с.",
                type(e).__name__, e, RETRY_INTERVAL_SEC,
            )
            bugsnag.notify(e)
            delay = RETRY_INTERVAL_SEC

        await asyncio.sleep(delay)
