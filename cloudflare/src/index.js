/**
 * Worker для Mini App «Видео для Cloud».
 *
 * Делает ровно три вещи:
 *   1. Проверяет, что запрос пришёл от реального пользователя Telegram из вайтлиста.
 *   2. Выдаёт presigned-ссылку на загрузку одного файла напрямую в R2.
 *   3. Принимает синхронизацию вайтлиста от бота.
 *
 * Почему загрузка идёт мимо Worker'а, а не через него: тело запроса к Worker
 * ограничено 100 МБ на планах Free/Pro (200 МБ на Business). Исходники — до
 * 250 МБ, то есть проксирование не проходит по лимиту в принципе. Браузер
 * кладёт файл прямо в R2 по подписанной ссылке.
 *
 * Статическая страница Mini App лежит в ./public и отдаётся этим же Worker'ом.
 * Это не только экономия деплоя: страница и API оказываются на одном источнике,
 * поэтому CORS между ними отсутствует как класс. CORS остаётся только на R2 —
 * туда браузер ходит на другой домен.
 */
import { AwsClient } from "aws4fetch";

/** Потолок загрузки. Запас над заявленными 250 МБ. */
const MAX_UPLOAD_BYTES = 300 * 1024 * 1024;

/** Минимум, ниже которого это не видео, а промах. */
const MIN_UPLOAD_BYTES = 1024;

/**
 * Максимальный возраст initData.
 *
 * Подпись Telegram бессрочна: перехваченная строка работала бы годами. Возраст
 * — единственное, что превращает её в короткоживущий токен.
 */
const INITDATA_MAX_AGE_SEC = 3600;

/** Лимит выдачи ссылок на пользователя. */
const RATE_LIMIT_MAX = 5;
const RATE_LIMIT_WINDOW_SEC = 3600;

/** Срок жизни presigned-ссылки. */
const PRESIGN_TTL_SEC = 300;

/**
 * Content-Type, который подписывается и которым обязан отдать браузер.
 *
 * Реальный контейнер здесь не важен: файл в incoming/ промежуточный, его
 * удалит бот после транскода. Настоящую проверку формата делает ffprobe на
 * хосте — доверять заявленному клиентом типу нельзя в любом случае.
 */
const UPLOAD_CONTENT_TYPE = "video/mp4";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

function json(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...JSON_HEADERS, ...extraHeaders },
  });
}

// --------------------------------------------------------------------------- //
// Криптография
// --------------------------------------------------------------------------- //

const encoder = new TextEncoder();

export async function hmacSha256(keyBytes, message) {
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return new Uint8Array(sig);
}

export function toHex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(hex) {
  if (hex.length % 2 !== 0) throw new Error("hex-строка нечётной длины");
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

/**
 * Сравнение за постоянное время.
 *
 * Обычное === выходит на первом несовпавшем символе, и по времени ответа
 * подпись подбирается побайтово. Разница длин здесь не секрет — оба значения
 * это hex-представления SHA-256.
 */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

// --------------------------------------------------------------------------- //
// Проверка initData
// --------------------------------------------------------------------------- //

/**
 * Проверяет подпись Telegram и возвращает пользователя, либо бросает Unauthorized.
 *
 * Алгоритм Telegram:
 *   secret_key = HMAC_SHA256(key="WebAppData", msg=<токен бота>)
 *   hash       = HMAC_SHA256(key=secret_key,  msg=data_check_string)
 *
 * В секретах Worker'а лежит уже готовый secret_key, а не токен бота. Проверять
 * подпись им можно, а развернуть обратно в токен — нельзя: это выход HMAC.
 * Значит компрометация Worker'а не отдаёт управление ботом.
 */
export async function verifyInitData(initData, secretKeyHex) {
  if (!initData || typeof initData !== "string") {
    throw new Unauthorized("initData отсутствует");
  }

  const params = new URLSearchParams(initData);
  const providedHash = params.get("hash");
  if (!providedHash) throw new Unauthorized("в initData нет hash");

  // hash не участвует в строке, по которой сам же и считается.
  params.delete("hash");

  // Порядок полей фиксирован: сортировка по имени ключа. URLSearchParams уже
  // выполнил percent-декодирование — повторно декодировать нельзя, иначе
  // значения с %-последовательностями внутри исказятся.
  const dataCheckString = [...params.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");

  const computed = toHex(await hmacSha256(hexToBytes(secretKeyHex), dataCheckString));
  if (!timingSafeEqual(computed, providedHash)) {
    throw new Unauthorized("подпись initData не сошлась");
  }

  const authDate = Number(params.get("auth_date"));
  if (!Number.isFinite(authDate)) {
    throw new Unauthorized("в initData нет корректного auth_date");
  }
  const age = Math.floor(Date.now() / 1000) - authDate;
  if (age > INITDATA_MAX_AGE_SEC) {
    throw new Unauthorized(`initData устарела на ${age - INITDATA_MAX_AGE_SEC} с`);
  }
  // Отрицательный возраст — часы клиента ушли вперёд либо подделка. Небольшой
  // сдвиг допустим, заметный — нет.
  if (age < -300) {
    throw new Unauthorized("auth_date из будущего");
  }

  let user;
  try {
    user = JSON.parse(params.get("user") || "null");
  } catch {
    throw new Unauthorized("поле user в initData не разбирается");
  }
  if (!user || typeof user.id !== "number") {
    throw new Unauthorized("в initData нет идентификатора пользователя");
  }

  return user;
}

export class Unauthorized extends Error {}
export class Forbidden extends Error {}
export class TooManyRequests extends Error {}
export class BadRequest extends Error {}

// --------------------------------------------------------------------------- //
// Вайтлист и лимит частоты
// --------------------------------------------------------------------------- //

/**
 * Вайтлист синхронизирует бот из Google Sheets — источник истины там же, где и
 * для самого бота (utils.get_whitelist). Держать здесь второй список значило бы
 * иметь два расходящихся ответа на вопрос «кому можно».
 *
 * Политика fail-closed: нет списка — нет доступа. Молча пускать всех при
 * недоступном KV нельзя.
 */
async function assertWhitelisted(env, userId) {
  const raw = await env.ACL.get("whitelist");
  if (!raw) throw new Forbidden("вайтлист не синхронизирован");

  let ids;
  try {
    ids = JSON.parse(raw);
  } catch {
    throw new Forbidden("вайтлист в KV повреждён");
  }
  if (!Array.isArray(ids) || !ids.includes(userId)) {
    throw new Forbidden("пользователь не в вайтлисте");
  }
}

/**
 * Лимит выдачи ссылок.
 *
 * KV согласован в конечном счёте, поэтому счётчик приблизительный: при гонке
 * пользователь получит на одну-две ссылки больше. Это защита от
 * злоупотребления объёмом, а не граница безопасности, и такой точности
 * достаточно. Жёсткий лимит потребовал бы Durable Object — избыточно для
 * внутреннего инструмента команды.
 */
async function assertUnderRateLimit(env, userId) {
  const key = `rate:${userId}`;
  const now = Math.floor(Date.now() / 1000);
  const raw = await env.ACL.get(key);

  let state = { count: 0, windowStart: now };
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      if (now - parsed.windowStart < RATE_LIMIT_WINDOW_SEC) state = parsed;
    } catch {
      // Битое значение — начинаем окно заново, но не молчим.
      console.warn(JSON.stringify({ event: "rate_state_corrupt", user_id: userId }));
    }
  }

  if (state.count >= RATE_LIMIT_MAX) {
    const retryAfter = RATE_LIMIT_WINDOW_SEC - (now - state.windowStart);
    throw new TooManyRequests(String(Math.max(retryAfter, 1)));
  }

  state.count += 1;
  await env.ACL.put(key, JSON.stringify(state), {
    expirationTtl: RATE_LIMIT_WINDOW_SEC,
  });
}

// --------------------------------------------------------------------------- //
// Presigned-ссылка на R2
// --------------------------------------------------------------------------- //

/**
 * Подписывает PUT в R2.
 *
 * Подписываем content-length: R2 проверит подпись против реально присланных
 * заголовков, поэтому клиент не сможет заявить маленький размер, а отправить
 * большой. Без этого потолок размера был бы пожеланием — presigned URL сам по
 * себе объём не ограничивает.
 *
 * Ключ содержит user_id, который подставлен здесь, после проверки подписи.
 * Клиент на имя объекта не влияет, поэтому бот доверяет ему при выборе
 * адресата ответа. uuid в имени — чтобы ссылки на чужие креативы не
 * перебирались.
 */
async function createUploadUrl(env, userId, sizeBytes) {
  const client = new AwsClient({
    accessKeyId: env.R2_ACCESS_KEY_ID,
    secretAccessKey: env.R2_SECRET_ACCESS_KEY,
    service: "s3",
    region: "auto",
  });

  const key = `incoming/${userId}/${crypto.randomUUID()}.mp4`;
  const url = new URL(
    `https://${env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com/${env.R2_BUCKET}/${key}`,
  );
  url.searchParams.set("X-Amz-Expires", String(PRESIGN_TTL_SEC));

  const signed = await client.sign(url.toString(), {
    method: "PUT",
    headers: {
      "content-type": UPLOAD_CONTENT_TYPE,
      "content-length": String(sizeBytes),
    },
    aws: { signQuery: true },
  });

  return { uploadUrl: signed.url, key };
}

// --------------------------------------------------------------------------- //
// Маршруты
// --------------------------------------------------------------------------- //

async function handleUploadUrl(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    throw new BadRequest("тело запроса не является JSON");
  }

  const size = Number(body.size);
  if (!Number.isInteger(size) || size < MIN_UPLOAD_BYTES) {
    throw new BadRequest("некорректный размер файла");
  }
  if (size > MAX_UPLOAD_BYTES) {
    throw new BadRequest(
      `файл больше ${Math.floor(MAX_UPLOAD_BYTES / 1024 / 1024)} МБ`,
    );
  }

  const user = await verifyInitData(body.initData, env.TG_SECRET_KEY);
  await assertWhitelisted(env, user.id);
  await assertUnderRateLimit(env, user.id);

  const { uploadUrl, key } = await createUploadUrl(env, user.id, size);

  // В логи — только user_id. Username и имя из initData это персональные
  // данные, и в журнале Cloudflare им делать нечего.
  console.log(JSON.stringify({ event: "upload_url_issued", user_id: user.id, size }));

  return json({ uploadUrl, key, contentType: UPLOAD_CONTENT_TYPE });
}

/**
 * Синхронизация вайтлиста ботом.
 *
 * Отдельный общий секрет, не токен Telegram: у этого запроса нет пользователя,
 * подпись initData тут неприменима.
 */
async function handleWhitelistSync(request, env) {
  const auth = request.headers.get("authorization") || "";
  const expected = `Bearer ${env.KV_SYNC_TOKEN}`;
  if (auth.length !== expected.length || !timingSafeEqual(auth, expected)) {
    throw new Unauthorized("неверный токен синхронизации");
  }

  let body;
  try {
    body = await request.json();
  } catch {
    throw new BadRequest("тело запроса не является JSON");
  }

  const ids = body.ids;
  if (!Array.isArray(ids) || !ids.every((v) => Number.isInteger(v))) {
    throw new BadRequest("ids должен быть массивом целых чисел");
  }
  // Пустой список закрыл бы доступ всем. Это возможно только при сбое на
  // стороне бота — принимать такое молча нельзя.
  if (ids.length === 0) {
    throw new BadRequest("пустой вайтлист отклонён");
  }

  await env.ACL.put("whitelist", JSON.stringify(ids));
  console.log(JSON.stringify({ event: "whitelist_synced", count: ids.length }));
  return json({ ok: true, count: ids.length });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Всё, что не /api/*, отдаётся как статика (см. [assets] в wrangler.toml).
    if (!url.pathname.startsWith("/api/")) {
      return env.ASSETS.fetch(request);
    }

    if (request.method !== "POST") {
      return json({ error: "метод не поддерживается" }, 405);
    }

    try {
      if (url.pathname === "/api/upload-url") return await handleUploadUrl(request, env);
      if (url.pathname === "/api/whitelist") return await handleWhitelistSync(request, env);
      return json({ error: "не найдено" }, 404);
    } catch (e) {
      if (e instanceof Unauthorized) {
        console.warn(JSON.stringify({ event: "unauthorized", reason: e.message }));
        return json({ error: "Доступ запрещён" }, 401);
      }
      if (e instanceof Forbidden) {
        console.warn(JSON.stringify({ event: "forbidden", reason: e.message }));
        return json({ error: "Нет доступа к этой функции" }, 403);
      }
      if (e instanceof TooManyRequests) {
        return json({ error: "Слишком часто. Попробуйте позже." }, 429, {
          "retry-after": e.message,
        });
      }
      if (e instanceof BadRequest) {
        // Единственный класс ошибок, чей текст безопасно показать клиенту:
        // он про его же ввод и не раскрывает устройство сервиса.
        return json({ error: e.message }, 400);
      }

      // Всё остальное — наружу общим текстом, подробности только в журнал.
      console.error(
        JSON.stringify({ event: "unhandled", name: e?.name, message: e?.message }),
      );
      return json({ error: "Внутренняя ошибка" }, 500);
    }
  },
};
