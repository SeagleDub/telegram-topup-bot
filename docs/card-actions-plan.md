# План: «Действия с картами» (AdsCard)

## Контекст
В бот добавляется новая функция меню — **«💳 Действия с картами»**. Доступна всем
пользователям (`is_user_allowed`). Позволяет по номеру карты в банке **AdsCard**
выполнить три действия:
1. Поменять лимит на карте
2. Заблокировать карту
3. Получить список последних транзакций (до 10)

Тип работы: **New feature**.

Реализованы оба банка: **AdsCard** и **MultiCards**.

## Внешний API (AdsCard, `https://talkv2.adscard.net/v3`)
Аутентификация — два значения из `.env`:
- `ADSCARD_TOKEN` → заголовок `Application-Authorization: Bearer <ADSCARD_TOKEN>`
- `ADSCARD_AUTH_TOKEN` → поле `auth_token` в JSON-теле каждого запроса

Аккаунт командный — карты лежат под пользователями команды, поэтому для
список/блок/транзакции используем **teams/***. Смены лимита в `teams/*` нет,
поэтому лимит меняем общим `cards/limit` — проверено на живом ответе: для
командных карт работает, возвращает обновлённую карту с новым `limit`.

Используемые эндпоинты (все POST, JSON):
| Действие | Endpoint | Тело | Ответ |
|---|---|---|---|
| Список карт | `teams/cards_list` | `{auth_token}` (без user_id — вся команда) | `data: {idx: {id, number, status, limit, currency, balance, ...}}` |
| Изменить лимит | `cards/limit` | `{auth_token, cards_id:[id], limit}` | обновлённая карта |
| Заблокировать | `teams/cards_block` | `{auth_token, cards_id:[id]}` | `data: {success: true}` |
| Транзакции | `teams/cards_transactions` | `{auth_token, time}` (без card_id — вся команда) | `data: {idx: {card_number, amount, fee, date, merchant, status, currency, group_id, ...}}` |

Поиск карты по введённому номеру: пользователь вводит **полный номер карты**.
Тянем `teams/cards_list`, сначала ищем точное совпадение цифр по полю `number`;
если точного нет (номера могут быть маскированы) — фолбэк по последним 4 цифрам.
Если найдено несколько — берём первую и показываем предупреждение; если ничего —
сообщаем об ошибке.

`teams/cards_transactions` требует `time` (используем `"month"`) и **не принимает
card_id** — отдаёт транзакции всей команды. Фильтруем на стороне бота по
последним 4 цифрам номера выбранной карты, берём первые 10.

## Внешний API (MultiCards, `https://api.multicards.io/v1`)
Аутентификация: логин по email/password → JWT.
- `MULTICARDS_EMAIL`, `MULTICARDS_PASSWORD` в `.env`.
- `POST /auth/login` `{email, password}` → `{token}` (JWT, RS256). Токен кэшируется
  в памяти сервиса, `exp` берётся из JWT, перелогин по истечении или при 401.
- Токен шлём в заголовке `x-auth-token` (и дублируем `Authorization: Bearer` для
  совместимости — доки по логину и по картам расходятся в способе передачи).

Используемые эндпоинты:
| Действие | Endpoint | Тело | Ответ |
|---|---|---|---|
| Список карт | `GET /card/list` | — | JSON-массив **полных** карт (как `/card/{id}`): `id`, `cardNumber`, `status`, `limitAmount`, `dailyLimitAmount`, `balanceAmount`, `spendAmount`, `dailySpendAmount`, ... |
| Глобальный лимит | `POST /card/{id}/update` | `{totalLimit}` | обновлённая карта (`limitAmount`) |
| Дневной лимит | `POST /card/{id}/update` | `{dailyLimit}` | обновлённая карта (`dailyLimitAmount`) |
| Заблокировать (закрыть) | `POST /card/{id}/close` | — | карта (`status` → не `ACTIVE`, напр. `CLOSED`) |
| Транзакции | `POST /transaction/pageable` | `{periodStart, periodEnd}` (unix-строки, текущий календарный месяц) | `{items: [...]}`, фильтр по `cardId` на стороне бота |

У MultiCards лимит раздельный → **две кнопки**: «Глобальный лимит» и «Дневной
лимит». У AdsCard — одна кнопка «Поменять лимит».

## Что будет построено

### 1. `config.py`
Добавить чтение `ADSCARD_TOKEN`, `ADSCARD_AUTH_TOKEN` из окружения.

### 2. `services/adscard.py` (новый)
По образцу [services/luboydomen.py](../services/luboydomen.py):
- общий `_post(endpoint, payload)` через `aiohttp` с заголовком Bearer и
  вмешиванием `auth_token` в тело; единый разбор ответа/ошибок (noisy errors,
  не «тихие» None);
- `get_cards() -> dict`
- `find_card_by_number(number) -> dict | None` (тянет список, матчит)
- `set_card_limit(card_id, limit) -> dict`
- `block_card(card_id) -> dict`
- `get_card_transactions(card_id, time="month") -> dict`

### 3. `states.py`
Новая группа состояний:
```
card_actions_choose_bank
card_actions_enter_number
card_actions_choose_action
card_actions_enter_limit
card_actions_confirm_block
```

### 4. `keyboards.py`
- В `menu_kb_user` и `menu_kb_admin_teamleader` добавить кнопку
  `«💳 Действия с картами»`.
- `get_card_bank_keyboard()` — inline: `AdsCard`, `MultiCards` (обе активны).
- `get_card_action_keyboard(bank)` — inline, зависит от банка: для AdsCard
  «Поменять лимит», для MultiCards «Глобальный лимит» + «Дневной лимит»; далее
  Заблокировать / Последние транзакции.
- `get_card_block_confirm_keyboard()` — Подтвердить / Отмена.

### 5. `handlers/card_actions.py` (новый, router)
FSM-флоу:
1. `«💳 Действия с картами»` → выбор банка (`card_actions_choose_bank`).
2. `bank:adscard` / `bank:multicards` → запрос полного номера карты.
3. Ввод номера → `find_card_by_number` соответствующего банка; при успехе
   сохраняем `bank`/`card_id`/`card_number`, показываем карту (маскированный
   номер) и меню действий банка.
4. **Изменить лимит**: ввод суммы (валидация — целое/число ≥ 0) → `set_card_limit`
   → подтверждение результата.
5. **Заблокировать**: экран подтверждения → `block_card` → результат.
6. **Транзакции**: `get_team_transactions` → фильтр по карте → до 10 строк
   (дата, тип, сумма+валюта, merchant), номера карт маскируются.

После любого действия (лимит / блокировка / транзакции, в т.ч. при ошибке или
отмене блокировки) пользователь **возвращается в меню действий по той же карте**
(`card_actions_choose_action`), карта остаётся в state — можно выполнить ещё
одно действие без повторного поиска. Выход в главное меню — только по «Отмена»
(глобальный хендлер) или в защитной ветке «карта не выбрана».

Везде кнопка «❌ Отмена» (общий хендлер в [handlers/common.py](../handlers/common.py)),
очистка сообщений через `last_messages`/`delete_last_messages`.

### 6. `main.py`
Зарегистрировать `card_actions.router`.

## Безопасность / чувствительные данные
- Номера карт **маскируются** перед выводом в Telegram: `**** **** **** 1234`
  (хелпер `mask_card_number`). CVC/полный номер в чат не выводим.
- Ошибки внешнего API логируются с контекстом, пользователю — общее сообщение,
  без сырого текста ответа API.
- Лимит валидируется на стороне бота (число ≥ 0) до отправки в API.

После любого действия — возврат в меню действий по той же карте (карта остаётся
в state). Выход в главное меню — по «Отмена» или в ветке «карта не выбрана».

Внутри флоу показывается клавиатура `card_flow_kb`: «🔄 Другая карта» + «❌ Отмена».
«Другая карта» (`ANOTHER_CARD_TEXT`) сбрасывает выбранную карту и возвращает к
выбору банка; обработчик зарегистрирован раньше обработчиков ввода и отфильтрован
по состояниям флоу (`StateFilter(*CARD_STATES)`), чтобы текст кнопки не считался
вводом номера/лимита.

Примечание: часть пунктов главного меню временно скрыта (закомментирована в
`keyboards.py`): «Перевод лендинга», «Добавить пиксель в систему», «Купить номера»,
«Список номеров», «Автопродление номеров», «Получить SMS Google Ads». Обработчики
этих функций остаются рабочими — скрыты только кнопки.

## Definition of Done
- [ ] Кнопка появляется в обоих меню; флоу проходится end-to-end на AdsCard и MultiCards.
- [ ] Изменение лимита реально меняет лимит (проверка по ответу API): AdsCard —
      один лимит; MultiCards — глобальный (`limitAmount`) и дневной (`dailyLimitAmount`).
- [ ] Блокировка подтверждается по ответу (AdsCard `closed_at`/`status:"D"`,
      MultiCards `status` ≠ `ACTIVE`).
- [ ] Транзакции выводятся (до 10, маскированные номера, фильтр по карте).
- [ ] Несуществующий номер карты → понятная ошибка, без traceback.
- [ ] Ошибка/таймаут/невалидный токен API → понятное сообщение, залогировано + Bugsnag.
- [ ] «Отмена» на любом шаге возвращает в главное меню и чистит состояние.

## Открытые вопросы / отложено
- MultiCards: токен кэшируется в памяти процесса (на инстанс). Для multi-instance
  понадобится общий кэш — пока не требуется.
- MultiCards `/card/list` возвращает полный объект карты (с `limitAmount`/
  `dailyLimitAmount`), поэтому отдельный `GET /card/{id}` не нужен. Применённый
  после изменения лимит берём из ответа `/card/{id}/update`.
- Способ передачи JWT (x-auth-token vs Authorization) в доках расходится — шлём оба.
- MultiCards «Заблокировать» = `/card/{id}/close` (необратимо), в `teams/*` AdsCard — `cards_block`.
- Нужна ли пагинация транзакций глубже 10 — пока нет.
