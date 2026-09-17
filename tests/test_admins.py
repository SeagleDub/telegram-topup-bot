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
