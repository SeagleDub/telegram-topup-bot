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

/**
 * Длина from/subject в метаданных. mail-inbox всё равно хранит 200 символов,
 * а URL-кодирование раздувает кириллицу в 6 раз: без обрезки спам с темой на
 * 10 000 символов дал бы заголовок больше лимита Cloudflare и потерю письма.
 */
const META_MAX_CHARS = 200;

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
        from: (message.headers.get("from") || message.from).slice(0, META_MAX_CHARS),
        subject: (message.headers.get("subject") || "").slice(0, META_MAX_CHARS),
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
