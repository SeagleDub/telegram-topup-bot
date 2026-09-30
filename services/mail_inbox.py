"""
Почта доменов: клиент к Worker'у mail-inbox и разбор писем для Telegram.

Worker хранит письма как есть (.eml), разбирает их бот: у Worker'а на
бесплатном плане 10 мс CPU, а стандартный модуль email справляется с
кодировками (cp1251, quoted-printable, base64) без лимитов и зависимостей.
Устройство: docs/mail-inbox-design.md.
"""
import asyncio
import email
import html
import json
import logging
import mimetypes
import re
from datetime import datetime
from email import policy
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import List, Optional, Tuple

import aiohttp

from config import MAIL_INBOX_URL, MAIL_READ_TOKEN
from services.ecards import KYIV_TZ

logger = logging.getLogger(__name__)

# Те же проверки, что в mail-inbox/collector/src/index.js (DOMAIN_RE, ADDRESS_RE).
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}$")
_ADDRESS_RE = re.compile(r"^[a-z0-9._+-]{1,64}@(?:[a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}$")

# Лимиты — в единицах UTF-16: так длину считает Telegram (4096 на сообщение,
# эмодзи — две единицы). Худший случай вместе с подписями полей и строкой «Кому»
# (адрес до 318 символов) — ~4080: влезает, но впритык. Поднимать лимиты — только
# вместе с test_worst_case_mail_fits_telegram_limit.
MAX_TEXT_LEN = 3000
MAX_HEADER_LEN = 200
MAX_ATTACHMENTS_LEN = 300
MAX_LABEL_LEN = 60

_TIMEOUT = aiohttp.ClientTimeout(total=20)


class MailInboxError(Exception):
    """mail-inbox недоступен или ответил ошибкой."""


def is_configured() -> bool:
    return bool(MAIL_INBOX_URL and MAIL_READ_TOKEN)


# --------------------------------------------------------------------------- #
# Ввод пользователя и список
# --------------------------------------------------------------------------- #

def parse_mail_query(text: str) -> Tuple[str, Optional[str]]:
    """Ввод пользователя → (домен, адрес или None).

    site1.com / https://www.site1.com/x → ("site1.com", None) — все адреса домена;
    Facebook@Site1.com → ("site1.com", "facebook@site1.com") — только этот адрес.
    ValueError — если на домен не похоже.
    """
    value = (text or "").strip().lower()
    local, at, domain = value.rpartition("@") if "@" in value else ("", "", value)
    domain = re.sub(r"^[a-z][a-z0-9+.-]*://", "", domain)
    domain = re.split(r"[/?#:]", domain, maxsplit=1)[0].strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    try:
        # Кириллический домен (сайт.укр) в адресах писем — в punycode.
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"не похоже на домен: {text!r}") from None
    if not _DOMAIN_RE.match(domain):
        raise ValueError(f"не похоже на домен: {text!r}")
    if not at:
        return domain, None
    address = f"{local}@{domain}"
    if not _ADDRESS_RE.match(address):
        raise ValueError(f"не похоже на адрес: {text!r}")
    return domain, address


def decode_header_value(value: Optional[str]) -> str:
    """=?UTF-8?B?…?= → текст. Битый заголовок возвращается как есть."""
    try:
        return str(make_header(decode_header(value or "")))
    except (HeaderParseError, LookupError, ValueError):
        return value or ""


def _cut(text: str, limit: int, tail: str = "…") -> str:
    """Обрезает до limit единиц UTF-16 — так длину считает Telegram."""
    encoded = text.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return text
    keep = (limit - len(tail.encode("utf-16-le")) // 2) * 2
    # "ignore" отбрасывает половинку эмодзи, если разрез пришёлся на неё.
    return encoded[:keep].decode("utf-16-le", "ignore").rstrip() + tail


def button_label(item: dict, now: datetime, with_recipient: bool = False) -> str:
    """«12:41 · Google · Код подтверждения» — подпись кнопки письма в списке.

    with_recipient — для списка всего домена: письма идут на разные адреса, и
    видно, на какой («12:41 · admin@ · Google · …»).
    """
    received = datetime.fromtimestamp(item["receivedAt"] / 1000, KYIV_TZ)
    parts = [received.strftime("%H:%M" if received.date() == now.date() else "%d.%m %H:%M")]
    if with_recipient:
        # Спам на catch-all идёт на длинные случайные адреса — без обрезки они
        # съели бы всю подпись, и не было бы видно ни отправителя, ни темы.
        parts.append(_cut((item.get("to") or "?").split("@")[0], 20) + "@")
    # Сначала разбор адреса, потом декодирование имени: декодированная запятая
    # («Doe, John») ломает parseaddr.
    name, addr = parseaddr(item.get("from") or "")
    parts.append(decode_header_value(name) or addr or "?")
    parts.append(decode_header_value(item.get("subject")) or "(без темы)")
    return _cut(" · ".join(parts), MAX_LABEL_LEN)


# --------------------------------------------------------------------------- #
# Разбор письма
# --------------------------------------------------------------------------- #

class _HtmlToText(HTMLParser):
    """HTML письма → текст. Адрес ссылки остаётся рядом с её текстом:
    в письмах с подтверждением главное часто спрятано в кнопке."""

    _BLOCK = {"p", "div", "tr", "li", "table", "blockquote", "section", "article", "header", "footer",
              "h1", "h2", "h3", "h4", "h5", "h6"}
    _SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_depth = 0
        self._href: Optional[str] = None
        self._link_start = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "br" or tag in self._BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            # Иначе ячейки слипаются: «Код» + «123456» → «Код123456».
            self.parts.append(" ")
        elif tag == "a":
            self._href = (dict(attrs).get("href") or "").strip()
            self._link_start = len(self.parts)

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "a" and self._href:
            label = "".join(self.parts[self._link_start:]).strip()
            if self._href.startswith(("http://", "https://")) and self._href != label:
                self.parts.append(f" ({self._href})")
            self._href = None

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(markup: str) -> str:
    parser = _HtmlToText()
    parser.feed(markup)
    parser.close()
    return "".join(parser.parts)


def _tidy(text: str) -> str:
    lines = (line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _part_text(part) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeError):
        # Неизвестная или неверно указанная кодировка — лучше кракозябры, чем ничего.
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return _tidy(content)


def _body_text(msg) -> str:
    # Текстовая часть в приоритете, но пустая — не повод молчать: бывает, что
    # text/plain пустой, а код есть только в HTML.
    for subtype in ("plain", "html"):
        part = msg.get_body(preferencelist=(subtype,))
        text = _part_text(part) if part is not None else ""
        if text:
            return text
    return ""


def _attachments(msg) -> list:
    """[(имя, часть)] в порядке письма. Порядок стабилен для тех же байтов —
    по нему кнопка находит файл при повторном скачивании письма."""
    result = []
    for i, part in enumerate(msg.iter_attachments()):
        name = part.get_filename()
        if not name:
            # Без имени Telegram файл не примет, а кнопке нужна подпись.
            name = f"attachment-{i + 1}{mimetypes.guess_extension(part.get_content_type()) or '.bin'}"
        result.append((name, part))
    return result


def list_attachments(raw: bytes) -> List[dict]:
    """Вложения письма: [{name, size}], size — в байтах."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    return [{"name": name, "size": len(part.get_payload(decode=True) or b"")}
            for name, part in _attachments(msg)]


def get_attachment(raw: bytes, index: int) -> Tuple[str, bytes]:
    """(имя, содержимое) вложения по номеру. IndexError — если такого нет."""
    if index < 0:
        raise IndexError(index)
    msg = email.message_from_bytes(raw, policy=policy.default)
    name, part = _attachments(msg)[index]
    return name, part.get_payload(decode=True) or b""


def attachment_label(att: dict) -> str:
    """«📎 invoice.pdf · 120 КБ» — подпись кнопки вложения."""
    size = att["size"]
    if size < 1024:
        human = f"{size} Б"
    elif size < 1024 * 1024:
        human = f"{size // 1024} КБ"
    else:
        human = f"{size / 1024 / 1024:.1f} МБ"
    # Режем имя, а не всю подпись: размер должен остаться виден.
    return f"📎 {_cut(att['name'], 40)} · {human}"


def render_message(raw: bytes, received_at_ms: Optional[int], recipient: Optional[str] = None) -> str:
    """Письмо (.eml) → текст для Telegram (parse_mode=HTML). Всё из письма экранируется.

    recipient — адрес из конверта (кому письмо реально пришло), а не заголовок
    To: при catch-all и скрытой копии в To может стоять чужой адрес.
    """
    msg = email.message_from_bytes(raw, policy=policy.default)
    subject = _cut(str(msg.get("subject") or "").strip() or "(без темы)", MAX_HEADER_LEN)
    sender = _cut(str(msg.get("from") or "").strip() or "?", MAX_HEADER_LEN)

    lines = [f"✉️ <b>{html.escape(subject)}</b>", f"От: {html.escape(sender)}"]
    if recipient:
        lines.append(f"Кому: {html.escape(recipient)}")
    if received_at_ms:
        received = datetime.fromtimestamp(received_at_ms / 1000, KYIV_TZ)
        lines.append(f"Получено: {received.strftime('%d.%m.%Y %H:%M')}")
    lines.append("")

    try:
        body = _body_text(msg)
    except Exception:
        # Письмо — чужие данные в любом виде. Сломанный MIME не должен ронять
        # показ: заголовки уже есть, о проблеме с текстом говорим прямо.
        logger.exception("[mail-inbox] не удалось разобрать текст письма")
        body = None
    if body is None:
        lines.append("<i>Не удалось разобрать текст письма.</i>")
    elif not body:
        lines.append("<i>(текст письма пустой)</i>")
    else:
        lines.append(html.escape(_cut(body, MAX_TEXT_LEN, "\n…обрезано")))

    names = [name for name, _ in _attachments(msg)]
    if names:
        lines.append("")
        lines.append("📎 Вложения: " + html.escape(_cut(", ".join(names), MAX_ATTACHMENTS_LEN)))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

async def _get(path: str, params: dict) -> bytes:
    url = MAIL_INBOX_URL.rstrip("/") + path
    headers = {"Authorization": f"Bearer {MAIL_READ_TOKEN}"}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url, params=params, headers=headers) as resp:
                body = await resp.read()
                if resp.status != 200:
                    raise MailInboxError(f"{path}: HTTP {resp.status}: {body[:200]!r}")
                return body
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise MailInboxError(f"{path}: {type(e).__name__}: {e}") from e


async def list_messages(domain: str, address: Optional[str] = None) -> List[dict]:
    """10 последних писем, новые первыми: [{id, to, from, subject, receivedAt, size}].

    Без address — на любые адреса домена, с address — только на этот адрес.
    """
    body = await _get("/messages", {"address": address} if address else {"domain": domain})
    try:
        return json.loads(body)
    except ValueError as e:
        raise MailInboxError(f"/messages: ответ не JSON: {e}") from e


async def fetch_message(message_id: str) -> bytes:
    """Письмо целиком (.eml)."""
    return await _get("/message", {"id": message_id})
