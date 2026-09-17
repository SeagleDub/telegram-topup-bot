"""
Тесты многопользовательского админства (админ + несколько тимлидеров).

Покрыто намеренно именно это:

  - _parse_id_list читает список ID из .env. Ошибка здесь тихо урезает круг
    получателей: заявки просто перестают доходить до части команды, и никакого
    сбоя при этом не видно.

  - is_admin — единственная точка авторизации повышенного доступа. Ошибка в обе
    стороны опасна: лишний доступ либо потерянный тимлидер.

  - рассылка уведомлений обязана переживать сбой на одном получателе.
    Заблокировавший бота тимлидер не должен отменять доставку остальным.

  - связывание копий уведомления перешло с модели ПАРЫ на ГРУППУ. На паре при
    трёх получателях кнопка гасла у одного и навсегда зависала у остальных —
    это и есть то, что здесь проверяется.

Запуск: .venv/bin/python -m unittest discover -s tests -v
"""
import importlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import utils


class FakeMessage:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeBot:
    """Минимальный бот: считает отправки и правки, умеет падать на заданных чатах."""

    def __init__(self, failing_chats=()):
        self.failing_chats = set(failing_chats)
        self.sent = []     # [(chat_id, text)]
        self.edited = []   # [(chat_id, message_id, text)]
        self._next_id = 1000

    async def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.failing_chats:
            raise RuntimeError(f"chat {chat_id} заблокировал бота")
        self._next_id += 1
        self.sent.append((chat_id, text))
        return FakeMessage(self._next_id)

    async def edit_message_text(self, chat_id, message_id, text):
        if chat_id in self.failing_chats:
            raise RuntimeError(f"chat {chat_id} недоступен")
        self.edited.append((chat_id, message_id, text))
        return FakeMessage(message_id)


class ParseIdListTest(unittest.TestCase):
    def test_parses_multiple_ids(self):
        self.assertEqual(config._parse_id_list("111,222,333"), (111, 222, 333))

    def test_tolerates_whitespace_and_semicolons(self):
        self.assertEqual(config._parse_id_list(" 111 ; 222 , 333 "), (111, 222, 333))

    def test_single_id_stays_a_tuple(self):
        self.assertEqual(config._parse_id_list("578722266"), (578722266,))

    def test_drops_duplicates_but_keeps_order(self):
        self.assertEqual(config._parse_id_list("222,111,222"), (222, 111))

    def test_empty_input_is_empty_tuple(self):
        self.assertEqual(config._parse_id_list(""), ())
        self.assertEqual(config._parse_id_list(None), ())
        self.assertEqual(config._parse_id_list(" , ; "), ())

    def test_garbage_raises_instead_of_being_skipped(self):
        # Тихо проглоченный мусор = молча потерянный тимлидер. Падаем на старте.
        with self.assertRaises(ValueError):
            config._parse_id_list("111,абв")


class NotifyIdsTest(unittest.TestCase):
    def test_admin_goes_first(self):
        self.assertEqual(config.NOTIFY_IDS[0], config.ADMIN_ID)

    def test_every_teamleader_is_a_recipient(self):
        for tid in config.TEAMLEADER_IDS:
            self.assertIn(tid, config.NOTIFY_IDS)

    def test_no_duplicate_recipients(self):
        # Админ, вписанный и в тимлидеры, не должен получать два сообщения.
        self.assertEqual(len(config.NOTIFY_IDS), len(set(config.NOTIFY_IDS)))


class IsAdminTest(unittest.TestCase):
    def test_admin_and_every_teamleader_pass(self):
        with patch.object(utils, "ADMIN_ID", 1), \
             patch.object(utils, "TEAMLEADER_IDS", (2, 3, 4)):
            for uid in (1, 2, 3, 4):
                self.assertTrue(utils.is_admin(uid), uid)

    def test_outsider_is_rejected(self):
        with patch.object(utils, "ADMIN_ID", 1), \
             patch.object(utils, "TEAMLEADER_IDS", (2, 3, 4)):
            for uid in (5, 0, -1, 22, 34):
                self.assertFalse(utils.is_admin(uid), uid)


class SendNotificationTest(unittest.IsolatedAsyncioTestCase):
    async def test_reaches_all_recipients(self):
        bot = FakeBot()
        with patch.object(utils, "NOTIFY_IDS", (1, 2, 3)):
            delivered = await utils.send_notification_to_admins(bot, "заявка")
        self.assertEqual(set(delivered), {1, 2, 3})
        self.assertEqual([chat for chat, _ in bot.sent], [1, 2, 3])

    async def test_one_blocked_recipient_does_not_stop_the_rest(self):
        bot = FakeBot(failing_chats={2})
        with patch.object(utils, "NOTIFY_IDS", (1, 2, 3)):
            delivered = await utils.send_notification_to_admins(bot, "заявка")
        self.assertEqual(set(delivered), {1, 3})
        self.assertEqual([chat for chat, _ in bot.sent], [1, 3])

    async def test_total_failure_returns_empty_and_does_not_raise(self):
        bot = FakeBot(failing_chats={1, 2, 3})
        with patch.object(utils, "NOTIFY_IDS", (1, 2, 3)):
            delivered = await utils.send_notification_to_admins(bot, "заявка")
        self.assertEqual(delivered, {})


class LinkedGroupTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        utils.linked_messages.clear()
        utils.linked_groups.clear()

    tearDown = setUp

    async def test_all_other_copies_are_updated(self):
        bot = FakeBot()
        with patch.object(utils, "NOTIFY_IDS", (1, 2, 3)):
            delivered = await utils.send_notification_with_buttons(bot, "заявка", None)

        handler_chat = 2
        await utils.update_linked_messages(bot, handler_chat, delivered[handler_chat], "заявка\n\n✅ ВЫПОЛНЕНО")

        # Обработавший чат правит своё сообщение сам — здесь только остальные.
        self.assertEqual({chat for chat, _, _ in bot.edited}, {1, 3})
        for _, _, text in bot.edited:
            self.assertIn("ВЫПОЛНЕНО", text)

    async def test_group_is_cleared_and_second_call_is_noop(self):
        bot = FakeBot()
        with patch.object(utils, "NOTIFY_IDS", (1, 2, 3)):
            delivered = await utils.send_notification_with_buttons(bot, "заявка", None)

        await utils.update_linked_messages(bot, 2, delivered[2], "готово")
        self.assertEqual(utils.linked_messages, {})
        self.assertEqual(utils.linked_groups, {})

        bot.edited.clear()
        # Второй нажавший не должен ничего перезаписать повторно.
        await utils.update_linked_messages(bot, 1, delivered[1], "ещё раз")
        self.assertEqual(bot.edited, [])

    async def test_unknown_message_is_ignored(self):
        bot = FakeBot()
        await utils.update_linked_messages(bot, 999, 12345, "текст")
        self.assertEqual(bot.edited, [])

    async def test_failed_edit_does_not_block_remaining_copies(self):
        bot = FakeBot(failing_chats={3})
        with patch.object(utils, "NOTIFY_IDS", (1, 2, 3, 4)):
            delivered = await utils.send_notification_with_buttons(bot, "заявка", None)

        await utils.update_linked_messages(bot, 2, delivered[2], "готово")
        # 3 упал, но 4 (идущий после него) всё равно обновлён.
        self.assertEqual({chat for chat, _, _ in bot.edited}, {1, 4})

    async def test_undelivered_recipient_is_not_linked(self):
        bot = FakeBot(failing_chats={2})
        with patch.object(utils, "NOTIFY_IDS", (1, 2, 3)):
            delivered = await utils.send_notification_with_buttons(bot, "заявка", None)

        self.assertNotIn(2, delivered)
        self.assertEqual(len(utils.linked_messages), 2)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# Роль «проверяющий расходы»
#
# Роль ниже админа по правам, и главный риск здесь — не отказ в доступе, а
# тихое расширение: одна строчка в is_admin выдала бы ей одобрение заявок.
# Поэтому проверяется в обе стороны — что право есть и что лишнего нет.
# --------------------------------------------------------------------------- #
ADMIN = 1
TEAMLEADERS = (2, 3)
VIEWERS = (7, 8)
OUTSIDER = 99


def _role_patches():
    """Единая расстановка ролей для тестов: 1 админ, 2 тимлида, 2 проверяющих."""
    return (
        patch.object(utils, "ADMIN_ID", ADMIN),
        patch.object(utils, "TEAMLEADER_IDS", TEAMLEADERS),
        patch.object(utils, "EXPENSE_VIEWER_IDS", VIEWERS),
        patch.object(utils, "ROLE_IDS", frozenset({ADMIN, *TEAMLEADERS, *VIEWERS})),
    )


class ExpenseViewerRoleTest(unittest.TestCase):
    def setUp(self):
        for p in _role_patches():
            p.start()
            self.addCleanup(p.stop)

    def test_viewer_may_read_buyer_expenses(self):
        for uid in VIEWERS:
            self.assertTrue(utils.can_view_buyer_expenses(uid), uid)

    def test_admin_and_teamleaders_keep_the_permission(self):
        for uid in (ADMIN, *TEAMLEADERS):
            self.assertTrue(utils.can_view_buyer_expenses(uid), uid)

    def test_outsider_may_not_read_buyer_expenses(self):
        self.assertFalse(utils.can_view_buyer_expenses(OUTSIDER))

    def test_viewer_is_not_an_admin(self):
        # Главная гарантия роли: одобрение заявок и автопродление остаются закрыты.
        for uid in VIEWERS:
            self.assertFalse(utils.is_admin(uid), uid)

    def test_viewer_enters_the_bot_without_being_whitelisted(self):
        # Вайтлист — это байеры из таблицы; роль задана руками в .env.
        with patch.object(utils, "get_whitelist", side_effect=AssertionError("вайтлист не должен опрашиваться")):
            for uid in VIEWERS:
                self.assertTrue(utils.is_user_allowed(uid), uid)

    def test_outsider_still_falls_through_to_the_whitelist(self):
        with patch.object(utils, "get_whitelist", return_value={OUTSIDER}):
            self.assertTrue(utils.is_user_allowed(OUTSIDER))
        with patch.object(utils, "get_whitelist", return_value=set()):
            self.assertFalse(utils.is_user_allowed(OUTSIDER))

    def test_viewer_gets_no_request_notifications(self):
        self.assertTrue(set(VIEWERS).isdisjoint(config.NOTIFY_IDS))


class RoleOverlapTest(unittest.TestCase):
    """config.py обязан падать на старте, если роли пересеклись.

    Перезагружает модуль с подменённым окружением: проверять надо саму
    загрузку конфига, а не копию его логики в тесте.
    """

    def _reload_config(self, **env):
        with patch.dict(os.environ, env, clear=False):
            importlib.reload(config)

    def tearDown(self):
        # Вернуть модуль к состоянию из настоящего .env, иначе испортим
        # остальные тесты, читающие config.NOTIFY_IDS.
        importlib.reload(config)

    def test_viewer_listed_among_admins_aborts_startup(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._reload_config(
                ADMIN_ID="1", TEAMLEADER_IDS="2,3", EXPENSE_VIEWER_IDS="3,7",
            )
        self.assertIn("3", str(ctx.exception))

    def test_viewer_equal_to_admin_aborts_startup(self):
        with self.assertRaises(RuntimeError):
            self._reload_config(
                ADMIN_ID="1", TEAMLEADER_IDS="2", EXPENSE_VIEWER_IDS="1",
            )

    def test_disjoint_roles_load_fine(self):
        self._reload_config(ADMIN_ID="1", TEAMLEADER_IDS="2,3", EXPENSE_VIEWER_IDS="7,8")
        self.assertEqual(config.EXPENSE_VIEWER_IDS, (7, 8))
        self.assertEqual(config.NOTIFY_IDS, (1, 2, 3))
        self.assertEqual(config.ROLE_IDS, frozenset({1, 2, 3, 7, 8}))

    def test_role_is_optional(self):
        self._reload_config(ADMIN_ID="1", TEAMLEADER_IDS="2", EXPENSE_VIEWER_IDS="")
        self.assertEqual(config.EXPENSE_VIEWER_IDS, ())

    def test_live_config_has_no_overlap(self):
        self.assertTrue(set(config.EXPENSE_VIEWER_IDS).isdisjoint(config.NOTIFY_IDS))


class MenuKeyboardTest(unittest.TestCase):
    BUYER_EXPENSE_BUTTON = "📊 Получить расход по байеру"

    def setUp(self):
        for p in _role_patches():
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _button_texts(kb):
        return {btn.text for row in kb.keyboard for btn in row}

    def test_viewer_sees_the_buyer_expense_button(self):
        import keyboards
        for uid in VIEWERS:
            self.assertIn(self.BUYER_EXPENSE_BUTTON, self._button_texts(keyboards.get_menu_keyboard(uid)), uid)

    def test_plain_user_does_not_see_it(self):
        import keyboards
        self.assertNotIn(self.BUYER_EXPENSE_BUTTON, self._button_texts(keyboards.get_menu_keyboard(OUTSIDER)))

    def test_menu_matches_the_gate_exactly(self):
        # Кнопка видна ровно тем, кого пропустит expense_view_only. Расхождение
        # означает либо мёртвую кнопку, либо видимую функцию без права.
        import keyboards
        for uid in (ADMIN, *TEAMLEADERS, *VIEWERS, OUTSIDER):
            visible = self.BUYER_EXPENSE_BUTTON in self._button_texts(keyboards.get_menu_keyboard(uid))
            self.assertEqual(visible, utils.can_view_buyer_expenses(uid), uid)


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id
        self.username = "tester"


class FakeEvent:
    """Событие без привязки к aiogram: декоратору достаточно from_user."""

    def __init__(self, user_id):
        self.from_user = FakeUser(user_id)


class AuthDecoratorTest(unittest.IsolatedAsyncioTestCase):
    """Гейт хендлера — реальная граница прав, а не предикат сам по себе.

    Предикаты можно проверить и напрямую, но в проде вызывается декоратор:
    если он навешан не тот, тесты предикатов этого не заметят.
    """

    def setUp(self):
        for p in _role_patches():
            p.start()
            self.addCleanup(p.stop)
        self.calls = []

        async def handler(event, *args, **kwargs):
            self.calls.append(event.from_user.id)
            return "выполнено"

        self.handler = handler

    async def test_viewer_passes_the_expense_gate(self):
        from middlewares.auth import expense_view_only
        guarded = expense_view_only(self.handler)
        for uid in VIEWERS:
            self.assertEqual(await guarded(FakeEvent(uid)), "выполнено", uid)
        self.assertEqual(self.calls, list(VIEWERS))

    async def test_viewer_is_blocked_by_the_admin_gate(self):
        # Ключевая гарантия: одобрение заявок и автопродление роли недоступны.
        from middlewares.auth import admin_only
        guarded = admin_only(self.handler)
        for uid in VIEWERS:
            self.assertIsNone(await guarded(FakeEvent(uid)), uid)
        self.assertEqual(self.calls, [])

    async def test_outsider_is_blocked_by_both_gates(self):
        from middlewares.auth import admin_only, expense_view_only
        for gate in (admin_only, expense_view_only):
            self.assertIsNone(await gate(self.handler)(FakeEvent(OUTSIDER)))
        self.assertEqual(self.calls, [])

    async def test_admin_passes_both_gates(self):
        from middlewares.auth import admin_only, expense_view_only
        for gate in (admin_only, expense_view_only):
            self.assertEqual(await gate(self.handler)(FakeEvent(ADMIN)), "выполнено")

    async def test_event_without_user_is_rejected(self):
        from middlewares.auth import expense_view_only
        guarded = expense_view_only(self.handler)

        class NoUser:
            from_user = None

        self.assertIsNone(await guarded(NoUser()))
        self.assertEqual(self.calls, [])

    async def test_wraps_preserves_signature_for_aiogram(self):
        # Без functools.wraps aiogram передаёт хендлеру весь контекст и тот
        # падает с TypeError, а кнопка молча перестаёт работать.
        from middlewares.auth import expense_view_only
        guarded = expense_view_only(self.handler)
        self.assertIs(guarded.__wrapped__, self.handler)
        self.assertEqual(guarded.__name__, self.handler.__name__)


class ExpenseHandlerGateTest(unittest.TestCase):
    """Проверка, что нужный декоратор реально навешан на нужные хендлеры."""

    def test_buyer_expense_handlers_use_the_expense_gate(self):
        import handlers.expenses as expenses
        for fn in (expenses.get_buyer_expense_start, expenses.process_buyer_id):
            self.assertTrue(hasattr(fn, "__wrapped__"), f"{fn.__name__} без гейта")

    def test_admin_callbacks_still_require_admin(self):
        # Одобрение заявок не должно было расшириться вместе с расходами.
        import handlers.common as common
        with patch.object(utils, "ADMIN_ID", ADMIN), \
             patch.object(utils, "TEAMLEADER_IDS", TEAMLEADERS), \
             patch.object(utils, "EXPENSE_VIEWER_IDS", VIEWERS):
            for uid in VIEWERS:
                self.assertFalse(utils.is_admin(uid), uid)
        self.assertTrue(hasattr(common.approve_request, "__wrapped__"))
