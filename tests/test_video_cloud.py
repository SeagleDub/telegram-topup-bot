"""
Тесты фичи «Видео для Cloud» — та часть, что проверяется без ffmpeg и без сети.

Покрыто намеренно именно это:

  - has_faststart разбирает бинарную структуру MP4 вручную. Ошибка здесь не
    уронит ничего заметно: проверка просто начнёт врать, и мы будем отдавать
    ссылки на файлы без autostart, то есть ровно на то, ради чего фича делалась.

  - регулярное выражение для ключей R2 решает, будет ли задание вообще
    подобрано. Ошибка в нём тихо остановит очередь: файлы будут копиться в
    incoming/, а бот — считать, что работы нет.

  - команда ffmpeg отлажена вручную и согласована. Любое расхождение параметров
    означает несовместимый с Cloud файл при полностью успешном прогоне.

Запуск: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import video_queue
from services.r2 import _INCOMING_KEY_RE, FAILED_PREFIX, INCOMING_PREFIX
from services.video import (
    MAX_OUTPUT_SECONDS,
    MAX_WIDTH,
    build_transcode_command,
    has_faststart,
)

UUID = "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d"


def box(box_type: bytes, payload: bytes = b"") -> bytes:
    """Собирает бокс MP4: 4 байта размера, 4 байта типа, тело."""
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def box64(box_type: bytes, payload: bytes = b"") -> bytes:
    """Бокс с 64-битным размером: поле размера равно 1, дальше 8 байт длины."""
    total = 16 + len(payload)
    return (1).to_bytes(4, "big") + box_type + total.to_bytes(8, "big") + payload


def write_tmp(data: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


class FaststartTest(unittest.TestCase):
    """moov раньше mdat — единственное, что делает autostart возможным."""

    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _file(self, data: bytes) -> str:
        path = write_tmp(data)
        self._paths.append(path)
        return path

    def test_moov_before_mdat(self):
        data = box(b"ftyp", b"isom") + box(b"moov", b"x" * 40) + box(b"mdat", b"y" * 100)
        self.assertTrue(has_faststart(self._file(data)))

    def test_moov_after_mdat(self):
        # Раскладка по умолчанию, без +faststart. Именно её проверка обязана
        # поймать: ffmpeg отчитается об успехе и в этом случае тоже.
        data = box(b"ftyp", b"isom") + box(b"mdat", b"y" * 100) + box(b"moov", b"x" * 40)
        self.assertFalse(has_faststart(self._file(data)))

    def test_moov_absent(self):
        data = box(b"ftyp", b"isom") + box(b"free", b"z" * 16)
        self.assertFalse(has_faststart(self._file(data)))

    def test_64bit_mdat_before_moov(self):
        # Большие файлы используют 64-битный размер бокса. Если его не
        # разобрать, обход собьётся и результат окажется случайным.
        data = box(b"ftyp", b"isom") + box64(b"mdat", b"y" * 200) + box(b"moov", b"x" * 40)
        self.assertFalse(has_faststart(self._file(data)))

    def test_64bit_moov_before_mdat(self):
        data = box(b"ftyp", b"isom") + box64(b"moov", b"x" * 40) + box(b"mdat", b"y" * 200)
        self.assertTrue(has_faststart(self._file(data)))

    def test_truncated_file(self):
        self.assertFalse(has_faststart(self._file(b"\x00\x00")))

    def test_empty_file(self):
        self.assertFalse(has_faststart(self._file(b"")))

    def test_zero_size_box_does_not_hang(self):
        # Размер 0 означает «бокс до конца файла». Наивная реализация уходит
        # здесь в бесконечный цикл вместо ответа.
        data = box(b"ftyp", b"isom") + (0).to_bytes(4, "big") + b"mdat" + b"y" * 50
        self.assertFalse(has_faststart(self._file(data)))

    def test_garbage_box_size_rejected(self):
        # Размер меньше заголовка — файл битый. Обход должен остановиться, а не
        # перематывать назад.
        data = box(b"ftyp", b"isom") + (3).to_bytes(4, "big") + b"junk" + b"\x00" * 20
        self.assertFalse(has_faststart(self._file(data)))


class IncomingKeyTest(unittest.TestCase):
    """Ключ задания несёт user_id, которому бот отправит результат."""

    def test_valid_key_parsed(self):
        match = _INCOMING_KEY_RE.match(f"incoming/123456/{UUID}.mp4")
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 123456)

    def test_other_prefixes_rejected(self):
        # Результаты и разобранные неудачи не должны попадать обратно в очередь.
        for key in (
            f"v/{UUID}.mp4",
            f"failed/123/{UUID}.mp4",
            f"incoming/{UUID}.mp4",
        ):
            with self.subTest(key=key):
                self.assertIsNone(_INCOMING_KEY_RE.match(key))

    def test_non_numeric_user_id_rejected(self):
        self.assertIsNone(_INCOMING_KEY_RE.match(f"incoming/abc/{UUID}.mp4"))

    def test_path_traversal_rejected(self):
        # Ключи формирует Worker, но полагаться на это в разборе нельзя:
        # имя объекта участвует в построении пути в failed/.
        for key in (
            "incoming/../../etc/passwd",
            f"incoming/123/../{UUID}.mp4",
            f"incoming/123/{UUID}.mp4/../x.mp4",
        ):
            with self.subTest(key=key):
                self.assertIsNone(_INCOMING_KEY_RE.match(key))

    def test_wrong_extension_rejected(self):
        self.assertIsNone(_INCOMING_KEY_RE.match(f"incoming/123/{UUID}.exe"))

    def test_failed_prefix_derivation(self):
        key = f"incoming/777/{UUID}.mp4"
        self.assertEqual(
            FAILED_PREFIX + key[len(INCOMING_PREFIX):],
            f"failed/777/{UUID}.mp4",
        )


class TranscodeCommandTest(unittest.TestCase):
    """Параметры согласованы с ручной отладкой; расхождение ломает autostart."""

    def setUp(self):
        self.cmd = build_transcode_command("/tmp/in.mov", "/tmp/out.mp4")

    def _value_after(self, flag: str) -> str:
        return self.cmd[self.cmd.index(flag) + 1]

    def test_required_profile(self):
        self.assertEqual(self._value_after("-profile:v"), "baseline")
        self.assertEqual(self._value_after("-level"), "3.1")
        self.assertEqual(self._value_after("-pix_fmt"), "yuv420p")
        self.assertEqual(self._value_after("-c:v"), "libx264")

    def test_duration_cap(self):
        self.assertEqual(self._value_after("-t"), str(MAX_OUTPUT_SECONDS))

    def test_scale_limits_width_and_keeps_even_height(self):
        self.assertEqual(self._value_after("-vf"), f"scale='min({MAX_WIDTH},iw)':-2")

    def test_faststart_enabled(self):
        self.assertEqual(self._value_after("-movflags"), "+faststart")

    def test_audio_stream_is_optional(self):
        # Без "?" ролик без звуковой дорожки не обработался бы вовсе.
        self.assertIn("0:a?", self.cmd)

    def test_metadata_stripped(self):
        self.assertEqual(self._value_after("-map_metadata"), "-1")

    def test_subtitles_and_data_dropped(self):
        self.assertIn("-sn", self.cmd)
        self.assertIn("-dn", self.cmd)

    def test_bitrate_caps(self):
        self.assertEqual(self._value_after("-crf"), "23")
        self.assertEqual(self._value_after("-maxrate"), "2500k")
        self.assertEqual(self._value_after("-bufsize"), "5000k")

    def test_runs_under_nice(self):
        # Без nice транскод конкурирует с ботом за процессор на равных.
        self.assertEqual(self.cmd[:3], ["nice", "-n", "19"])

    def test_paths_passed_as_separate_argv(self):
        # Аргументы уходят в create_subprocess_exec списком, без оболочки:
        # имя файла с пробелом или кавычкой не должно ничего ломать.
        self.assertIn("/tmp/in.mov", self.cmd)
        self.assertEqual(self.cmd[-1], "/tmp/out.mp4")


class AttemptsPruningTest(unittest.TestCase):
    """Счётчики попыток не должны копиться в памяти долгоживущего процесса."""

    def setUp(self):
        video_queue._attempts.clear()

    def tearDown(self):
        video_queue._attempts.clear()

    def test_forgets_keys_absent_from_queue(self):
        # Задание может исчезнуть не только успехом: его удалит lifecycle R2.
        video_queue._attempts.update({"incoming/1/a.mp4": 2, "incoming/2/b.mp4": 1})
        video_queue._prune_attempts({"incoming/1/a.mp4"}, listing_complete=True)
        self.assertEqual(video_queue._attempts, {"incoming/1/a.mp4": 2})

    def test_keeps_everything_when_listing_truncated(self):
        # Задание из невидимого хвоста очереди потеряло бы счётчик и получило
        # лишние попытки вместо ухода в failed/.
        video_queue._attempts.update({"incoming/1/a.mp4": 2, "incoming/2/b.mp4": 1})
        video_queue._prune_attempts({"incoming/1/a.mp4"}, listing_complete=False)
        self.assertEqual(len(video_queue._attempts), 2)

    def test_empty_queue_clears_all(self):
        video_queue._attempts.update({"incoming/1/a.mp4": 2})
        video_queue._prune_attempts(set(), listing_complete=True)
        self.assertEqual(video_queue._attempts, {})


class WorkdirCleanupTest(unittest.TestCase):
    """Неудалённая папка с исходником на 250 МБ съедает диск молча."""

    def test_removes_directory(self):
        workdir = tempfile.mkdtemp()
        with open(os.path.join(workdir, "f.bin"), "wb") as f:
            f.write(b"x" * 128)
        video_queue._cleanup_workdir(workdir)
        self.assertFalse(os.path.exists(workdir))

    def test_reports_failure_instead_of_raising(self):
        # Удалять нечего — обработчик обязан сообщить, а не уронить задание,
        # которое к этому моменту уже успешно выполнено.
        with self.assertLogs("services.video_queue", level="ERROR") as captured:
            video_queue._cleanup_workdir("/nonexistent/path/videocloud_test")
        self.assertTrue(
            any("не удалось удалить" in line for line in captured.output),
            captured.output,
        )


if __name__ == "__main__":
    unittest.main()
