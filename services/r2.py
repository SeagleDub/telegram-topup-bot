"""
Работа с Cloudflare R2 (S3-совместимое хранилище).

R2 здесь выполняет две роли сразу:
  - очередь заданий — папка incoming/, куда браузер кладёт исходники;
  - хранилище результатов — папка v/, откуда Cloud забирает готовые ролики.

Отдельного брокера очередей нет намеренно: задания появляются штучно, порядок
не важен, а любой брокер был бы ещё одним сервисом, который нужно
разворачивать и наблюдать.

boto3 синхронный, поэтому каждый вызов уходит в пул потоков: прямой вызов
заблокировал бы event loop на всё время передачи 250 МБ, и бот перестал бы
отвечать всем остальным.
"""
import asyncio
import functools
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from config import (
    R2_ACCESS_KEY_ID,
    R2_ACCOUNT_ID,
    R2_BUCKET,
    R2_PUBLIC_BASE_URL,
    R2_SECRET_ACCESS_KEY,
)

logger = logging.getLogger(__name__)

INCOMING_PREFIX = "incoming/"
RESULT_PREFIX = "v/"
FAILED_PREFIX = "failed/"

# Ключ, который выдаёт Worker: incoming/<user_id>/<uuid>.mp4
# user_id подставлен Worker'ом после проверки подписи Telegram, клиент на него
# не влияет — поэтому ему можно доверять при выборе адресата ответа.
_INCOMING_KEY_RE = re.compile(r"^incoming/(\d+)/([0-9a-fA-F-]{36})\.mp4$")


class R2Error(Exception):
    """Ошибка обращения к хранилищу."""


@dataclass(frozen=True)
class IncomingObject:
    key: str
    user_id: int
    size_bytes: int


_client = None


def _get_client():
    """Создаёт клиента один раз и переиспользует.

    Пересоздание на каждый вызов означало бы новый TLS-хендшейк на каждую
    операцию — заметно при работе с файлами в сотни мегабайт.
    """
    global _client
    if _client is not None:
        return _client

    missing = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID", R2_ACCOUNT_ID),
            ("R2_ACCESS_KEY_ID", R2_ACCESS_KEY_ID),
            ("R2_SECRET_ACCESS_KEY", R2_SECRET_ACCESS_KEY),
            ("R2_BUCKET", R2_BUCKET),
            ("R2_PUBLIC_BASE_URL", R2_PUBLIC_BASE_URL),
        )
        if not value
    ]
    if missing:
        raise R2Error(
            "не заданы переменные окружения: " + ", ".join(missing)
        )

    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            # Сеть может моргнуть посреди передачи сотен мегабайт; таймаут
            # должен быть больше времени осмысленной передачи, но не
            # бесконечным.
            connect_timeout=15,
            read_timeout=300,
        ),
    )
    return _client


async def _call(fn, *args, **kwargs):
    """Выполняет синхронный вызов boto3 в пуле потоков."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))
    except (ClientError, BotoCoreError) as e:
        raise R2Error(f"{type(e).__name__}: {e}") from e


async def list_incoming(limit: int = 20) -> List[IncomingObject]:
    """Возвращает задания из incoming/.

    Объекты с неожиданным именем не обрабатываются и не удаляются: ключи
    формирует Worker, и чужой формат означает либо ошибку в нём, либо чью-то
    ручную запись. И то, и другое требует внимания человека, а не тихого
    удаления.
    """
    client = _get_client()
    response = await _call(
        client.list_objects_v2,
        Bucket=R2_BUCKET,
        Prefix=INCOMING_PREFIX,
        MaxKeys=limit,
    )

    result: List[IncomingObject] = []
    for item in response.get("Contents") or []:
        key = item["Key"]
        match = _INCOMING_KEY_RE.match(key)
        if not match:
            logger.warning("[r2] объект с неожиданным именем пропущен: %s", key)
            continue
        result.append(
            IncomingObject(
                key=key,
                user_id=int(match.group(1)),
                size_bytes=int(item.get("Size") or 0),
            )
        )
    return result


async def download(key: str, dest_path: str) -> None:
    """Скачивает объект в локальный файл."""
    client = _get_client()
    await _call(client.download_file, R2_BUCKET, key, dest_path)
    logger.info("[r2] скачан %s -> %s (%s байт)", key, dest_path, os.path.getsize(dest_path))


async def upload_result(local_path: str, key: str) -> str:
    """Заливает готовый ролик и возвращает публичную ссылку.

    ContentType задаётся явно и намеренно. Без него R2 отдаёт
    application/octet-stream, браузер предлагает скачать файл вместо
    воспроизведения, и autostart не срабатывает — при технически успешной
    загрузке цель фичи не достигается.
    """
    client = _get_client()
    await _call(
        client.upload_file,
        local_path,
        R2_BUCKET,
        key,
        ExtraArgs={"ContentType": "video/mp4"},
    )
    url = f"{R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"
    logger.info("[r2] залит результат %s", key)
    return url


async def delete(key: str) -> None:
    """Удаляет объект."""
    client = _get_client()
    await _call(client.delete_object, Bucket=R2_BUCKET, Key=key)
    logger.info("[r2] удалён %s", key)


async def move_to_failed(key: str) -> Optional[str]:
    """Переносит задание в failed/ и возвращает новый ключ.

    Зачем перенос, а не удаление: файл, который стабильно роняет обработку,
    нужен для разбора причины. Удалив его, мы потеряем единственный
    воспроизводимый пример.

    Если перенос не удался, объект остаётся в incoming/. Это заметно хуже, чем
    кажется — он будет подобран снова, — поэтому ошибка не глушится, а
    поднимается вызывающему.
    """
    if not key.startswith(INCOMING_PREFIX):
        raise R2Error(f"ожидался ключ из {INCOMING_PREFIX}, получен {key}")

    client = _get_client()
    new_key = FAILED_PREFIX + key[len(INCOMING_PREFIX):]

    await _call(
        client.copy_object,
        Bucket=R2_BUCKET,
        CopySource={"Bucket": R2_BUCKET, "Key": key},
        Key=new_key,
    )
    await _call(client.delete_object, Bucket=R2_BUCKET, Key=key)
    logger.warning("[r2] задание перенесено в %s", new_key)
    return new_key


def build_result_key() -> str:
    """Имя объекта для результата.

    uuid, а не последовательный номер: по угадываемым именам перебираются
    чужие креативы. user_id в путь не кладём — публичная ссылка не должна
    раскрывать, кто её сделал.
    """
    return f"{RESULT_PREFIX}{uuid.uuid4().hex}.mp4"
