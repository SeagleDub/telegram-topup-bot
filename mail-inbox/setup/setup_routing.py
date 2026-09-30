"""
Настройка почты доменов по CSV: письма на любой адрес домена → бот.

Для каждой строки: включает Email Routing на домене, заливает в аккаунт
пересыльщика mail-inbox-relay и направляет в него все адреса домена (catch-all).
Правила info@ от версии до catch-all удаляет — повторный запуск по тому же CSV
переводит на catch-all уже настроенные домены. Домены с чужой почтой, чужим
catch-all или своим SPF не трогает — только пишет причину в отчёт.
Устройство: docs/mail-inbox-design.md, раздел 5.

Запуск (из корня репозитория):
    .venv/bin/python mail-inbox/setup/setup_routing.py domains.csv --dry-run
    .venv/bin/python mail-inbox/setup/setup_routing.py domains.csv

CSV: колонка 1 — <email>:<token> (API Token или Global API Key), колонка 3 —
домен, разделитель «,» или «;». Для настоящего запуска нужен mail-inbox/.env
с MAIL_INBOX_URL и RELAY_TOKEN; --dry-run работает без него.
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from dotenv import load_dotenv

API_BASE = "https://api.cloudflare.com/client/v4"
RELAY_NAME = "mail-inbox-relay"
MAIL_INBOX_DIR = Path(__file__).resolve().parent.parent
RELAY_FILE = MAIL_INBOX_DIR / "relay" / "relay.mjs"
ENV_FILE = MAIL_INBOX_DIR / ".env"
COMPATIBILITY_DATE = "2026-09-01"
CF_MX_SUFFIX = ".mx.cloudflare.net"
CF_SPF_INCLUDE = "_spf.mx.cloudflare.net"
RULES_PER_PAGE = 50  # максимум API; по умолчанию было бы 20

OK = "✅ настроен"
ALREADY = "↩️ уже был настроен"
PLANNED = "🔎 будет настроен"
SKIPPED = "⏭ пропущен"
ERROR = "❌ ошибка"


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Row:
    line: int
    email: str
    token: str
    domain: str


def read_rows(path: Path) -> Tuple[List[Row], List[Tuple[int, str]]]:
    """CSV → (годные строки, [(номер строки, причина пропуска)])."""
    # utf-8-sig: Excel пишет BOM в начало файла.
    text = path.read_text(encoding="utf-8-sig")

    rows, skipped = [], []
    for line, raw_line in enumerate(text.splitlines(), start=1):
        # В «email:token» не бывает ни «,», ни «;», ни табуляции, поэтому первый
        # из этих символов в строке — её разделитель. csv.Sniffer тут ошибается:
        # русский Excel пишет «;», а «,» во второй колонке — десятичная запятая.
        delimiter = re.search(r"[,;\t]", raw_line)
        cells = next(csv.reader([raw_line], delimiter=delimiter.group() if delimiter else ","), [])
        if not any(cell.strip() for cell in cells):
            continue
        creds = cells[0].strip()
        if ":" not in creds:
            skipped.append((line, "в колонке 1 нет «email:token»"))
            continue
        if len(cells) < 3 or not cells[2].strip():
            skipped.append((line, "нет домена в колонке 3"))
            continue
        email, token = creds.split(":", 1)
        rows.append(Row(line, email.strip(), token.strip(), cells[2].strip().lower().rstrip(".")))
    return rows, skipped


# --------------------------------------------------------------------------- #
# Cloudflare API
# --------------------------------------------------------------------------- #

class CFError(Exception):
    """Ошибка Cloudflare API. status — HTTP-статус, None — сбой сети."""

    def __init__(self, status: Optional[int], message: str):
        super().__init__(message)
        self.status = status


def _errors_text(payload) -> str:
    errors = (payload or {}).get("errors") or []
    return "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errors) or "неизвестная ошибка"


class CloudflareAPI:
    """Клиент одной пары email:token. Тип токена определяет первый запрос."""

    def __init__(self, email: str, token: str):
        self.email = email
        self.token = token
        self.mode: Optional[str] = None  # "token" | "key"

    def call(self, method: str, path: str, body=None, *, raw: bytes = None, content_type: str = None):
        if self.mode:
            return self._send(self.mode, method, path, body, raw, content_type)
        # В CSV не сказано, что это: API Token или Global API Key. Пробуем как
        # API Token, при отказе в авторизации — как ключ. /user/tokens/verify
        # не годится: он не работает для токенов, выданных на аккаунт.
        try:
            result = self._send("token", method, path, body, raw, content_type)
            self.mode = "token"
            return result
        except CFError as as_token:
            if as_token.status not in (400, 401, 403):
                raise
            try:
                result = self._send("key", method, path, body, raw, content_type)
            except CFError as as_key:
                raise CFError(as_key.status, f"токен не принят — как API Token: {as_token}; "
                                             f"как Global API Key: {as_key}") from None
            self.mode = "key"
            return result

    def list_all(self, path: str, per_page: int) -> list:
        """GET с постраничным обходом."""
        items, page = [], 1
        sep = "&" if "?" in path else "?"
        while True:
            batch = self.call("GET", f"{path}{sep}page={page}&per_page={per_page}") or []
            items.extend(batch)
            if len(batch) < per_page:
                return items
            page += 1

    def _send(self, mode, method, path, body, raw, content_type):
        if mode == "token":
            headers = {"Authorization": f"Bearer {self.token}"}
        else:
            headers = {"X-Auth-Email": self.email, "X-Auth-Key": self.token}
        data = raw
        if body is not None:
            data, content_type = json.dumps(body).encode(), "application/json"
        if content_type:
            headers["Content-Type"] = content_type

        request = urllib.request.Request(API_BASE + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"{}")
            except ValueError:
                payload = None
            raise CFError(e.code, _errors_text(payload) if payload else f"HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise CFError(None, f"сеть: {e.reason}") from None
        if not payload.get("success"):
            raise CFError(200, _errors_text(payload))
        return payload.get("result")


# --------------------------------------------------------------------------- #
# Решение по домену
# --------------------------------------------------------------------------- #

@dataclass
class Plan:
    skip: Optional[str] = None
    enable_routing: bool = False
    set_catch_all: bool = False
    # Наше правило info@<домен> от версии до catch-all: catch-all его заменяет.
    delete_rules: List[dict] = field(default_factory=list)
    # Для отчёта: чужой catch-all, который перезапишется, и адреса, которые из-за
    # чужих правил до бота не дойдут. Иначе старая настройка теряется бесследно,
    # а байер видит пустой список без объяснения.
    replaced_catch_all: Optional[str] = None
    bypass: List[str] = field(default_factory=list)

    @property
    def nothing_to_do(self) -> bool:
        return not (self.enable_routing or self.set_catch_all or self.delete_rules)

    def describe(self) -> str:
        steps = ["включить Email Routing"] if self.enable_routing else []
        steps.append("залить пересыльщика")
        if self.set_catch_all:
            steps.append("направить все адреса домена в бота (catch-all)")
        if self.replaced_catch_all:
            steps.append(f"заменён чужой catch-all: {self.replaced_catch_all}")
        if self.delete_rules:
            steps.append("удалить старое правило info@")
        if self.bypass:
            steps.append("мимо бота (свои правила): " + "; ".join(self.bypass))
        return ", ".join(steps)


def _txt_value(record: dict) -> str:
    return (record.get("content") or "").strip().strip('"')


def _literal_addresses(rule: dict) -> List[str]:
    return [(m.get("value") or "").lower() for m in rule.get("matchers") or [] if m.get("type") == "literal"]


def _is_ours(rule: dict) -> bool:
    actions = rule.get("actions") or []
    return len(actions) == 1 and actions[0].get("type") == "worker" and actions[0].get("value") == [RELAY_NAME]


def _describe_rule(rule: dict) -> str:
    return ", ".join(
        f"{a.get('type')} → {', '.join(a.get('value') or [])}" for a in rule.get("actions") or []
    ) or "без действий"


def plan_domain(domain: str, mx_records: list, txt_records: list, routing_enabled: bool, rules: list,
                routing_status: str = "ready", catch_all: Optional[dict] = None) -> Plan:
    """Что делать с доменом. Чистая функция: вся логика «трогать или нет» здесь."""
    foreign_mx = sorted({
        # «.» — null MX (RFC 7505): домен явно не принимает почту. Тоже чужое
        # решение, и в отчёте оно должно быть видно, а не пустой строкой.
        (r.get("content") or "").rstrip(".") or "."
        for r in mx_records
        if r.get("name") == domain and not (r.get("content") or "").rstrip(".").lower().endswith(CF_MX_SUFFIX)
    })
    if foreign_mx:
        return Plan(skip="чужая почта (MX): " + ", ".join(foreign_mx))

    # Включённая, но сломанная маршрутизация (например, удалили MX): правило
    # создать можно, но письма не придут. Назвать такой домен «настроенным» —
    # значит спрятать проблему.
    if routing_enabled and routing_status != "ready":
        return Plan(skip=f"Email Routing включён, но статус «{routing_status}» — проверь DNS домена")

    # Свой SPF мешает только включению: Email Routing добавит вторую запись
    # v=spf1, а с двумя SPF-записями SPF домена недействителен.
    if not routing_enabled:
        own_spf = [
            _txt_value(r) for r in txt_records
            if r.get("name") == domain
            and _txt_value(r).lower().startswith("v=spf1")
            and CF_SPF_INCLUDE not in _txt_value(r)
        ]
        if own_spf:
            return Plan(skip="есть свой SPF: " + own_spf[0])

    catch_all_enabled = bool(catch_all and catch_all.get("enabled"))
    # Чужой catch-all уже доставляет почту домена кому-то — наш увёл бы её.
    if catch_all_enabled and not _is_ours(catch_all) and any(
            a.get("type") != "drop" for a in catch_all.get("actions") or []):
        return Plan(skip="catch-all уже настроен: " + _describe_rule(catch_all))

    set_catch_all = not (catch_all_enabled and _is_ours(catch_all))
    replaced = None
    if set_catch_all and catch_all and not _is_ours(catch_all) and (
            catch_all_enabled or any(a.get("type") != "drop" for a in catch_all.get("actions") or [])):
        replaced = _describe_rule(catch_all) + ("" if catch_all_enabled else " (был выключен)")

    return Plan(
        enable_routing=not routing_enabled,
        set_catch_all=set_catch_all,
        replaced_catch_all=replaced,
        # Только наше и только info@<домен> — его ставила версия до catch-all.
        # Правило на другой адрес или поддомен — ручное (catch-all покрывает
        # только сам домен), удалять его нельзя.
        delete_rules=[r for r in rules if _is_ours(r) and _literal_addresses(r) == [f"info@{domain}"]],
        # Чужие правила точнее catch-all и продолжат работать — эти адреса просто
        # не попадут в бота.
        bypass=[f"{', '.join(_literal_addresses(r))} → {_describe_rule(r)}"
                for r in rules if not _is_ours(r) and r.get("enabled", True) and _literal_addresses(r)],
    )


# --------------------------------------------------------------------------- #
# Заливка пересыльщика
# --------------------------------------------------------------------------- #

def build_relay_upload(ingest_url: str, relay_token: str, code: bytes) -> Tuple[bytes, str]:
    """Тело multipart для PUT /workers/scripts: metadata + модуль relay.mjs."""
    metadata = {
        "main_module": "relay.mjs",
        "compatibility_date": COMPATIBILITY_DATE,
        # Без флага запрос Worker→Worker в том же аккаунте блокируется
        # (ошибка 1042) — на случай, если домен лежит в аккаунте mail-inbox.
        "compatibility_flags": ["global_fetch_strictly_public"],
        "bindings": [
            {"type": "plain_text", "name": "INGEST_URL", "text": ingest_url},
            {"type": "secret_text", "name": "RELAY_TOKEN", "text": relay_token},
        ],
    }
    boundary = uuid.uuid4().hex
    parts = [
        ('name="metadata"', "application/json", json.dumps(metadata).encode()),
        ('name="relay.mjs"; filename="relay.mjs"', "application/javascript+module", code),
    ]
    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; {disposition}\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n"
        for disposition, ctype, data in parts
    ) + f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


# --------------------------------------------------------------------------- #
# Обработка строки
# --------------------------------------------------------------------------- #

def process(row: Row, api: CloudflareAPI, config: Optional[dict], uploaded: set, dry_run: bool) -> Tuple[str, str]:
    zones = api.call("GET", "/zones?" + urllib.parse.urlencode({"name": row.domain}))
    if not zones:
        return SKIPPED, "домен не найден в аккаунте"
    zone = zones[0]
    if zone.get("status") != "active":
        return SKIPPED, f"домен не активен (статус {zone.get('status')}): NS не на Cloudflare"
    zone_id, account_id = zone["id"], zone["account"]["id"]

    mx = api.call("GET", f"/zones/{zone_id}/dns_records?type=MX&per_page=100") or []
    txt = api.call("GET", f"/zones/{zone_id}/dns_records?"
                   + urllib.parse.urlencode({"type": "TXT", "name": row.domain, "per_page": 100})) or []
    try:
        routing = api.call("GET", f"/zones/{zone_id}/email/routing") or {}
    except CFError as e:
        if e.status != 404:
            raise
        routing = {}
    rules = api.list_all(f"/zones/{zone_id}/email/routing/rules", RULES_PER_PAGE)
    try:
        catch_all = api.call("GET", f"/zones/{zone_id}/email/routing/rules/catch_all")
    except CFError as e:
        if e.status != 404:
            raise
        catch_all = None

    plan = plan_domain(row.domain, mx, txt, bool(routing.get("enabled")), rules,
                       routing_status=routing.get("status", "ready"), catch_all=catch_all)
    if plan.skip:
        return SKIPPED, plan.skip
    if dry_run:
        return (ALREADY if plan.nothing_to_do else PLANNED), plan.describe()

    # Сначала пересыльщик, потом DNS: если заливка не удастся (раздел 9
    # спеки), домен останется нетронутым, а не с заблокированными MX без
    # обработчика. Раз на аккаунт за запуск; перезаливка безопасна — так же
    # обновляется код пересыльщика во всех аккаунтах.
    if account_id not in uploaded:
        body, content_type = build_relay_upload(config["ingest_url"], config["relay_token"], config["relay_code"])
        api.call("PUT", f"/accounts/{account_id}/workers/scripts/{RELAY_NAME}", raw=body, content_type=content_type)
        uploaded.add(account_id)
    if plan.enable_routing:
        # Без name: сам домен включается по умолчанию, а name — только для
        # поддоменов (сам домен в нём Cloudflare отвергает ошибкой 2007).
        api.call("POST", f"/zones/{zone_id}/email/routing/dns", {})
    if plan.set_catch_all:
        api.call("PUT", f"/zones/{zone_id}/email/routing/rules/catch_all", {
            "name": f"все адреса → {RELAY_NAME}",
            "enabled": True,
            "matchers": [{"type": "all"}],
            "actions": [{"type": "worker", "value": [RELAY_NAME]}],
        })
    # Старые правила — только после catch-all: иначе письма на info@ в
    # промежутке не попали бы никуда.
    for rule in plan.delete_rules:
        try:
            api.call("DELETE", f"/zones/{zone_id}/email/routing/rules/{rule['id']}")
        except CFError as e:
            # 404 — правила уже нет (например, прошлый запуск упал после
            # удаления): домен в нужном состоянии, это не ошибка.
            if e.status != 404:
                raise
    return (ALREADY if plan.nothing_to_do else OK), plan.describe()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Настройка почты доменов (catch-all) по CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="только проверить и показать план, ничего не менять")
    args = parser.parse_args(argv)

    config = None
    if not args.dry_run:
        load_dotenv(ENV_FILE)
        # removesuffix: частая ошибка — вписать полный адрес с /ingest; тогда все
        # пересыльщики слали бы на /ingest/ingest, получали 404 и теряли письма.
        base_url = (os.getenv("MAIL_INBOX_URL") or "").rstrip("/").removesuffix("/ingest")
        relay_token = os.getenv("RELAY_TOKEN") or ""
        if not base_url.startswith("https://") or not relay_token:
            print(f"Нужны MAIL_INBOX_URL (https://…) и RELAY_TOKEN в {ENV_FILE}", file=sys.stderr)
            return 2
        config = {"ingest_url": f"{base_url}/ingest", "relay_token": relay_token,
                  "relay_code": RELAY_FILE.read_bytes()}

    rows, skipped = read_rows(args.csv_path)
    report = []
    for line, reason in skipped:
        report.append((f"строка {line}", SKIPPED, reason))
        print(f"{SKIPPED}  строка {line}  {reason}")

    apis, uploaded = {}, set()
    for row in rows:
        api = apis.setdefault((row.email, row.token), CloudflareAPI(row.email, row.token))
        try:
            status, reason = process(row, api, config, uploaded, args.dry_run)
        except CFError as e:
            status, reason = ERROR, str(e)
        except Exception as e:  # одна битая строка не должна останавливать весь CSV
            status, reason = ERROR, f"{type(e).__name__}: {e}"
        report.append((row.domain, status, reason))
        print(f"{status}  {row.domain}  {reason}", flush=True)

    report_path = args.csv_path.with_name(f"{args.csv_path.stem}.report.csv")
    # utf-8-sig: без BOM русский Excel показывает кириллицу кракозябрами.
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["domain", "status", "reason"])
        writer.writerows(report)

    counts = {}
    for _, status, _ in report:
        counts[status] = counts.get(status, 0) + 1
    print("\nИтого: " + ", ".join(f"{status} — {n}" for status, n in counts.items()))
    print(f"Отчёт: {report_path}")
    return 1 if ERROR in counts else 0


if __name__ == "__main__":
    sys.exit(main())
