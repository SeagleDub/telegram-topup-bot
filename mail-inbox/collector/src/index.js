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
