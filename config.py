"""
Конфигурация бота
"""
from dotenv import load_dotenv
import os

load_dotenv()

# Токены и ключи
API_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
BUGSNAG_TOKEN = os.getenv("BUGSNAG_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
LUBOYDOMEN_API_TOKEN = os.getenv("LUBOYDOMEN_API_TOKEN")

# eCards API (единственный банк: "Действия с картами" + расход по группе).
# Авторизация статическая: заголовок Authorization: Bearer <ECARDS_TOKEN>.
# Базовый URL захардкожен в services/ecards.py.
ECARDS_TOKEN = os.getenv("ECARDS_TOKEN")

# Cloudflare: хранилище видео и Mini App для загрузки.
#
# Загрузка идёт браузером напрямую в R2, минуя бота: Bot API не отдаёт файлы
# больше 20 МБ, а исходники доходят до 250 МБ. Бот забирает готовое задание из
# R2 сам — входящих соединений на хост при этом не появляется.
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL")

# Адрес Worker'а: он же отдаёт страницу Mini App, он же принимает синхронизацию
# вайтлиста. Токен — общий секрет только для синхронизации, не токен Telegram.
CF_WORKER_URL = os.getenv("CF_WORKER_URL")
KV_SYNC_TOKEN = os.getenv("KV_SYNC_TOKEN")

# ID пользователей
ADMIN_ID = int(os.getenv("ADMIN_ID"))


def _parse_id_list(raw: str) -> tuple:
    """Разбирает "111, 222" в кортеж int. Порядок сохраняется, дубли снимаются.

    Порядок важен: он определяет очерёдность рассылки уведомлений, а значит и
    то, кто увидит заявку первым.
    """
    ids = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)  # не try/except: мусор в .env должен ронять старт
        if value not in ids:
            ids.append(value)
    return tuple(ids)


# Тимлидов может быть несколько: TEAMLEADER_IDS="111,222,333".
# Старое односоставное имя TEAMLEADER_ID продолжает работать как запасной
# источник — иначе обновление кода молча разлогинило бы текущего тимлидера.
TEAMLEADER_IDS = _parse_id_list(os.getenv("TEAMLEADER_IDS") or os.getenv("TEAMLEADER_ID"))
if not TEAMLEADER_IDS:
    raise RuntimeError(
        "Не задан ни TEAMLEADER_IDS, ни TEAMLEADER_ID. Без тимлидеров заявки "
        "уйдут только админу — это тихая потеря половины получателей, "
        "поэтому старт запрещён."
    )

# Роль «проверяющий расходы»: EXPENSE_VIEWER_IDS="111,222".
#
# Права ровно одни — «📊 Получить расход по байеру», то есть чтение чужого
# расхода из таблицы. Не админ: заявки на пополнение такой пользователь не
# получает и одобрять их не может. Роль необязательная, пустой список —
# штатное состояние, поэтому здесь нет проверки на непустоту.
EXPENSE_VIEWER_IDS = _parse_id_list(os.getenv("EXPENSE_VIEWER_IDS"))

# Получатели уведомлений о заявках: админ плюс все тимлидеры.
# Проверяющих расходы здесь намеренно нет — роль read-only.
# Админ первым — исторический порядок рассылки.
NOTIFY_IDS = (ADMIN_ID,) + tuple(tid for tid in TEAMLEADER_IDS if tid != ADMIN_ID)

# Проверяющий расходы, вписанный ещё и в админы/тимлидеры, — почти наверняка
# ошибка настройки: роль ниже по правам, и такой ID молча получил бы полный
# доступ. Падаем на старте, а не разбираемся потом.
_role_overlap = set(EXPENSE_VIEWER_IDS) & set(NOTIFY_IDS)
if _role_overlap:
    raise RuntimeError(
        f"ID {sorted(_role_overlap)} указаны и в EXPENSE_VIEWER_IDS, и среди "
        "админа/тимлидеров. Роль проверяющего расходы — строго read-only, "
        "совмещение означает полный админский доступ. Уберите ID из одного списка."
    )

# Все, кому роль назначена вручную в .env. Такие ID не обязаны быть в
# вайтлисте — вайтлист это байеры из таблицы, а роли задаются при деплое.
ROLE_IDS = frozenset(NOTIFY_IDS) | frozenset(EXPENSE_VIEWER_IDS)

# Настройка Bugsnag
import bugsnag
if BUGSNAG_TOKEN:
    bugsnag.configure(api_key=BUGSNAG_TOKEN)
