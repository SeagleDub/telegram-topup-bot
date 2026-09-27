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
import re
from datetime import datetime
from email import policy
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import List, Optional

import aiohttp

from config import MAIL_INBOX_URL, MAIL_READ_TOKEN
from services.ecards import KYIV_TZ

logger = logging.getLogger(__name__)

# Та же проверка, что в mail-inbox/collector/src/index.js (ADDRESS_RE).
_ADDRESS_RE = re.compile(r"^[a-z0-9._+-]{1,64}@(?:[a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}$")

# ponytail: лимиты в символах Python, а Telegram считает 4096 единиц UTF-16
# (эмодзи — две). Худший случай без эмодзи ~3800, запас ~300 покрывает обычные
# письма. Если упрётся — резать тело по длине в UTF-16.
MAX_TEXT_CHARS = 3000
MAX_HEADER_CHARS = 200
MAX_ATTACHMENTS_CHARS = 300
MAX_LABEL_CHARS = 60

_TIMEOUT = aiohttp.ClientTimeout(total=20)


class MailInboxError(Exception):
    """mail-inbox недоступен или ответил ошибкой."""


def is_configured() -> bool:
    return bool(MAIL_INBOX_URL and MAIL_READ_TOKEN)


# --------------------------------------------------------------------------- #
# Ввод пользователя и список
# --------------------------------------------------------------------------- #

def normalize_address(text: str) -> str:
    """site1.com / info@Site1.com / https://www.site1.com/x → info@site1.com.

    ValueError — если на домен не похоже.
    """
    value = (text or "").strip().lower()
    local, _, domain = value.rpartition("@") if "@" in value else ("info", "@", value)
    domain = re.sub(r"^[a-z][a-z0-9+.-]*://", "", domain)
    domain = re.split(r"[/?#:]", domain, maxsplit=1)[0].strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    try:
        # Кириллический домен (сайт.укр) в адресах писем — в punycode.
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"не похоже на домен: {text!r}") from None
    address = f"{local}@{domain}"
    if not _ADDRESS_RE.match(address):
        raise ValueError(f"не похоже на домен: {text!r}")
    return address


def decode_header_value(value: Optional[str]) -> str:
    """=?UTF-8?B?…?= → текст. Битый заголовок возвращается как есть."""
    try:
        return str(make_header(decode_header(value or "")))
    except (HeaderParseError, LookupError, ValueError):
        return value or ""


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def button_label(item: dict, now: datetime) -> str:
    """«12:41 · Google · Код подтверждения» — подпись кнопки письма в списке."""
    received = datetime.fromtimestamp(item["receivedAt"] / 1000, KYIV_TZ)
    when = received.strftime("%H:%M" if received.date() == now.date() else "%d.%m %H:%M")
    name, addr = parseaddr(decode_header_value(item.get("from")))
    subject = decode_header_value(item.get("subject")) or "(без темы)"
    return _cut(f"{when} · {name or addr or '?'} · {subject}", MAX_LABEL_CHARS)


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


def _body_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeError):
        # Неизвестная или неверно указанная кодировка — лучше кракозябры, чем ничего.
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return _tidy(content)


def render_message(raw: bytes, received_at_ms: Optional[int]) -> str:
    """Письмо (.eml) → текст для Telegram (parse_mode=HTML). Всё из письма экранируется."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    subject = _cut(str(msg.get("subject") or "").strip() or "(без темы)", MAX_HEADER_CHARS)
    sender = _cut(str(msg.get("from") or "").strip() or "?", MAX_HEADER_CHARS)

    lines = [f"✉️ <b>{html.escape(subject)}</b>", f"От: {html.escape(sender)}"]
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
        if len(body) > MAX_TEXT_CHARS:
            body = body[:MAX_TEXT_CHARS].rstrip() + "\n…обрезано"
        lines.append(html.escape(body))

    names = [part.get_filename() or "без имени" for part in msg.iter_attachments()]
    if names:
        lines.append("")
        lines.append("📎 Вложения (не пересылаются): " + html.escape(_cut(", ".join(names), MAX_ATTACHMENTS_CHARS)))
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


async def list_messages(address: str) -> List[dict]:
    """10 последних писем адреса, новые первыми: [{id, from, subject, receivedAt, size}]."""
    body = await _get("/messages", {"address": address})
    try:
        return json.loads(body)
    except ValueError as e:
        raise MailInboxError(f"/messages: ответ не JSON: {e}") from e


async def fetch_message(message_id: str) -> bytes:
    """Письмо целиком (.eml)."""
    return await _get("/message", {"id": message_id})
