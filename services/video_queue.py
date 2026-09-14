"""
Фоновая обработка очереди видео.

Очередь — это папка incoming/ в R2, куда браузер кладёт исходник. Опросчик
живёт внутри процесса бота: ffmpeg запускается отдельным процессом и event loop
не блокирует, поэтому выносить это в отдельный сервис не за чем — он потребовал
бы своего деплоя, наблюдения и способа отвечать пользователю в Telegram.

Направление связи важно: бот сам ходит в R2. Входящих соединений на хост фича
не добавляет.
"""
import asyncio
import logging
import os
import shutil
import sys
import tempfile
from typing import Dict, Set

import bugsnag
from aiogram import Bot
from aiogram.types import FSInputFile

from services import r2, video
from utils import is_user_allowed

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 5
ERROR_BACKOFF_SEC = 30

# Сколько заданий запрашиваем за один опрос.
LIST_LIMIT = 20

# Потолок на задание целиком: скачивание, транскод, заливка.
#
# У ffmpeg и у запросов boto3 таймауты свои, но они посегментные: скачивание,
# которое отдаёт по байту в секунду, ни один из них не прервёт. Без общего
# потолка такое задание держало бы слот в _in_flight и MAX_CONCURRENT_JOBS
# неограниченно долго, и после двух таких очередь встала бы совсем.
JOB_TIMEOUT_SEC = 1800

# Сколько заданий обрабатывается одновременно. Транскод дополнительно
# ограничен семафором в services.video — здесь ограничивается ещё и скачивание,
# которое для файла в 250 МБ тоже не бесплатно.
MAX_CONCURRENT_JOBS = 2

# После скольких неудач задание уходит в failed/.
#
# Без этого счётчика файл, стабильно роняющий ffmpeg, подбирался бы опросчиком
# снова и снова: бесконечный цикл, который тратит процессор и ничем себя не
# обнаруживает.
MAX_ATTEMPTS = 3

# Во сколько раз больше размера исходника нужно свободного места: сам исходник,
# результат и запас на временные файлы ffmpeg.
DISK_HEADROOM_FACTOR = 3

_in_flight: Set[str] = set()
_attempts: Dict[str, int] = {}


async def _notify(bot: Bot, user_id: int, text: str, **kwargs) -> None:
    """Отправляет сообщение пользователю, не роняя обработку.

    Пользователь мог заблокировать бота или удалить чат. Это не повод терять
    уже сделанную работу, но и молчать не будем — иначе непонятно, почему
    человек «не получил ссылку».
    """
    try:
        await bot.send_message(user_id, text, **kwargs)
    except Exception as e:
        logger.warning(
            "[video-queue] не удалось написать user_id=%s (%s: %s)",
            user_id, type(e).__name__, e,
        )


def _assert_enough_disk(workdir: str, source_size: int) -> None:
    """Проверяет свободное место до скачивания.

    Упереться в заполненный диск на середине — значит положить не только эту
    фичу, но и всё остальное на хосте. Лучше отказать заранее и сказать об этом.
    """
    free = shutil.disk_usage(workdir).free
    needed = source_size * DISK_HEADROOM_FACTOR
    if free < needed:
        raise video.VideoError(
            f"недостаточно места на диске: свободно {free // 1024 // 1024} МБ, "
            f"нужно не менее {needed // 1024 // 1024} МБ"
        )


async def _process(bot: Bot, obj: r2.IncomingObject) -> None:
    """Обрабатывает одно задание от скачивания до ответа пользователю."""
    # Повторная проверка доступа. Worker проверил вайтлист при выдаче ссылки,
    # но между загрузкой и обработкой человека могли убрать из таблицы.
    # Пропуск — это работа в пользу того, кому доступ уже закрыт.
    #
    # Через executor: обычно это попадание в кэш, но по истечении TTL
    # is_user_allowed синхронно идёт в Google Sheets, и прямой вызов из
    # корутины остановил бы event loop на всё время запроса.
    loop = asyncio.get_running_loop()
    allowed = await loop.run_in_executor(None, is_user_allowed, obj.user_id)
    if not allowed:
        logger.warning(
            "[video-queue] задание от пользователя вне вайтлиста отброшено: user_id=%s",
            obj.user_id,
        )
        await r2.delete(obj.key)
        return

    workdir = tempfile.mkdtemp(prefix="videocloud_")
    src_path = os.path.join(workdir, "source")
    dst_path = os.path.join(workdir, "result.mp4")

    try:
        _assert_enough_disk(workdir, obj.size_bytes)

        await r2.download(obj.key, src_path)
        source_info = await video.probe(src_path)

        if source_info.duration > video.MAX_INPUT_SECONDS:
            raise video.VideoError(
                f"исходник длиной {source_info.duration:.0f} с превышает "
                f"допустимые {video.MAX_INPUT_SECONDS} с"
            )

        lines = [f"📥 Принял: {source_info.human()}"]
        warning = video.describe_plan(source_info)
        if warning:
            lines.append(warning)
        lines.append("Обрабатываю…")
        await _notify(bot, obj.user_id, "\n".join(lines))

        result_info = await video.transcode(src_path, dst_path)

        key = r2.build_result_key()
        url = await r2.upload_result(dst_path, key)

        await _notify(
            bot,
            obj.user_id,
            f"✅ Готово\n"
            f"Было: {source_info.human()}\n"
            f"Стало: {result_info.human()}\n\n"
            f"{url}",
            disable_web_page_preview=True,
        )
        try:
            await bot.send_document(obj.user_id, FSInputFile(dst_path, filename="cloud_autostart.mp4"))
        except Exception as e:
            # Ссылка уже отправлена — главное доставлено. Но знать о сбое надо.
            logger.warning(
                "[video-queue] не удалось отправить файл user_id=%s (%s: %s)",
                obj.user_id, type(e).__name__, e,
            )

        await r2.delete(obj.key)
        _attempts.pop(obj.key, None)
        logger.info("[video-queue] задание выполнено: %s -> %s", obj.key, key)

    finally:
        _cleanup_workdir(workdir)


def _cleanup_workdir(workdir: str) -> None:
    """Удаляет рабочую папку, сообщая о неудаче.

    ignore_errors=True здесь был бы тихим отказом: неудалённая папка с
    исходником на 250 МБ съедает диск, и обнаружится это только когда диск
    кончится и встанет весь хост, а не одна эта фича.
    """
    def on_error(func, path, exc_info):
        logger.error(
            "[video-queue] не удалось удалить %s (%s: %s). Место на диске не освобождено.",
            path, type(exc_info[1]).__name__, exc_info[1],
        )

    # onexc появился в 3.12 и заменяет устаревший onerror; поддерживаем оба,
    # чтобы не зависеть от версии Python на хосте.
    if sys.version_info >= (3, 12):
        shutil.rmtree(workdir, onexc=lambda f, p, e: on_error(f, p, (type(e), e, None)))
    else:
        shutil.rmtree(workdir, onerror=on_error)


async def _process_safe(bot: Bot, obj: r2.IncomingObject) -> None:
    """Обёртка: считает попытки и уводит безнадёжные задания в failed/."""
    try:
        # Вложенный try, а не соседний except: возбуждение из except-блока
        # не перехватывается соседними ветками того же try, и таймаут прошёл
        # бы мимо счётчика попыток и мимо переноса в failed/.
        try:
            await asyncio.wait_for(_process(bot, obj), timeout=JOB_TIMEOUT_SEC)
        except asyncio.TimeoutError as e:
            raise video.VideoError(
                f"задание не уложилось в {JOB_TIMEOUT_SEC} с и было прервано"
            ) from e
    except asyncio.CancelledError:
        raise
    except Exception as e:
        attempts = _attempts.get(obj.key, 0) + 1
        _attempts[obj.key] = attempts

        logger.error(
            "[video-queue] попытка %s/%s для %s провалилась: %s: %s",
            attempts, MAX_ATTEMPTS, obj.key, type(e).__name__, e,
        )
        bugsnag.notify(e)

        if attempts >= MAX_ATTEMPTS:
            try:
                await r2.move_to_failed(obj.key)
                _attempts.pop(obj.key, None)
                await _notify(
                    bot,
                    obj.user_id,
                    "❌ Не удалось обработать видео. "
                    "Файл сохранён для разбора, администратор уведомлён.",
                )
            except Exception as move_error:
                # Объект остался в incoming/ и будет подобран снова. Это хуже
                # обычной ошибки: цикл продолжится, поэтому пишем громко.
                logger.error(
                    "[video-queue] задание %s не удалось перенести в failed/ (%s: %s). "
                    "Оно останется в очереди и будет повторяться.",
                    obj.key, type(move_error).__name__, move_error,
                )
                bugsnag.notify(move_error)
    finally:
        _in_flight.discard(obj.key)


def _prune_attempts(present_keys: Set[str], listing_complete: bool) -> None:
    """Забывает счётчики для заданий, которых в очереди уже нет.

    Счётчик снимается при успехе и при уходе в failed/, но задание может
    исчезнуть и иначе: его удалит lifecycle-правило R2 через сутки. Без чистки
    такие записи копились бы в памяти процесса, который перезапускается редко.

    Чистим только когда видели очередь целиком: при усечённом листинге задание
    из невидимого хвоста потеряло бы счётчик и получило лишние попытки.
    """
    if not listing_complete:
        return
    for key in [k for k in _attempts if k not in present_keys]:
        del _attempts[key]


async def run_video_queue(bot: Bot) -> None:
    """Фоновая задача: опрашивает очередь и раздаёт задания."""
    logger.info("[video-queue] запущена, опрос раз в %s с", POLL_INTERVAL_SEC)

    while True:
        delay = POLL_INTERVAL_SEC
        try:
            if len(_in_flight) < MAX_CONCURRENT_JOBS:
                objects = await r2.list_incoming(limit=LIST_LIMIT)
                _prune_attempts(
                    {o.key for o in objects},
                    listing_complete=len(objects) < LIST_LIMIT,
                )

                for obj in objects:
                    if obj.key in _in_flight:
                        continue
                    if len(_in_flight) >= MAX_CONCURRENT_JOBS:
                        break
                    _in_flight.add(obj.key)
                    asyncio.create_task(_process_safe(bot, obj))
        except asyncio.CancelledError:
            logger.info("[video-queue] остановлена")
            raise
        except Exception as e:
            # Сюда попадают только сбои самого опроса (R2 недоступен,
            # креды неверны). Ошибки обработки заданий ловит _process_safe.
            logger.error(
                "[video-queue] опрос очереди не удался (%s: %s). Пауза %s с.",
                type(e).__name__, e, ERROR_BACKOFF_SEC,
            )
            bugsnag.notify(e)
            delay = ERROR_BACKOFF_SEC

        await asyncio.sleep(delay)
