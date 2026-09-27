# Почта доменов — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Цель:** письма на `info@<domain>` для доменов из CSV видны в боте по кнопке «📧 Почта доменов».

**Архитектура:** в каждом Cloudflare-аккаунте из CSV — пересыльщик `mail-inbox-relay`
(email-Worker), он сдаёт письмо центральному Worker'у `mail-inbox`, тот кладёт его
в приватный R2-бакет. Бот читает письма через API `mail-inbox` и разбирает MIME
стандартным модулем `email`. Аккаунты настраивает скрипт по CSV.

**Стек:** Cloudflare Workers (JS, модули), R2, Cloudflare REST API v4;
Python 3.13 (aiogram 3.22, aiohttp, стандартная библиотека); тесты — `node --test`
(Node 24) и `unittest`.

**Спека:** `docs/mail-inbox-design.md` — причины всех решений там.

## Глобальные ограничения

- Новых Python-зависимостей нет: только стандартная библиотека + уже установленные `aiohttp`, `python-dotenv`.
- JS-тесты — только встроенные модули Node (`node:test`, `node:assert`). `wrangler ^4.86.0` — devDependency только в `mail-inbox/collector`.
- Имена: Worker `mail-inbox`, бакет `mail-inbox`, биндинг R2 `MAIL`, пересыльщик `mail-inbox-relay`, секреты Worker'а `RELAY_TOKEN` / `READ_TOKEN`.
- Переменные: бот (`.env`) — `MAIL_INBOX_URL`, `MAIL_READ_TOKEN`; скрипт (`mail-inbox/.env`) — `MAIL_INBOX_URL`, `RELAY_TOKEN`.
- Проверка адреса одинакова в JS и Python: `^[a-z0-9._+-]{1,64}@(?:[a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}$`.
- Константы: список — 10 писем; хранение — 30 дней; текст письма — до 3000 символов; тема/отправитель — до 200; вложения — до 300; подпись кнопки — до 60; троттлинг `mail` — 10 в минуту.
- `compatibility_date = "2026-09-01"` (как у `video-cloud`).
- Комментарии и тексты для пользователя — по-русски, в стиле репозитория (комментарий объясняет «почему»).
- `cloudflare/` (video-cloud) не меняется.

## Карта файлов

| Файл | Что делает |
|------|-----------|
| `mail-inbox/relay/relay.mjs` | пересыльщик: письмо → `POST /ingest`, 3 попытки |
| `mail-inbox/relay/relay.test.mjs` | тесты пересыльщика |
| `mail-inbox/collector/src/index.js` | приёмник: `/ingest`, `/messages`, `/message`, R2 |
| `mail-inbox/collector/test/collector.test.mjs` | тесты приёмника (R2 в памяти) |
| `mail-inbox/collector/wrangler.toml`, `package.json` | деплой приёмника |
| `mail-inbox/setup/setup_routing.py` | CSV → настройка аккаунтов через API |
| `mail-inbox/setup/test_setup_routing.py` | тесты скрипта |
| `mail-inbox/README.md`, `mail-inbox/.gitignore` | инструкция; CSV и отчёты вне git |
| `services/mail_inbox.py` | бот: HTTP-клиент к приёмнику + разбор писем |
| `handlers/mail_inbox.py` | бот: флоу кнопка → домен → список → письмо |
| `tests/test_mail_inbox.py` | тесты разбора и нормализации |
| `config.py`, `keyboards.py`, `states.py`, `middlewares/throttle.py`, `main.py` | обвязка |

Ветка: `feature/mail-inbox`.

## Статус

| Задача | Состояние |
|--------|-----------|
| 1–6. Код | ✅ тесты: пересыльщик 7, приёмник 6, скрипт 27, бот 89 (73 старых + 16 новых) |
| Ревью | ✅ отдельным агентом, правки ниже |
| 7. Запуск | ⏳ за пользователем |

### Правки по результатам ревью

Ревью: 0 критичных, 3 важных, 9 мелких замечаний. Принято всё, кроме п. 10 —
он оставлен осознанно. **Код в блоках задач ниже — версия до ревью; источник
истины — код в репозитории.**

1. CSV с доступами к аккаунтам не игнорировался в корне репозитория, откуда
   запускается скрипт → `*.csv` в корневом `.gitignore`.
2. Catch-all не проверялся: включённый catch-all уже доставляет и info@, а
   наше правило молча увело бы эти письма → такой домен пропускается.
3. `csv.Sniffer` выбирал `,` на файлах русского Excel (десятичная запятая во
   2-й колонке) → разделитель — первый из `,` `;` табуляция в строке.
4. Бакет троттлинга `mail` для любого текста в состоянии почты: после частых
   «Обновить» не нажималась «❌ Отмена» → правило по состоянию убрано; кнопка
   меню и callback'и `mail:` остались в бакете `mail`.
5. «Обновить» сохраняло новый список до правки сообщения; при сбое правки
   кнопки открывали не те письма → сохранение после успешной правки.
6. Ошибка Telegram после ответа на нажатие оставляла тишину → короткий отказ;
   недоступное сообщение (`InaccessibleMessage`) → «список устарел».
7. Включённый, но сломанный Email Routing (статус ≠ `ready`) назывался
   «настроенным» → в отчёт; пересыльщик заливается до изменений DNS.
8. Огромная тема письма раздувала `X-Mail-Meta` сверх лимита Cloudflare, 4xx
   без повтора — потеря письма → обрезка до 200 символов в пересыльщике.
9. Разбор писем: пустой text/plain при HTML с кодом; слипшиеся ячейки таблиц;
   отправитель с запятой в имени → исправлено, лимиты длины — в UTF-16.
10. Один `RELAY_TOKEN` на все аккаунты позволяет подделывать письма при утечке
    из любого аккаунта — **оставлено**: к аккаунтам имеет доступ только команда.
    Риск и путь перехода — в спеке, раздел 4.
11. Не было тестов авторизации, пагинации и порядка шагов скрипта → добавлены.
12. Отчёт с BOM (для Excel), читаемый null MX, `MAIL_INBOX_URL` с `/ingest`.

---

### Задача 1: Пересыльщик `mail-inbox-relay`

**Файлы:**
- Создать: `mail-inbox/relay/relay.mjs`
- Создать: `mail-inbox/relay/relay.test.mjs`
- Создать: `mail-inbox/.gitignore`

**Интерфейсы:**
- Потребляет: ничего.
- Производит: `export default { email(message, env) }`; биндинги `INGEST_URL`, `RELAY_TOKEN`; запрос `POST {INGEST_URL}` с `Authorization: Bearer <RELAY_TOKEN>` и `X-Mail-Meta: encodeURIComponent(JSON.stringify({to, from, subject}))`, тело — письмо байт-в-байт. Задача 2 принимает ровно это; задача 3 заливает файл `relay.mjs` с этими биндингами.

- [ ] **Шаг 1: ветка и .gitignore**

```bash
git checkout -b feature/mail-inbox
mkdir -p mail-inbox/relay
```

`mail-inbox/.gitignore`:
```
# CSV с токенами аккаунтов и отчёты скрипта — только локально
*.csv
# служебные файлы wrangler
.wrangler/
```

- [ ] **Шаг 2: тест (падает)**

`mail-inbox/relay/relay.test.mjs`:
```js
/**
 * Тесты пересыльщика mail-inbox-relay.
 *
 * Проверяется то, что при ошибке тихо теряет письма: что именно уходит в
 * mail-inbox (адрес, метаданные, тело) и поведение при сбоях — повторяется
 * только то, что повтор может исправить, а в конце пересыльщик падает, а не
 * глотает ошибку.
 *
 * Запуск: node --test "mail-inbox/relay/*.test.mjs"
 * (именно шаблон: папку Node 24 принимает за модуль и не запускает).
 * Два теста ждут настоящие паузы между попытками (1 и 3 с).
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";

import relay from "./relay.mjs";

const ENV = { INGEST_URL: "https://mail-inbox.test/ingest", RELAY_TOKEN: "relay-token" };
const RAW = "From: Google <no-reply@accounts.google.com>\r\nSubject: test\r\n\r\nВаш код: 123456";
const realFetch = globalThis.fetch;

function fakeMessage(subject = "=?UTF-8?B?0JrQvtC0?=", from = "Google <no-reply@accounts.google.com>") {
  const headers = new Map([["subject", subject], ["from", from]]);
  return {
    to: "info@site1.com",
    from: "bounce-123@mail.google.com",
    headers: { get: (name) => headers.get(name.toLowerCase()) ?? null },
    raw: new Blob([RAW]).stream(),
  };
}

/** Подменяет fetch: ответы по очереди (число — HTTP-статус, Error — сбой сети). */
function stubFetch(outcomes) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    const next = outcomes[Math.min(calls.length, outcomes.length) - 1];
    if (next instanceof Error) throw next;
    return new Response("{}", { status: next });
  };
  return calls;
}

const metaOf = (call) => JSON.parse(decodeURIComponent(call.init.headers["x-mail-meta"]));

afterEach(() => {
  globalThis.fetch = realFetch;
});

test("сдаёт письмо: адрес, токен, метаданные, тело без изменений", async () => {
  const calls = stubFetch([200]);
  await relay.email(fakeMessage(), ENV);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, ENV.INGEST_URL);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers.authorization, "Bearer relay-token");
  assert.deepEqual(metaOf(calls[0]), {
    to: "info@site1.com",
    from: "Google <no-reply@accounts.google.com>",
    subject: "=?UTF-8?B?0JrQvtC0?=",
  });
  assert.equal(new TextDecoder().decode(calls[0].init.body), RAW);
});

test("кириллическая тема уходит в заголовке только ASCII-символами", async () => {
  const calls = stubFetch([200]);
  await relay.email(fakeMessage("Код подтверждения"), ENV);

  assert.match(calls[0].init.headers["x-mail-meta"], /^[\x21-\x7e]+$/);
  assert.equal(metaOf(calls[0]).subject, "Код подтверждения");
});

test("без заголовка From берётся адрес конверта", async () => {
  const calls = stubFetch([200]);
  await relay.email(fakeMessage("s", null), ENV);
  assert.equal(metaOf(calls[0]).from, "bounce-123@mail.google.com");
});

test("5xx повторяется: успех со второй попытки", async () => {
  const calls = stubFetch([503, 200]);
  await relay.email(fakeMessage(), ENV);
  assert.equal(calls.length, 2);
});

test("сеть лежит: 3 попытки, потом исключение", async () => {
  const calls = stubFetch([new Error("network down")]);
  await assert.rejects(relay.email(fakeMessage(), ENV), /network down/);
  assert.equal(calls.length, 3);
});

test("4xx не повторяется: одна попытка и исключение", async () => {
  const calls = stubFetch([401]);
  await assert.rejects(relay.email(fakeMessage(), ENV), /mail-inbox ответил 401/);
  assert.equal(calls.length, 1);
});
```

- [ ] **Шаг 3: убедиться, что падает**

Run: `node --test "mail-inbox/relay/*.test.mjs"`
Expected: FAIL — `Cannot find module .../relay.mjs`.

- [ ] **Шаг 4: реализация**

`mail-inbox/relay/relay.mjs`:
```js
/**
 * mail-inbox-relay — пересыльщик почты info@<domain>.
 *
 * Стоит в каждом Cloudflare-аккаунте из CSV (заливает его
 * mail-inbox/setup/setup_routing.py, не wrangler). Правило Email Routing
 * «info@<domain> → mail-inbox-relay» отдаёт ему письмо, он сдаёт письмо
 * центральному Worker'у mail-inbox и больше ничего не делает.
 *
 * Зачем он нужен: Email Routing отдаёт письмо только Worker'у того же
 * аккаунта, а mail-inbox живёт в другом.
 *
 * Почему письмо не разбирается здесь: на бесплатном плане у Worker'а 10 мс CPU,
 * и тяжёлый email-обработчик падает с EXCEEDED_CPU. Пересылка — ввод-вывод,
 * CPU почти не тратит. Разбирает бот (стандартный модуль email).
 *
 * Биндинги (задаёт скрипт при заливке):
 *   INGEST_URL  — https://mail-inbox.<поддомен>.workers.dev/ingest
 *   RELAY_TOKEN — токен записи (секрет)
 */

/** Паузы перед 2-й и 3-й попыткой. */
const RETRY_DELAYS_MS = [1000, 3000];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export default {
  async email(message, env) {
    // Буфер, а не поток: тело уйдёт с Content-Length, и тот же буфер можно
    // отправить повторно. Письмо не больше 25 МиБ (лимит Email Routing).
    const raw = await new Response(message.raw).arrayBuffer();

    // Метаданные — одним заголовком в URL-кодировке: тема и адрес бывают не
    // латиницей, а HTTP-заголовок пропускает только Latin-1 — fetch упал бы на
    // кириллической теме. From — из заголовка письма («Google <no-reply@…>»),
    // а не из конверта: в конверте обычно адрес для отказов.
    const meta = encodeURIComponent(
      JSON.stringify({
        to: message.to,
        from: message.headers.get("from") || message.from,
        subject: message.headers.get("subject") || "",
      }),
    );
    const init = {
      method: "POST",
      headers: { authorization: `Bearer ${env.RELAY_TOKEN}`, "x-mail-meta": meta },
      body: raw,
    };

    let lastError;
    for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
      if (attempt > 0) await sleep(RETRY_DELAYS_MS[attempt - 1]);
      try {
        const res = await fetch(env.INGEST_URL, init);
        if (res.ok) return;
        lastError = new Error(`mail-inbox ответил ${res.status}`);
        // 4xx — ошибка в самом запросе (токен, адрес, размер), повтор её не
        // исправит. Повторяются только сбои сети и 5xx.
        if (res.status < 500) break;
      } catch (e) {
        lastError = e;
      }
    }
    // Исключение — сигнал Cloudflare, что письмо не обработано. Что он делает
    // дальше (временный отказ отправителю или потеря), проверяется на тестовом
    // домене: docs/mail-inbox-design.md, раздел 9.
    throw lastError;
  },
};
```

- [ ] **Шаг 5: тесты проходят**

Run: `node --test "mail-inbox/relay/*.test.mjs"`
Expected: PASS, 6 тестов (~5 с из-за пауз).

- [ ] **Шаг 6: коммит**

```bash
git add mail-inbox/.gitignore mail-inbox/relay/
git commit -m "feat(mail-inbox): add email relay worker"
```

---

### Задача 2: Приёмник `mail-inbox`

**Файлы:**
- Создать: `mail-inbox/collector/package.json`
- Создать: `mail-inbox/collector/wrangler.toml`
- Создать: `mail-inbox/collector/src/index.js`
- Создать: `mail-inbox/collector/test/collector.test.mjs`

**Интерфейсы:**
- Потребляет: запрос пересыльщика из задачи 1 (`X-Mail-Meta` с `{to, from, subject}`, тело — письмо).
- Производит (для задачи 4):
  - `GET /messages?address=<addr>` + `Authorization: Bearer <READ_TOKEN>` → `200` JSON-массив до 10 объектов `{id: string, from: string, subject: string, receivedAt: number (мс), size: number}`, новые первыми;
  - `GET /message?id=<id>` → `200`, тело — письмо, `content-type: message/rfc822`;
  - ошибки: `400` ввод, `401` токен, `404` нет письма/пути, `500` прочее.
  - экспорт для тестов: `buildKey(address, now)`, `receivedAt(key)`.

- [ ] **Шаг 1: package.json и wrangler.toml**

`mail-inbox/collector/package.json`:
```json
{
  "name": "mail-inbox-collector",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "test": "node --test \"test/*.test.mjs\"",
    "deploy": "wrangler deploy",
    "tail": "wrangler tail"
  },
  "devDependencies": {
    "wrangler": "^4.86.0"
  }
}
```

`mail-inbox/collector/wrangler.toml`:
```toml
name = "mail-inbox"
main = "src/index.js"
compatibility_date = "2026-09-01"

# Письма. Бакет приватный: публичного доступа (r2.dev, свой домен) у него нет
# и быть не должно — в письмах коды подтверждения. Создаётся командой:
#   npx wrangler r2 bucket create mail-inbox
# Письма старше 30 дней удаляет правило бакета:
#   npx wrangler r2 bucket lifecycle add mail-inbox expire-30d --expire-days 30 -y
[[r2_buckets]]
binding = "MAIL"
bucket_name = "mail-inbox"

# Секреты в этот файл не пишутся — он в git. Задаются командами:
#   npx wrangler secret put RELAY_TOKEN   (запись: пересыльщики)
#   npx wrangler secret put READ_TOKEN    (чтение: бот)
```

- [ ] **Шаг 2: тест (падает)**

`mail-inbox/collector/test/collector.test.mjs`:
```js
/**
 * Тесты mail-inbox.
 *
 * Покрыто то, что ломается тихо:
 *   - порядок ключей: ошибка в «перевёрнутом» времени покажет в боте старые
 *     письма вместо новых, и заметят это, только когда потеряется код;
 *   - разделение токенов: токен записи из чужого аккаунта не должен читать
 *     почту, а незаданный секрет не должен открывать всё.
 *
 * R2 заменён словарём в памяти — логика Worker'а от этого не меняется.
 * Запуск: npm test
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import worker, { buildKey, receivedAt } from "../src/index.js";

const RELAY_TOKEN = "relay-token";
const READ_TOKEN = "read-token";
const META = { to: "Info@Site1.com", from: "Google <no-reply@google.com>", subject: "Код" };

/** Минимальная замена R2: put / list (по алфавиту, с limit) / get. */
class FakeR2 {
  constructor() {
    this.objects = new Map();
  }
  async put(key, body, { customMetadata } = {}) {
    this.objects.set(key, { body: new Uint8Array(body), customMetadata });
  }
  async list({ prefix, limit }) {
    const keys = [...this.objects.keys()].filter((k) => k.startsWith(prefix)).sort().slice(0, limit);
    return {
      objects: keys.map((key) => ({
        key,
        size: this.objects.get(key).body.byteLength,
        customMetadata: this.objects.get(key).customMetadata,
      })),
    };
  }
  async get(key) {
    const o = this.objects.get(key);
    return o ? { body: o.body } : null;
  }
}

const makeEnv = () => ({ MAIL: new FakeR2(), RELAY_TOKEN, READ_TOKEN });

function ingest(env, { token = RELAY_TOKEN, meta, body = "Subject: hi\r\n\r\nbody" } = {}) {
  const headers = { authorization: `Bearer ${token}` };
  if (meta !== undefined) headers["x-mail-meta"] = encodeURIComponent(JSON.stringify(meta));
  return worker.fetch(new Request("https://mail-inbox.test/ingest", { method: "POST", headers, body }), env);
}

function get(env, path, token = READ_TOKEN) {
  return worker.fetch(
    new Request(`https://mail-inbox.test${path}`, { headers: { authorization: `Bearer ${token}` } }),
    env,
  );
}

test("buildKey: более позднее письмо идёт раньше, время восстанавливается из ключа", () => {
  const earlier = buildKey("info@site1.com", 1_790_000_000_000);
  const later = buildKey("info@site1.com", 1_790_000_000_001);
  assert.ok(later < earlier);
  assert.equal(receivedAt(earlier), 1_790_000_000_000);
  assert.match(earlier, /^info@site1\.com\/\d{13}-[0-9a-f]{8}\.eml$/);
});

test("письмо сохраняется и отдаётся: список с метаданными и письмо целиком", async () => {
  const env = makeEnv();
  assert.equal((await ingest(env, { meta: META })).status, 200);

  const res = await get(env, "/messages?address=info@site1.com");
  assert.equal(res.status, 200);
  const items = await res.json();
  assert.equal(items.length, 1);
  assert.equal(items[0].from, META.from);
  assert.equal(items[0].subject, "Код");
  assert.equal(typeof items[0].receivedAt, "number");

  const raw = await get(env, `/message?id=${encodeURIComponent(items[0].id)}`);
  assert.equal(raw.status, 200);
  assert.equal(raw.headers.get("content-type"), "message/rfc822");
  assert.equal(await raw.text(), "Subject: hi\r\n\r\nbody");
});

test("список: новые письма первыми, не больше 10", async () => {
  const env = makeEnv();
  for (let i = 0; i < 12; i++) {
    await env.MAIL.put(buildKey("info@site1.com", 1_790_000_000_000 + i), new TextEncoder().encode(`m${i}`), {
      customMetadata: { from: "x", subject: `s${i}` },
    });
  }
  const items = await (await get(env, "/messages?address=info@site1.com")).json();
  assert.equal(items.length, 10);
  assert.equal(items[0].subject, "s11");
  assert.equal(items[9].subject, "s2");
});

test("токен записи не читает, токен чтения не пишет", async () => {
  const env = makeEnv();
  assert.equal((await ingest(env, { token: READ_TOKEN, meta: META })).status, 401);
  assert.equal((await get(env, "/messages?address=info@site1.com", RELAY_TOKEN)).status, 401);
  assert.equal(env.MAIL.objects.size, 0);
});

test("незаданные секреты закрывают всё, даже «Bearer undefined»", async () => {
  const env = { MAIL: new FakeR2() };
  assert.equal((await ingest(env, { token: "undefined", meta: META })).status, 401);
  assert.equal((await get(env, "/messages?address=info@site1.com", "undefined")).status, 401);
});

test("мусор на входе → 400, нет письма или пути → 404", async () => {
  const env = makeEnv();
  assert.equal((await ingest(env, { meta: { to: "не адрес" } })).status, 400);
  assert.equal((await ingest(env, {})).status, 400);
  assert.equal((await ingest(env, { meta: META, body: "" })).status, 400);
  assert.equal((await get(env, "/messages?address=../etc")).status, 400);
  assert.equal((await get(env, "/message?id=whatever")).status, 400);
  assert.equal((await get(env, `/message?id=${encodeURIComponent(buildKey("info@site1.com"))}`)).status, 404);
  assert.equal((await get(env, "/")).status, 404);
});
```

- [ ] **Шаг 3: убедиться, что падает**

Run: `cd mail-inbox/collector && npm test`
Expected: FAIL — `Cannot find module .../src/index.js`.

- [ ] **Шаг 4: реализация**

`mail-inbox/collector/src/index.js`:
```js
/**
 * mail-inbox — приёмник писем для раздела бота «📧 Почта доменов».
 *
 * Письма на info@<domain> приходят сюда от пересыльщиков mail-inbox-relay
 * (по одному в каждом Cloudflare-аккаунте), хранятся в приватном R2-бакете и
 * отдаются боту по запросу. Устройство и причины: docs/mail-inbox-design.md.
 *
 *   POST /ingest              — пересыльщик сдаёт письмо (RELAY_TOKEN)
 *   GET  /messages?address=…  — бот берёт 10 последних карточек (READ_TOKEN)
 *   GET  /message?id=…        — бот берёт письмо целиком (READ_TOKEN)
 *
 * Токенов два намеренно: RELAY_TOKEN лежит в чужих аккаунтах, и его утечка
 * должна давать только запись (то же может любой отправитель письма), но не
 * чтение почты.
 */

/** Потолок тела: лимит Email Routing 25 МиБ плюс запас. */
const MAX_BYTES = 26 * 1024 * 1024;

/** Сколько писем отдаётся в список. */
const LIST_LIMIT = 10;

/**
 * Основание «перевёрнутого» времени в ключе. R2 отдаёт ключи по алфавиту, а
 * 9999999999999 − Date.now() со временем убывает: новые письма идут первыми,
 * и 10 последних — это один list с limit: 10. 13 цифр хватит до 2286 года.
 */
const REV_BASE = 9999999999999;

/** Та же проверка адреса, что в боте (services/mail_inbox.py). */
const ADDRESS_RE = /^[a-z0-9._+-]{1,64}@(?:[a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}$/;

/** Ключ письма: <address>/<revTs>-<rand>.eml */
const ID_RE = /^[a-z0-9._+-]{1,64}@[a-z0-9.-]{1,253}\/(\d{13})-[0-9a-f]{8}\.eml$/;

/** Длина from/subject в customMetadata: тема по RFC может быть почти любой. */
const META_MAX_CHARS = 200;

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

/** Сравнение без раннего выхода: время ответа не выдаёт, сколько символов совпало. */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

/**
 * Незаданный секрет закрывает всё. Без этой проверки он превратился бы в
 * строку «Bearer undefined», которую можно просто прислать.
 */
function authorized(request, token) {
  if (!token) return false;
  return timingSafeEqual(request.headers.get("authorization") || "", `Bearer ${token}`);
}

export function buildKey(address, now = Date.now()) {
  const rev = String(REV_BASE - now).padStart(13, "0");
  const rand = Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
  return `${address}/${rev}-${rand}.eml`;
}

/** Время получения письма (мс) из его ключа. */
export function receivedAt(key) {
  const m = ID_RE.exec(key);
  return m ? REV_BASE - Number(m[1]) : null;
}

async function handleIngest(request, env) {
  if (!authorized(request, env.RELAY_TOKEN)) return json({ error: "Доступ запрещён" }, 401);

  let meta;
  try {
    meta = JSON.parse(decodeURIComponent(request.headers.get("x-mail-meta") || ""));
  } catch {
    return json({ error: "некорректный X-Mail-Meta" }, 400);
  }
  const to = String(meta?.to || "").trim().toLowerCase();
  if (!ADDRESS_RE.test(to)) return json({ error: "некорректный адрес" }, 400);

  // Заявленный размер проверяется до чтения тела: 100 МБ мусора не должны
  // попасть в память Worker'а.
  if (Number(request.headers.get("content-length")) > MAX_BYTES) {
    return json({ error: "некорректный размер письма" }, 400);
  }
  const body = await request.arrayBuffer();
  if (body.byteLength === 0 || body.byteLength > MAX_BYTES) {
    return json({ error: "некорректный размер письма" }, 400);
  }

  await env.MAIL.put(buildKey(to), body, {
    customMetadata: {
      from: String(meta.from || "").slice(0, META_MAX_CHARS),
      subject: String(meta.subject || "").slice(0, META_MAX_CHARS),
    },
  });
  console.log(JSON.stringify({ event: "stored", to, size: body.byteLength }));
  return json({ ok: true });
}

async function handleList(url, request, env) {
  if (!authorized(request, env.READ_TOKEN)) return json({ error: "Доступ запрещён" }, 401);
  const address = (url.searchParams.get("address") || "").trim().toLowerCase();
  if (!ADDRESS_RE.test(address)) return json({ error: "некорректный адрес" }, 400);

  const listed = await env.MAIL.list({
    prefix: `${address}/`,
    limit: LIST_LIMIT,
    include: ["customMetadata"],
  });
  return json(
    listed.objects.map((o) => ({
      id: o.key,
      from: o.customMetadata?.from || "",
      subject: o.customMetadata?.subject || "",
      receivedAt: receivedAt(o.key),
      size: o.size,
    })),
  );
}

async function handleGet(url, request, env) {
  if (!authorized(request, env.READ_TOKEN)) return json({ error: "Доступ запрещён" }, 401);
  const id = url.searchParams.get("id") || "";
  if (!ID_RE.test(id)) return json({ error: "некорректный id" }, 400);

  const object = await env.MAIL.get(id);
  if (!object) return json({ error: "письмо не найдено" }, 404);
  return new Response(object.body, { headers: { "content-type": "message/rfc822" } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "POST" && url.pathname === "/ingest") return await handleIngest(request, env);
      if (request.method === "GET" && url.pathname === "/messages") return await handleList(url, request, env);
      if (request.method === "GET" && url.pathname === "/message") return await handleGet(url, request, env);
      return json({ error: "не найдено" }, 404);
    } catch (e) {
      // Наружу — общий текст, подробности только в журнал Worker'а.
      console.error(JSON.stringify({ event: "unhandled", name: e?.name, message: e?.message }));
      return json({ error: "Внутренняя ошибка" }, 500);
    }
  },
};
```

- [ ] **Шаг 5: тесты проходят**

Run: `cd mail-inbox/collector && npm test`
Expected: PASS, 6 тестов.

- [ ] **Шаг 6: коммит**

```bash
git add mail-inbox/collector/
git commit -m "feat(mail-inbox): add collector worker with R2 storage"
```

---

### Задача 3: Скрипт настройки `setup_routing.py`

**Файлы:**
- Создать: `mail-inbox/setup/setup_routing.py`
- Создать: `mail-inbox/setup/test_setup_routing.py`

**Интерфейсы:**
- Потребляет: `mail-inbox/relay/relay.mjs` (задача 1) — читается как байты и заливается; адрес `MAIL_INBOX_URL` приёмника (задача 2) + `/ingest`.
- Производит: CLI `setup_routing.py <csv> [--dry-run]`, отчёт рядом с входным файлом (`domains.csv` → `domains.report.csv`); функции `read_rows(path) -> (List[Row], List[(int, str)])`, `plan_domain(domain, mx, txt, routing_enabled, rules) -> Plan`, `build_relay_upload(ingest_url, relay_token, code) -> (bytes, str)`.

- [ ] **Шаг 1: тест (падает)**

`mail-inbox/setup/test_setup_routing.py`:
```python
"""
Тесты скрипта настройки почты.

Покрыто то, что решает, трогать ли чужой домен: plan_domain (ошибка здесь
ломает кому-то работающую почту) и разбор CSV (ошибка — тихо пропущенные или
перепутанные домены). Плюс тело заливки пересыльщика: опечатка в имени биндинга
всплыла бы только на первом письме.

Запуск: .venv/bin/python -m unittest discover -s mail-inbox/setup -v
"""
import email
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import setup_routing as sr

DOMAIN = "site1.com"
CF_MX = [{"name": DOMAIN, "content": "route1.mx.cloudflare.net"}]
OUR_RULE = {
    "id": "r1",
    "enabled": True,
    "matchers": [{"type": "literal", "field": "to", "value": "info@site1.com"}],
    "actions": [{"type": "worker", "value": ["mail-inbox-relay"]}],
}


class PlanDomainTest(unittest.TestCase):
    def test_fresh_domain_gets_everything(self):
        plan = sr.plan_domain(DOMAIN, [], [], False, [])
        self.assertIsNone(plan.skip)
        self.assertTrue(plan.enable_routing)
        self.assertTrue(plan.create_rule)
        self.assertIsNone(plan.enable_rule)

    def test_foreign_mx_is_skipped(self):
        mx = [{"name": DOMAIN, "content": "aspmx.l.google.com"}]
        self.assertIn("aspmx.l.google.com", sr.plan_domain(DOMAIN, mx, [], False, []).skip)

    def test_mx_on_subdomain_is_not_a_conflict(self):
        mx = [{"name": "send.site1.com", "content": "feedback-smtp.amazonses.com"}]
        self.assertIsNone(sr.plan_domain(DOMAIN, mx, [], False, []).skip)

    def test_own_spf_blocks_enabling(self):
        txt = [{"name": DOMAIN, "content": '"v=spf1 include:_spf.google.com ~all"'}]
        self.assertIn("SPF", sr.plan_domain(DOMAIN, [], txt, False, []).skip)

    def test_own_spf_ignored_when_routing_already_enabled(self):
        txt = [{"name": DOMAIN, "content": "v=spf1 include:_spf.google.com ~all"}]
        self.assertIsNone(sr.plan_domain(DOMAIN, CF_MX, txt, True, []).skip)

    def test_cloudflare_spf_is_not_a_conflict(self):
        txt = [{"name": DOMAIN, "content": "v=spf1 include:_spf.mx.cloudflare.net ~all"}]
        self.assertIsNone(sr.plan_domain(DOMAIN, CF_MX, txt, False, []).skip)

    def test_info_forwarded_elsewhere_is_skipped(self):
        rule = dict(OUR_RULE, actions=[{"type": "forward", "value": ["boss@gmail.com"]}])
        self.assertIn("boss@gmail.com", sr.plan_domain(DOMAIN, CF_MX, [], True, [rule]).skip)

    def test_rule_match_ignores_case(self):
        rule = dict(OUR_RULE, matchers=[{"type": "literal", "field": "to", "value": "INFO@Site1.com"}],
                    actions=[{"type": "drop"}])
        self.assertIsNotNone(sr.plan_domain(DOMAIN, CF_MX, [], True, [rule]).skip)

    def test_configured_domain_needs_nothing(self):
        plan = sr.plan_domain(DOMAIN, CF_MX, [], True, [OUR_RULE])
        self.assertIsNone(plan.skip)
        self.assertTrue(plan.nothing_to_do)

    def test_our_disabled_rule_gets_enabled(self):
        rule = dict(OUR_RULE, enabled=False)
        plan = sr.plan_domain(DOMAIN, CF_MX, [], True, [rule])
        self.assertIs(plan.enable_rule, rule)
        self.assertFalse(plan.create_rule)


class ReadRowsTest(unittest.TestCase):
    def _read(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "in.csv"
            path.write_text(text, encoding="utf-8")
            return sr.read_rows(path)

    def test_comma_with_header(self):
        rows, skipped = self._read("account,x,domain\na@b.com:tok123,foo,Site1.com\n")
        self.assertEqual(skipped, [(1, "в колонке 1 нет «email:token»")])
        self.assertEqual(rows, [sr.Row(2, "a@b.com", "tok123", "site1.com")])

    def test_semicolon_and_bom(self):
        rows, _ = self._read("﻿a@b.com:tok;x;site2.com\n")
        self.assertEqual(rows, [sr.Row(1, "a@b.com", "tok", "site2.com")])

    def test_missing_domain_is_reported(self):
        rows, skipped = self._read("a@b.com:tok,x\n")
        self.assertEqual(rows, [])
        self.assertEqual(skipped, [(1, "нет домена в колонке 3")])


class RelayUploadTest(unittest.TestCase):
    def test_metadata_and_module(self):
        body, content_type = sr.build_relay_upload(
            "https://mail-inbox.x.workers.dev/ingest", "secret", b"export default {}")
        msg = email.message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
        parts = {p.get_param("name", header="content-disposition"): p for p in msg.get_payload()}

        meta = json.loads(parts["metadata"].get_payload(decode=True))
        self.assertEqual(meta["main_module"], "relay.mjs")
        self.assertIn("global_fetch_strictly_public", meta["compatibility_flags"])
        self.assertEqual({b["name"]: (b["type"], b["text"]) for b in meta["bindings"]}, {
            "INGEST_URL": ("plain_text", "https://mail-inbox.x.workers.dev/ingest"),
            "RELAY_TOKEN": ("secret_text", "secret"),
        })
        self.assertEqual(parts["relay.mjs"].get_content_type(), "application/javascript+module")
        self.assertEqual(parts["relay.mjs"].get_payload(decode=True), b"export default {}")

    def test_relay_file_is_where_script_expects(self):
        self.assertTrue(sr.RELAY_FILE.is_file())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Шаг 2: убедиться, что падает**

Run: `.venv/bin/python -m unittest discover -s mail-inbox/setup -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'setup_routing'`.

- [ ] **Шаг 3: реализация**

`mail-inbox/setup/setup_routing.py`:
```python
"""
Настройка почты info@<domain> по CSV.

Для каждой строки: включает Email Routing на домене, заливает в аккаунт
пересыльщика mail-inbox-relay и создаёт правило info@<domain> → пересыльщик.
Домены с чужой почтой, занятым info@ или своим SPF не трогает — только пишет
причину в отчёт. Устройство: docs/mail-inbox-design.md, раздел 5.

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
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
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
    try:
        # Excel с русской локалью сохраняет CSV через «;».
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows, skipped = [], []
    for line, cells in enumerate(csv.reader(text.splitlines(), dialect), start=1):
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
    create_rule: bool = False
    enable_rule: Optional[dict] = None  # наше правило, но выключенное

    @property
    def nothing_to_do(self) -> bool:
        return not (self.enable_routing or self.create_rule or self.enable_rule)

    def describe(self) -> str:
        steps = ["включить Email Routing"] if self.enable_routing else []
        steps.append("залить пересыльщика")
        if self.create_rule:
            steps.append("создать правило info@")
        if self.enable_rule:
            steps.append("включить выключенное правило info@")
        return ", ".join(steps)


def _txt_value(record: dict) -> str:
    return (record.get("content") or "").strip().strip('"')


def _matches(rule: dict, address: str) -> bool:
    return any(
        m.get("type") == "literal" and m.get("field") == "to" and (m.get("value") or "").lower() == address
        for m in rule.get("matchers") or []
    )


def _is_ours(rule: dict) -> bool:
    actions = rule.get("actions") or []
    return len(actions) == 1 and actions[0].get("type") == "worker" and actions[0].get("value") == [RELAY_NAME]


def _describe_rule(rule: dict) -> str:
    return ", ".join(
        f"{a.get('type')} → {', '.join(a.get('value') or [])}" for a in rule.get("actions") or []
    ) or "без действий"


def plan_domain(domain: str, mx_records: list, txt_records: list, routing_enabled: bool, rules: list) -> Plan:
    """Что делать с доменом. Чистая функция: вся логика «трогать или нет» здесь."""
    address = f"info@{domain}"

    foreign_mx = sorted({
        (r.get("content") or "").rstrip(".")
        for r in mx_records
        if r.get("name") == domain and not (r.get("content") or "").rstrip(".").lower().endswith(CF_MX_SUFFIX)
    })
    if foreign_mx:
        return Plan(skip="чужая почта (MX): " + ", ".join(foreign_mx))

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

    rule = next((r for r in rules if _matches(r, address)), None)
    if rule is not None and not _is_ours(rule):
        return Plan(skip="info@ уже настроен: " + _describe_rule(rule))

    return Plan(
        enable_routing=not routing_enabled,
        create_rule=rule is None,
        enable_rule=rule if rule is not None and not rule.get("enabled", True) else None,
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
        routing_enabled = bool((api.call("GET", f"/zones/{zone_id}/email/routing") or {}).get("enabled"))
    except CFError as e:
        if e.status != 404:
            raise
        routing_enabled = False
    rules = api.list_all(f"/zones/{zone_id}/email/routing/rules", RULES_PER_PAGE)

    plan = plan_domain(row.domain, mx, txt, routing_enabled, rules)
    if plan.skip:
        return SKIPPED, plan.skip
    if dry_run:
        return (ALREADY if plan.nothing_to_do else PLANNED), plan.describe()

    if plan.enable_routing:
        api.call("POST", f"/zones/{zone_id}/email/routing/dns", {"name": row.domain})
    # Раз на аккаунт за запуск. Перезаливка безопасна: так же обновляется код
    # пересыльщика во всех аккаунтах.
    if account_id not in uploaded:
        body, content_type = build_relay_upload(config["ingest_url"], config["relay_token"], config["relay_code"])
        api.call("PUT", f"/accounts/{account_id}/workers/scripts/{RELAY_NAME}", raw=body, content_type=content_type)
        uploaded.add(account_id)
    if plan.create_rule:
        api.call("POST", f"/zones/{zone_id}/email/routing/rules", {
            "name": f"info@ → {RELAY_NAME}",
            "enabled": True,
            "matchers": [{"type": "literal", "field": "to", "value": f"info@{row.domain}"}],
            "actions": [{"type": "worker", "value": [RELAY_NAME]}],
        })
    if plan.enable_rule:
        rule = plan.enable_rule
        api.call("PUT", f"/zones/{zone_id}/email/routing/rules/{rule['id']}", {
            "name": rule.get("name") or f"info@ → {RELAY_NAME}",
            "enabled": True,
            "matchers": rule["matchers"],
            "actions": rule["actions"],
            "priority": rule.get("priority", 0),
        })
    return (ALREADY if plan.nothing_to_do else OK), plan.describe()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Настройка почты info@<domain> по CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="только проверить и показать план, ничего не менять")
    args = parser.parse_args(argv)

    config = None
    if not args.dry_run:
        load_dotenv(ENV_FILE)
        base_url = (os.getenv("MAIL_INBOX_URL") or "").rstrip("/")
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
    with report_path.open("w", newline="", encoding="utf-8") as f:
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
```

- [ ] **Шаг 4: тесты проходят**

Run: `.venv/bin/python -m unittest discover -s mail-inbox/setup -v`
Expected: PASS, 15 тестов.

- [ ] **Шаг 5: коммит**

```bash
git add mail-inbox/setup/
git commit -m "feat(mail-inbox): add CSV setup script for email routing"
```

---

### Задача 4: Сервис бота `services/mail_inbox.py`

**Файлы:**
- Изменить: `config.py` (после блока `CF_WORKER_URL` / `KV_SYNC_TOKEN`)
- Создать: `services/mail_inbox.py`
- Создать: `tests/test_mail_inbox.py`

**Интерфейсы:**
- Потребляет: API приёмника из задачи 2; `KYIV_TZ` из `services/ecards.py`.
- Производит (для задачи 5): `MailInboxError`, `is_configured() -> bool`, `normalize_address(text) -> str` (ValueError при мусоре), `button_label(item: dict, now: datetime) -> str`, `render_message(raw: bytes, received_at_ms: Optional[int]) -> str` (HTML для Telegram), `async list_messages(address) -> List[dict]`, `async fetch_message(message_id) -> bytes`.

- [ ] **Шаг 1: переменные в config.py**

Добавить после строки `KV_SYNC_TOKEN = os.getenv("KV_SYNC_TOKEN")`:
```python

# Почта доменов (info@<domain>): адрес Worker'а mail-inbox и токен ЧТЕНИЯ.
# Токена записи (RELAY_TOKEN) здесь нет намеренно: он нужен только
# пересыльщикам и скрипту настройки (mail-inbox/.env), боту — нет.
MAIL_INBOX_URL = os.getenv("MAIL_INBOX_URL")
MAIL_READ_TOKEN = os.getenv("MAIL_READ_TOKEN")
```

- [ ] **Шаг 2: тест (падает)**

`tests/test_mail_inbox.py`:
```python
"""
Тесты раздела «📧 Почта доменов» — то, что проверяется без сети.

  - normalize_address: ошибка здесь покажет пустой ящик вместо писем, и
    человек решит, что код не пришёл.
  - render_message: письма бывают в cp1251, в quoted-printable, только в HTML;
    ссылка «Подтвердить» обязана дожить до Telegram, чужой текст экранируется,
    сообщение не превышает лимит Telegram.
  - button_label: тема в списке приходит как encoded-word и должна читаться.

Запуск: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import datetime
from email.header import Header
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import mail_inbox
from services.ecards import KYIV_TZ


def build_mail(body, subtype="plain", charset="utf-8", cte=None, subject="Код подтверждения"):
    msg = EmailMessage()
    msg["From"] = "Google <no-reply@accounts.google.com>"
    msg["To"] = "info@site1.com"
    msg["Subject"] = subject
    msg.set_content(body, subtype=subtype, charset=charset, cte=cte)
    return msg


def kyiv_ms(day, hour=12, minute=41):
    return int(datetime(2026, 9, day, hour, minute, tzinfo=KYIV_TZ).timestamp() * 1000)


class NormalizeAddressTest(unittest.TestCase):
    def test_variants_become_info_address(self):
        for text in ("site1.com", " Site1.COM ", "info@site1.com", "https://www.site1.com/page?x=1", "site1.com."):
            with self.subTest(text=text):
                self.assertEqual(mail_inbox.normalize_address(text), "info@site1.com")

    def test_other_local_part_kept(self):
        self.assertEqual(mail_inbox.normalize_address("Admin@site1.com"), "admin@site1.com")

    def test_cyrillic_domain_becomes_punycode(self):
        self.assertEqual(mail_inbox.normalize_address("сайт.укр"), "info@xn--80aswg.xn--j1amh")

    def test_garbage_rejected(self):
        for text in ("", "hello", "site1", "a@b@c", "site one.com", "@site1.com"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    mail_inbox.normalize_address(text)


class ButtonLabelTest(unittest.TestCase):
    NOW = datetime(2026, 9, 28, 15, 0, tzinfo=KYIV_TZ)
    ENCODED_SUBJECT = Header("Код подтверждения", "utf-8").encode()

    def _item(self, day, subject=None):
        return {"from": "Google <no-reply@accounts.google.com>",
                "subject": subject or self.ENCODED_SUBJECT, "receivedAt": kyiv_ms(day)}

    def test_today_shows_time_sender_and_decoded_subject(self):
        self.assertEqual(mail_inbox.button_label(self._item(28), self.NOW), "12:41 · Google · Код подтверждения")

    def test_older_shows_date(self):
        self.assertTrue(mail_inbox.button_label(self._item(26), self.NOW).startswith("26.09 12:41 · "))

    def test_long_label_cut(self):
        label = mail_inbox.button_label(self._item(28, subject="x" * 200), self.NOW)
        self.assertEqual(len(label), 60)
        self.assertTrue(label.endswith("…"))


class RenderMessageTest(unittest.TestCase):
    def test_plain_cp1251_quoted_printable(self):
        raw = build_mail("Ваш код: 123456", charset="cp1251", cte="quoted-printable").as_bytes()
        text = mail_inbox.render_message(raw, kyiv_ms(28))
        self.assertIn("Ваш код: 123456", text)
        self.assertIn("<b>Код подтверждения</b>", text)
        self.assertIn("28.09.2026 12:41", text)

    def test_html_keeps_link_address(self):
        raw = build_mail('<p>Нажмите <a href="https://example.com/verify?t=1&amp;u=2">Подтвердить</a></p>'
                         '<style>p{color:red}</style>', subtype="html").as_bytes()
        text = mail_inbox.render_message(raw, None)
        self.assertIn("Подтвердить (https://example.com/verify?t=1&amp;u=2)", text)
        self.assertNotIn("color:red", text)

    def test_foreign_markup_is_escaped(self):
        raw = build_mail("<script>alert(1)</script> & <b>жирный</b>").as_bytes()
        text = mail_inbox.render_message(raw, None)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>", text)

    def test_long_mail_fits_telegram_limit(self):
        raw = build_mail("а" * 10_000, subject="т" * 1000).as_bytes()
        text = mail_inbox.render_message(raw, kyiv_ms(28))
        self.assertIn("…обрезано", text)
        self.assertLess(len(text), 4096)

    def test_attachments_listed_by_name(self):
        msg = build_mail("см. вложение")
        msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="invoice.pdf")
        text = mail_inbox.render_message(msg.as_bytes(), None)
        self.assertIn("см. вложение", text)
        self.assertIn("invoice.pdf", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Шаг 3: убедиться, что падает**

Run: `.venv/bin/python -m unittest tests.test_mail_inbox -v`
Expected: FAIL — `ImportError: cannot import name 'mail_inbox' from 'services'`.

- [ ] **Шаг 4: реализация**

`services/mail_inbox.py`:
```python
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
```

- [ ] **Шаг 5: тесты проходят**

Run: `.venv/bin/python -m unittest tests.test_mail_inbox -v`
Expected: PASS, 12 тестов.

- [ ] **Шаг 6: коммит**

```bash
git add config.py services/mail_inbox.py tests/test_mail_inbox.py
git commit -m "feat(mail-inbox): add bot-side mail client and renderer"
```

---

### Задача 5: Раздел «📧 Почта доменов» в боте

**Файлы:**
- Изменить: `keyboards.py` (константа + кнопка в оба меню)
- Изменить: `states.py` (состояние)
- Изменить: `middlewares/throttle.py` (бакет `mail`)
- Создать: `handlers/mail_inbox.py`
- Изменить: `main.py` (импорт и роутер)

**Интерфейсы:**
- Потребляет: всё из задачи 4 (`services.mail_inbox`).
- Производит: `keyboards.MAIL_INBOX_TEXT = "📧 Почта доменов"`, `Form.mail_waiting_for_domain`, callback_data `mail:open:<n>` и `mail:refresh`, данные FSM `mail_address`, `mail_items`, `mail_list_message_id`.

- [ ] **Шаг 1: keyboards.py**

После `VIDEO_CLOUD_TEXT = "🎬 Видео для Cloud"` добавить:
```python
MAIL_INBOX_TEXT = "📧 Почта доменов"
```
В `menu_kb_user` и `menu_kb_with_buyer_expenses` сразу после строки `[KeyboardButton(text=VIDEO_CLOUD_TEXT)],` добавить:
```python
    [KeyboardButton(text=MAIL_INBOX_TEXT)],
```

- [ ] **Шаг 2: states.py**

В конец класса `Form` добавить:
```python

    # Почта доменов (info@<domain>): ожидание домена. Остаётся и после показа
    # списка — можно сразу ввести следующий домен.
    mail_waiting_for_domain = State()
```

- [ ] **Шаг 3: middlewares/throttle.py**

Импорт: `from keyboards import VIDEO_CLOUD_TEXT` → `from keyboards import MAIL_INBOX_TEXT, VIDEO_CLOUD_TEXT`.

В `LIMITS` добавить:
```python
    "mail": (10, 60),            # почта доменов — каждый запрос идёт в mail-inbox
```
В `_TEXT_BUCKETS` добавить:
```python
    MAIL_INBOX_TEXT: "mail",
```
В `_CALLBACK_BUCKETS` добавить:
```python
    "mail:": "mail",
```
В `_bucket_for`, сразу после проверки `card_actions_enter_number`, добавить:
```python
        # Ввод домена — тоже запрос в mail-inbox, а по тексту он неотличим от
        # любого другого сообщения.
        if state and "mail_waiting_for_domain" in str(state):
            return "mail"
```

- [ ] **Шаг 4: handlers/mail_inbox.py**

```python
"""
Почта доменов: письма на info@<domain> в боте.

Флоу: кнопка → домен → список последних писем (инлайн-кнопки) → письмо.
Письма хранит отдельный Worker mail-inbox; бот только читает их через
services.mail_inbox. Устройство и причины: docs/mail-inbox-design.md.

Кнопки списка работают по данным FSM (адрес, письма, id сообщения со
списком): в callback_data помещается только 64 байта, id письма с длинным
доменом туда не влезает. Кнопка из старого списка отвечает «устарел», а не
показывает письма другого домена.
"""
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from keyboards import MAIL_INBOX_TEXT, cancel_kb, get_menu_keyboard
from services import mail_inbox
from services.ecards import KYIV_TZ
from states import Form

logger = logging.getLogger(__name__)

router = Router()

UNAVAILABLE_TEXT = "❌ Почта сейчас недоступна, попробуйте через минуту."
STALE_TEXT = "Список устарел — введите домен ещё раз."

if not mail_inbox.is_configured():
    # Не ошибка старта: без почты остальной бот работает, а кнопка честно
    # ответит «не настроено» (см. start_mail_inbox).
    logger.warning("[mail-inbox] MAIL_INBOX_URL / MAIL_READ_TOKEN не заданы — раздел почты выключен")


def _list_view(address: str, items: list):
    """Текст и клавиатура списка писем. address уже проверен регуляркой — экранировать нечего."""
    now = datetime.now(KYIV_TZ)
    rows = [
        [InlineKeyboardButton(text=mail_inbox.button_label(item, now), callback_data=f"mail:open:{i}")]
        for i, item in enumerate(items)
    ]
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="mail:refresh")])
    text = f"📬 <b>{address}</b>\n" + ("Последние письма:" if items else "Писем пока нет.")
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _current_list(query: CallbackQuery, state: FSMContext) -> dict | None:
    """Данные списка, если кнопка нажата в последнем показанном списке, иначе None."""
    data = await state.get_data()
    if not data.get("mail_address") or data.get("mail_list_message_id") != query.message.message_id:
        return None
    return data


@router.message(F.text == MAIL_INBOX_TEXT)
async def start_mail_inbox(message: Message, state: FSMContext):
    if not mail_inbox.is_configured():
        await message.answer(
            "❌ Почта доменов не настроена. Сообщите администратору.",
            reply_markup=get_menu_keyboard(message.from_user.id),
        )
        return
    await state.set_state(Form.mail_waiting_for_domain)
    await message.answer(
        "📧 Введите домен, например <b>site1.com</b> — покажу письма на info@ этого домена.",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )


@router.message(Form.mail_waiting_for_domain)
async def show_inbox(message: Message, state: FSMContext):
    try:
        address = mail_inbox.normalize_address(message.text or "")
    except ValueError:
        await message.answer("❌ Не похоже на домен. Пример: site1.com", reply_markup=cancel_kb)
        return

    try:
        items = await mail_inbox.list_messages(address)
    except mail_inbox.MailInboxError:
        logger.exception("[mail-inbox] список писем не получен: %s", address)
        await message.answer(UNAVAILABLE_TEXT, reply_markup=cancel_kb)
        return

    text, kb = _list_view(address, items)
    sent = await message.answer(text, parse_mode="HTML", reply_markup=kb)
    await state.update_data(mail_address=address, mail_items=items, mail_list_message_id=sent.message_id)


@router.callback_query(F.data == "mail:refresh")
async def refresh_inbox(query: CallbackQuery, state: FSMContext):
    data = await _current_list(query, state)
    if data is None:
        await query.answer(STALE_TEXT, show_alert=True)
        return
    address = data["mail_address"]
    try:
        items = await mail_inbox.list_messages(address)
    except mail_inbox.MailInboxError:
        logger.exception("[mail-inbox] обновление списка не удалось: %s", address)
        await query.answer(UNAVAILABLE_TEXT, show_alert=True)
        return

    await state.update_data(mail_items=items)
    text, kb = _list_view(address, items)
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        # Список не изменился — Telegram отказывается «редактировать» в то же самое.
        if "message is not modified" not in str(e):
            raise
        await query.answer("Новых писем нет")
        return
    await query.answer("Обновлено")


@router.callback_query(F.data.startswith("mail:open:"))
async def open_mail(query: CallbackQuery, state: FSMContext):
    data = await _current_list(query, state)
    try:
        item = data["mail_items"][int(query.data.rsplit(":", 1)[1])] if data else None
    except (ValueError, IndexError):
        item = None
    if item is None:
        await query.answer(STALE_TEXT, show_alert=True)
        return

    # Ответ на нажатие — сразу: у Telegram на него 15 секунд, а письмо ещё
    # нужно скачать.
    await query.answer()
    try:
        raw = await mail_inbox.fetch_message(item["id"])
    except mail_inbox.MailInboxError:
        logger.exception("[mail-inbox] письмо не получено: %s", item["id"])
        await query.message.answer(UNAVAILABLE_TEXT)
        return

    await query.message.answer(
        mail_inbox.render_message(raw, item.get("receivedAt")),
        parse_mode="HTML",
        # Для превью Telegram сам открывает ссылку — одноразовая ссылка
        # «войти / подтвердить» сработала бы раньше человека.
        disable_web_page_preview=True,
    )
```

- [ ] **Шаг 5: main.py**

В импорт `from handlers import (...)` добавить `mail_inbox` после `video_cloud`:
```python
    video_cloud,
    mail_inbox
)
```
(у `video_cloud` появляется запятая). В `create_dispatcher()` после `dp.include_router(video_cloud.router)`:
```python
    dp.include_router(mail_inbox.router)
```
Роутер — последним: общий обработчик «❌ Отмена» из `common` и кнопки других разделов срабатывают раньше состояния ожидания домена.

- [ ] **Шаг 6: проверка сборки и всех тестов**

Run: `.venv/bin/python -c "import main; main.create_dispatcher(); print('dispatcher ok')"`
Expected: `dispatcher ok` (и предупреждение `[mail-inbox] … не заданы`, пока нет `.env`-переменных).

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS — все старые тесты + 12 новых.

- [ ] **Шаг 7: коммит**

```bash
git add keyboards.py states.py middlewares/throttle.py handlers/mail_inbox.py main.py
git commit -m "feat(mail-inbox): add domain mail section to the bot"
```

---

### Задача 6: README подсистемы

**Файлы:**
- Создать: `mail-inbox/README.md`

**Интерфейсы:** документ; команды и имена — из глобальных ограничений.

- [ ] **Шаг 1: README**

`mail-inbox/README.md`:
````markdown
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

## Тесты

```bash
node --test "mail-inbox/relay/*.test.mjs"
(cd mail-inbox/collector && npm test)
.venv/bin/python -m unittest discover -s mail-inbox/setup -v
```
````

- [ ] **Шаг 2: коммит**

```bash
git add mail-inbox/README.md
git commit -m "docs(mail-inbox): add setup and deploy guide"
```

---

### Задача 7: 👤 Запуск (делает пользователь, по шагам)

Код к этому моменту готов и протестирован. Дальше — действия в Cloudflare и на сервере бота.

- [ ] **Шаг 1: токены** — `openssl rand -hex 32` дважды (RELAY и READ).
- [ ] **Шаг 2: приёмник** — команды из README, раздел «Развёртывание приёмника». Если wrangler попросит войти — `npx wrangler login`.
- [ ] **Шаг 3: проверка приёмника**
  - `curl -i "https://mail-inbox.<…>.workers.dev/messages?address=info@example.com"` → `401`;
  - то же с `-H "Authorization: Bearer <READ_TOKEN>"` → `200 []`.
- [ ] **Шаг 4: `.env`-файлы** — `mail-inbox/.env` и `.env` бота (таблица «Секреты»), перезапуск бота. В меню появилась кнопка «📧 Почта доменов».
- [ ] **Шаг 5: тестовый домен** — CSV из одной строки → `--dry-run` → запуск → письмо на `info@<домен>` из Gmail → видно в боте. Ещё одно письмо: кириллическая тема + HTML-кнопка.
- [ ] **Шаг 6: сбой пересыльщика** — временно сменить `RELAY_TOKEN` у приёмника (`npx wrangler secret put RELAY_TOKEN`, любое значение) → отправить письмо → посмотреть Activity log в Email Routing и пришёл ли отказ в Gmail → вернуть токен. Результат — в раздел 9 спеки.
- [ ] **Шаг 7: весь CSV** — `--dry-run` → разбор отчёта вместе → настоящий запуск.
