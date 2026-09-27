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
