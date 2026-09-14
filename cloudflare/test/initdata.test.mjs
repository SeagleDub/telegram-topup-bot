/**
 * Тесты проверки подписи initData.
 *
 * Зачем именно эти: несовпадение HMAC — самый вероятный источник долгой отладки
 * в этой фиче, и проявляется он одинаково при любой причине («подпись не
 * сошлась»), из-за чего разбирается тяжело. Проверяем не только счастливый
 * путь, но и каждый способ подделки в отдельности: тест, который умеет только
 * подтвердить успех, про безопасность не говорит ничего.
 *
 * Запуск: npm test
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { verifyInitData, hmacSha256, toHex, Unauthorized } from "../src/index.js";

const BOT_TOKEN = "123456:TEST-TOKEN-NOT-REAL";

/** Тот самый производный ключ, который лежит в секретах Worker'а. */
async function deriveSecretKeyHex(token) {
  return toHex(await hmacSha256(new TextEncoder().encode("WebAppData"), token));
}

/**
 * Собирает initData ровно так, как это делает Telegram: значения подписываются
 * в декодированном виде, а в строку попадают percent-encoded. Расхождение между
 * этими двумя формами и есть классическая причина несходящейся подписи.
 */
async function buildInitData(fields, token = BOT_TOKEN) {
  const dataCheckString = Object.keys(fields)
    .sort()
    .map((k) => `${k}=${fields[k]}`)
    .join("\n");

  const secretKey = await hmacSha256(new TextEncoder().encode("WebAppData"), token);
  const hash = toHex(await hmacSha256(secretKey, dataCheckString));

  const params = new URLSearchParams(fields);
  params.set("hash", hash);
  return params.toString();
}

function freshFields(overrides = {}) {
  return {
    query_id: "AAHdF6IQAAAAAN0XohDhrOrc",
    user: JSON.stringify({ id: 777001, first_name: "Тест", username: "tester" }),
    auth_date: String(Math.floor(Date.now() / 1000)),
    ...overrides,
  };
}

test("корректная initData принимается и отдаёт пользователя", async () => {
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  const initData = await buildInitData(freshFields());

  const user = await verifyInitData(initData, secret);
  assert.equal(user.id, 777001);
  assert.equal(user.username, "tester");
});

test("значения со спецсимволами не ломают подпись", async () => {
  // Имя с & = % + кириллицей: если где-то закрадётся лишнее декодирование или
  // кодирование, подпись развалится именно здесь, а не на простых данных.
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  const initData = await buildInitData(
    freshFields({
      user: JSON.stringify({
        id: 777002,
        first_name: "A&B=C%20D",
        last_name: "Пётр+Иванов",
      }),
    }),
  );

  const user = await verifyInitData(initData, secret);
  assert.equal(user.id, 777002);
  assert.equal(user.first_name, "A&B=C%20D");
});

test("подменённый user отвергается", async () => {
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  const initData = await buildInitData(freshFields());

  // Подпись оставляем прежней, пользователя подменяем на чужого.
  const params = new URLSearchParams(initData);
  params.set("user", JSON.stringify({ id: 999999, first_name: "Чужой" }));

  await assert.rejects(
    () => verifyInitData(params.toString(), secret),
    Unauthorized,
  );
});

test("подпись, сделанная другим токеном, отвергается", async () => {
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  const initData = await buildInitData(freshFields(), "999999:ANOTHER-BOT-TOKEN");

  await assert.rejects(() => verifyInitData(initData, secret), Unauthorized);
});

test("initData без hash отвергается", async () => {
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  const params = new URLSearchParams(freshFields());

  await assert.rejects(() => verifyInitData(params.toString(), secret), Unauthorized);
});

test("initData старше часа отвергается", async () => {
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  const twoHoursAgo = String(Math.floor(Date.now() / 1000) - 7200);
  const initData = await buildInitData(freshFields({ auth_date: twoHoursAgo }));

  // Подпись корректна — отказ должен наступить именно из-за возраста.
  await assert.rejects(
    () => verifyInitData(initData, secret),
    (e) => e instanceof Unauthorized && /устарела/.test(e.message),
  );
});

test("auth_date из будущего отвергается", async () => {
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  const future = String(Math.floor(Date.now() / 1000) + 3600);
  const initData = await buildInitData(freshFields({ auth_date: future }));

  await assert.rejects(
    () => verifyInitData(initData, secret),
    (e) => e instanceof Unauthorized && /будущего/.test(e.message),
  );
});

test("пустая и не-строковая initData отвергаются", async () => {
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  await assert.rejects(() => verifyInitData("", secret), Unauthorized);
  await assert.rejects(() => verifyInitData(null, secret), Unauthorized);
  await assert.rejects(() => verifyInitData(undefined, secret), Unauthorized);
});

test("initData без поля user отвергается", async () => {
  const secret = await deriveSecretKeyHex(BOT_TOKEN);
  const fields = freshFields();
  delete fields.user;
  const initData = await buildInitData(fields);

  await assert.rejects(() => verifyInitData(initData, secret), Unauthorized);
});
