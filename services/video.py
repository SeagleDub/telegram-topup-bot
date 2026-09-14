"""
Транскод видео под autostart в Cloud.

Требование платформы: у ролика длиннее ~29 секунд autostart не включается.
Поэтому итоговый файл приводится к совместимому профилю и обрезается.

Почему обязательна проверка результата, а не только успешный код возврата
ffmpeg: файл, не соответствующий требованиям Cloud, внешне выглядит рабочим.
Отдать на него ссылку молча хуже, чем упасть — байер зальёт его в рекламу и
будет искать причину неработающего autostart в самой рекламе.
"""
import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Требования Cloud к итоговому файлу.
MAX_OUTPUT_SECONDS = 29
MAX_WIDTH = 720

# Допуск при проверке длительности: ffmpeg режет по границе кадра, поэтому
# итог может оказаться на доли секунды длиннее заданного -t.
DURATION_TOLERANCE_SEC = 0.5

# Потолок времени на один транскод. 29 секунд 720p укладываются в минуту даже
# на слабом ядре; всё, что идёт кратно дольше, зависло.
TRANSCODE_TIMEOUT_SEC = 900
PROBE_TIMEOUT_SEC = 120

# Отбой до запуска ffmpeg: часовой ролик не станет пригодным креативом, а ядро
# займёт надолго.
MAX_INPUT_SECONDS = 3600

# Сколько транскодов идёт одновременно.
#
# ffmpeg не блокирует event loop (запускается отдельным процессом), но забирает
# ядра. Значение 1, а не 2: на VPS с двумя ядрами два параллельных libx264
# не оставляют боту ничего, и он выглядит зависшим, хотя формально работает.
# Очередь и так последовательная, пропускная способность от этого страдает
# несильно.
_transcode_slots = asyncio.Semaphore(1)

# Сколько потоков разрешено libx264.
#
# По умолчанию x264 берёт примерно полтора потока на ядро и занимает машину
# целиком. nice понижает приоритет, но не уменьшает число готовых к работе
# потоков: планировщик всё равно постоянно вытесняет процесс бота. Явный
# потолок надёжнее приоритета.
#
# Цена — более долгий транскод. Для 29 секунд 720p это приемлемо: фича не
# интерактивная, человек всё равно ждёт сообщения в Telegram.
FFMPEG_THREADS = 1

# nice дополняет ограничение потоков: транскод уступает процессор боту.
NICE_PREFIX: List[str] = ["nice", "-n", "19"]


class VideoError(Exception):
    """Ошибка обработки видео с достаточным контекстом для разбора."""


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    codec: str
    profile: str
    pix_fmt: str
    size_bytes: int
    has_audio: bool

    def human(self) -> str:
        mb = self.size_bytes / 1024 / 1024
        return f"{self.duration:.0f} с, {self.width}×{self.height}, {mb:.1f} МБ"


_tools_ok = False


def tools_available() -> bool:
    """Готова ли фича к работе. Проверяется хендлером перед показом загрузчика."""
    return _tools_ok


def ensure_tools_available() -> None:
    """Проверяет наличие ffmpeg и ffprobe. Вызывается при старте бота.

    Бросает VideoError, если бинарников нет. Ронять ли из-за этого весь бот —
    решает вызывающий: остальные функции от ffmpeg не зависят, и лишать команду
    работы с картами из-за неустановленного пакета было бы хуже самой проблемы.
    Но тихо продолжать тоже нельзя — иначе загруженное видео уходило бы в
    никуда, и никто бы не понял почему.
    """
    global _tools_ok
    missing = [tool for tool in (FFMPEG, FFPROBE) if shutil.which(tool) is None]
    if missing:
        _tools_ok = False
        raise VideoError(
            f"не найдены обязательные бинарники: {', '.join(missing)}. "
            f"Установите пакет ffmpeg на хосте."
        )
    _tools_ok = True
    logger.info("[video] ffmpeg и ffprobe найдены")


async def _run(cmd: List[str], timeout: int) -> Tuple[int, bytes, bytes]:
    """Запускает процесс, возвращает (код возврата, stdout, stderr).

    Зависший ffmpeg убивается: без этого он держал бы слот семафора вечно, и
    после двух таких зависаний фича встала бы целиком, ничем это не показав.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise VideoError(
            f"процесс не уложился в {timeout} с и был убит: {' '.join(cmd[:4])}…"
        ) from None
    return proc.returncode, stdout, stderr


def _tail(data: bytes, limit: int = 2000) -> str:
    text = data.decode("utf-8", errors="replace").strip()
    return text[-limit:]


async def probe(path: str) -> VideoInfo:
    """Читает параметры файла через ffprobe.

    Заодно служит проверкой формата: заявленный клиентом тип файла — это
    пожелание, а не факт. Не разобрался ffprobe — значит это не видео,
    независимо от расширения и Content-Type.
    """
    cmd = [FFPROBE, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    rc, stdout, stderr = await _run(cmd, PROBE_TIMEOUT_SEC)

    if rc != 0:
        raise VideoError(
            f"ffprobe завершился с кодом {rc} на файле {path}. "
            f"stderr: {_tail(stderr)}"
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise VideoError(f"ffprobe вернул неразбираемый JSON для {path}: {e}") from e

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise VideoError(f"в файле {path} нет видеодорожки")

    fmt = data.get("format") or {}
    # Длительность надёжнее брать из контейнера: у потока её может не быть.
    raw_duration = fmt.get("duration") or video.get("duration")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        raise VideoError(f"не удалось определить длительность файла {path}") from None

    try:
        size_bytes = int(fmt.get("size") or os.path.getsize(path))
    except (TypeError, ValueError, OSError) as e:
        raise VideoError(f"не удалось определить размер файла {path}: {e}") from e

    return VideoInfo(
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        codec=str(video.get("codec_name") or "?"),
        profile=str(video.get("profile") or "?"),
        pix_fmt=str(video.get("pix_fmt") or "?"),
        size_bytes=size_bytes,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def build_transcode_command(src: str, dst: str) -> List[str]:
    """Собирает команду ffmpeg.

    Вынесено отдельно, чтобы параметры можно было проверить тестом, не запуская
    кодирование. Значения согласованы с командой, отлаженной вручную:

    - baseline / level 3.1 / yuv420p — самый совместимый профиль H.264
    - ширина ограничена 720, высота через -2 остаётся чётной и сохраняет
      пропорции (нечётная высота недопустима для yuv420p)
    - -map 0:a? — вопросительный знак делает аудиодорожку необязательной,
      иначе ролик без звука не обработался бы вовсе
    - -sn -dn — выбрасывает субтитры и данные
    - -map_metadata -1 — снимает метаданные исходника
    - +faststart — переносит moov в начало файла, без этого видео не начнёт
      играть до полной загрузки
    - -t 29 — обрезка ради autostart
    """
    return [
        *NICE_PREFIX,
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
        "-threads", str(FFMPEG_THREADS),
        "-i", src,
        "-map", "0:v:0",
        "-map", "0:a?",
        "-sn", "-dn",
        "-vf", f"scale='min({MAX_WIDTH},iw)':-2",
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.1",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "23",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-movflags", "+faststart",
        "-t", str(MAX_OUTPUT_SECONDS),
        "-c:a", "aac",
        "-b:a", "128k",
        "-map_metadata", "-1",
        dst,
    ]


async def transcode(src: str, dst: str) -> VideoInfo:
    """Перекодирует файл и возвращает параметры результата.

    Успешный код возврата ffmpeg проверкой не считается — результат
    перепроверяется отдельно, см. verify_output.
    """
    cmd = build_transcode_command(src, dst)

    async with _transcode_slots:
        logger.info("[video] старт транскода: %s -> %s", src, dst)
        rc, _stdout, stderr = await _run(cmd, TRANSCODE_TIMEOUT_SEC)

    if rc != 0:
        raise VideoError(
            f"ffmpeg завершился с кодом {rc}.\n"
            f"команда: {' '.join(cmd)}\n"
            f"stderr: {_tail(stderr)}"
        )
    if not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise VideoError(
            f"ffmpeg отчитался об успехе, но файл {dst} пуст или отсутствует.\n"
            f"команда: {' '.join(cmd)}\n"
            f"stderr: {_tail(stderr)}"
        )

    # Предупреждения ffmpeg при успешном коде возврата обычно безобидны, но
    # молчать о них не будем: именно там видно потерю дорожки или подмену
    # параметра.
    warnings = _tail(stderr, 500)
    if warnings:
        logger.warning("[video] ffmpeg отработал с предупреждениями: %s", warnings)

    return await verify_output(dst)


async def verify_output(path: str) -> VideoInfo:
    """Проверяет, что результат действительно соответствует требованиям Cloud.

    Каждая проверка соответствует конкретному требованию платформы. Провал
    любой означает, что autostart не включится, поэтому ссылку отдавать нельзя.
    """
    info = await probe(path)
    problems: List[str] = []

    if info.duration > MAX_OUTPUT_SECONDS + DURATION_TOLERANCE_SEC:
        problems.append(
            f"длительность {info.duration:.2f} с больше допустимых {MAX_OUTPUT_SECONDS} с"
        )
    if info.width > MAX_WIDTH:
        problems.append(f"ширина {info.width} больше допустимых {MAX_WIDTH}")
    if info.codec != "h264":
        problems.append(f"кодек {info.codec}, ожидался h264")
    if "baseline" not in info.profile.lower():
        problems.append(f"профиль {info.profile}, ожидался Baseline")
    if info.pix_fmt != "yuv420p":
        problems.append(f"pix_fmt {info.pix_fmt}, ожидался yuv420p")
    if info.height % 2 != 0:
        problems.append(f"нечётная высота {info.height}")
    if not has_faststart(path):
        problems.append("moov расположен после mdat — faststart не сработал")

    if problems:
        raise VideoError(
            f"результат не соответствует требованиям Cloud ({path}): "
            + "; ".join(problems)
        )

    return info


def has_faststart(path: str) -> bool:
    """Проверяет, что атом moov идёт раньше mdat.

    moov — это оглавление файла. Если оно в конце, плеер не может начать
    воспроизведение, не скачав весь файл, и autostart не срабатывает. Флаг
    -movflags +faststart переносит его в начало, но отчитаться об этом ffmpeg
    может и не выполнив перенос (например, при нехватке места под временный
    файл), поэтому проверяем сами.

    Разбирается только верхний уровень боксов MP4: заголовок это 4 байта
    размера и 4 байта типа. Размер 1 означает 64-битный размер следом,
    размер 0 — бокс до конца файла.
    """
    try:
        file_size = os.path.getsize(path)
        with open(path, "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    return False  # дочитали до конца, moov не встретился

                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8]
                header_len = 8

                if box_size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        return False
                    box_size = int.from_bytes(ext, "big")
                    header_len = 16
                elif box_size == 0:
                    box_size = file_size - (f.tell() - 8)

                if box_type == b"moov":
                    return True
                if box_type == b"mdat":
                    return False

                if box_size < header_len:
                    logger.warning(
                        "[video] некорректный размер бокса %r (%s) в %s",
                        box_type, box_size, path,
                    )
                    return False

                f.seek(box_size - header_len, os.SEEK_CUR)
    except OSError as e:
        raise VideoError(f"не удалось прочитать структуру MP4 {path}: {e}") from e


def describe_plan(info: VideoInfo) -> Optional[str]:
    """Возвращает предупреждение об обрезке, если она будет.

    Байер должен узнать об обрезке до обработки, а не получить обрубок без
    объяснений.
    """
    if info.duration > MAX_OUTPUT_SECONDS + DURATION_TOLERANCE_SEC:
        return (
            f"⚠️ Ролик длиннее {MAX_OUTPUT_SECONDS} с — "
            f"будет обрезан с {info.duration:.0f} с. Это требование autostart."
        )
    return None
