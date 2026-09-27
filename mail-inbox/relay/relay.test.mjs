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
