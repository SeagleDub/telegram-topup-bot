# mail-inbox — почта info@<domain> для бота

Письма на `info@<domain>` доменов из CSV попадают в раздел бота «📧 Почта доменов».
Дизайн и причины решений: [docs/mail-inbox-design.md](../docs/mail-inbox-design.md).

```
письмо → mail-inbox-relay (в аккаунте домена) → mail-inbox (приёмник) → R2 → бот
```

| Папка | Что это | Как попадает в Cloudflare |
|-------|---------|---------------------------|
| `collector/` | приёмник `mail-inbox` | `npm run deploy`, один раз |
| `relay/` | пересыльщик `mail-inbox-relay` | скрипт `setup/`, в каждый аккаунт |
| `setup/` | настройка аккаунтов по CSV | запускается локально |

## Развёртывание приёмника (один раз)

```bash
cd mail-inbox/collector
npm install
npx wrangler r2 bucket create mail-inbox
npx wrangler r2 bucket lifecycle add mail-inbox expire-30d --expire-days 30 -y
npm run deploy                          # напечатает адрес https://mail-inbox.<…>.workers.dev
npx wrangler secret put RELAY_TOKEN     # openssl rand -hex 32
npx wrangler secret put READ_TOKEN      # openssl rand -hex 32, другой
```

## Секреты

| Где | Что |
|-----|-----|
| Worker `mail-inbox` | `RELAY_TOKEN`, `READ_TOKEN` (wrangler secret) |
| `mail-inbox/.env` | `MAIL_INBOX_URL`, `RELAY_TOKEN` — для скрипта |
| `.env` бота | `MAIL_INBOX_URL`, `MAIL_READ_TOKEN` (= `READ_TOKEN`) |

## Настройка доменов

```bash
.venv/bin/python mail-inbox/setup/setup_routing.py domains.csv --dry-run
.venv/bin/python mail-inbox/setup/setup_routing.py domains.csv
```

CSV: колонка 1 — `email:token`, колонка 3 — домен. Отчёт — `domains.report.csv`
рядом с CSV. Повторный запуск безопасен и обновляет пересыльщика во всех аккаунтах.
Права API Token: Zone Read, DNS Read, Zone Settings Edit, Email Routing Rules Edit,
Workers Scripts Edit.

`сеть: [SSL: CERTIFICATE_VERIFY_FAILED]` на Mac — Python установлен с python.org
и не видит сертификаты macOS. Один раз:

```bash
"/Applications/Python 3.13/Install Certificates.command"
```

## Тесты

```bash
node --test "mail-inbox/relay/*.test.mjs"
(cd mail-inbox/collector && npm test)
.venv/bin/python -m unittest discover -s mail-inbox/setup -v
```
