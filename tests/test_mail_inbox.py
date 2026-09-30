"""
Тесты раздела «📧 Почта доменов» — то, что проверяется без сети.

  - parse_mail_query: ошибка здесь покажет пустой ящик вместо писем, и
    человек решит, что код не пришёл.
  - list_messages: домен — письма на все адреса (catch-all), точный адрес —
    только его письма, чтобы спам на другие адреса не вытеснил нужное.
  - render_message: письма бывают в cp1251, в quoted-printable, только в HTML;
    ссылка «Подтвердить» обязана дожить до Telegram, чужой текст экранируется,
    сообщение не превышает лимит Telegram.
  - button_label: тема в списке приходит как encoded-word и должна читаться.

Запуск: .venv/bin/python -m unittest discover -s tests -v
"""
import asyncio
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch
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


class ParseMailQueryTest(unittest.TestCase):
    def test_domain_variants_mean_whole_domain(self):
        for text in ("site1.com", " Site1.COM ", "https://www.site1.com/page?x=1", "site1.com."):
            with self.subTest(text=text):
                self.assertEqual(mail_inbox.parse_mail_query(text), ("site1.com", None))

    def test_address_means_only_that_address(self):
        self.assertEqual(mail_inbox.parse_mail_query("Facebook@Site1.com"), ("site1.com", "facebook@site1.com"))
        self.assertEqual(mail_inbox.parse_mail_query("info@site1.com"), ("site1.com", "info@site1.com"))

    def test_cyrillic_domain_becomes_punycode(self):
        self.assertEqual(mail_inbox.parse_mail_query("сайт.укр"), ("xn--80aswg.xn--j1amh", None))

    def test_garbage_rejected(self):
        for text in ("", "hello", "site1", "a@b@c", "site one.com", "@site1.com"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    mail_inbox.parse_mail_query(text)


class ListMessagesTest(unittest.TestCase):
    def _params_for(self, *args):
        calls = []

        async def fake_get(path, params):
            calls.append((path, params))
            return b"[]"

        with patch.object(mail_inbox, "_get", fake_get):
            asyncio.run(mail_inbox.list_messages(*args))
        return calls

    def test_domain_asks_for_all_addresses(self):
        self.assertEqual(self._params_for("site1.com"), [("/messages", {"domain": "site1.com"})])

    def test_address_asks_for_that_address_only(self):
        self.assertEqual(self._params_for("site1.com", "facebook@site1.com"),
                         [("/messages", {"address": "facebook@site1.com"})])


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

    def test_encoded_sender_name_with_comma(self):
        # Декодировать до разбора адреса нельзя: запятая в имени ломает parseaddr.
        item = dict(self._item(28), **{"from": "=?UTF-8?Q?Doe=2C_John?= <j@x.com>"})
        self.assertIn(" · Doe, John · ", mail_inbox.button_label(item, self.NOW))

    def test_domain_list_shows_recipient(self):
        # В списке домена письма идут на разные адреса — видно, на какой.
        item = dict(self._item(28), to="admin@site1.com")
        self.assertEqual(mail_inbox.button_label(item, self.NOW, with_recipient=True),
                         "12:41 · admin@ · Google · Код подтверждения")

    def test_long_random_recipient_does_not_eat_the_label(self):
        # Спам на catch-all приходит на длинные случайные адреса — отправитель и
        # тема должны остаться видны.
        item = dict(self._item(28), to="x" * 64 + "@site1.com")
        label = mail_inbox.button_label(item, self.NOW, with_recipient=True)
        self.assertIn("…@ · Google · ", label)


class RenderMessageTest(unittest.TestCase):
    def test_plain_cp1251_quoted_printable(self):
        raw = build_mail("Ваш код: 123456", charset="cp1251", cte="quoted-printable").as_bytes()
        text = mail_inbox.render_message(raw, kyiv_ms(28))
        self.assertIn("Ваш код: 123456", text)
        self.assertIn("<b>Код подтверждения</b>", text)
        self.assertIn("28.09.2026 12:41", text)

    def test_worst_case_mail_fits_telegram_limit(self):
        # Всё длинное сразу: тема, отправитель, получатель на 318 символов,
        # эмодзи в тексте, много вложений.
        msg = build_mail("😀" * 5000, subject="т" * 1000)
        msg.replace_header("From", "Я" * 500 + " <a@b.com>")
        for i in range(40):
            msg.add_attachment(b"x", maintype="application", subtype="pdf", filename=f"файл-{i:02d}-" + "д" * 30 + ".pdf")
        recipient = "x" * 64 + "@" + ".".join(["a" * 60] * 4) + ".com"
        text = mail_inbox.render_message(msg.as_bytes(), kyiv_ms(28), recipient)
        self.assertLess(len(text.encode("utf-16-le")) // 2, 4096)

    def test_recipient_line(self):
        # Адрес из конверта, а не заголовок To: при catch-all и скрытой копии
        # To может быть чужим.
        raw = build_mail("текст").as_bytes()
        self.assertIn("Кому: admin@site1.com", mail_inbox.render_message(raw, None, "admin@site1.com"))

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

    def test_emoji_mail_fits_telegram_limit(self):
        # Telegram считает длину в единицах UTF-16: эмодзи — две.
        raw = build_mail("😀" * 5000, subject="🔥" * 300).as_bytes()
        text = mail_inbox.render_message(raw, kyiv_ms(28))
        self.assertLess(len(text.encode("utf-16-le")) // 2, 4096)
        self.assertIn("…обрезано", text)

    def test_empty_plain_part_falls_back_to_html(self):
        msg = build_mail("")
        msg.add_alternative("<p>Ваш код: <b>481516</b></p>", subtype="html")
        self.assertIn("Ваш код: 481516", mail_inbox.render_message(msg.as_bytes(), None))

    def test_table_cells_do_not_stick_together(self):
        raw = build_mail("<table><tr><td>Код</td><td>123456</td><td>действует 10 мин</td></tr></table>",
                         subtype="html").as_bytes()
        self.assertIn("Код 123456 действует 10 мин", mail_inbox.render_message(raw, None))

    def test_attachments_listed_by_name(self):
        msg = build_mail("см. вложение")
        msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="invoice.pdf")
        text = mail_inbox.render_message(msg.as_bytes(), None)
        self.assertIn("см. вложение", text)
        self.assertIn("invoice.pdf", text)
        # Файлы теперь отдаются кнопками под письмом.
        self.assertNotIn("не пересылаются", text)


def mail_with_attachments() -> bytes:
    msg = build_mail("см. вложения")
    msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="invoice.pdf")
    msg.add_attachment("строка;1\n".encode("utf-8"), maintype="text", subtype="csv", filename="счёт.csv")
    msg.add_attachment(b"\x89PNG\r\n", maintype="image", subtype="png")  # без имени
    return msg.as_bytes()


class AttachmentsTest(unittest.TestCase):
    def test_list_names_and_sizes_in_mail_order(self):
        self.assertEqual(mail_inbox.list_attachments(mail_with_attachments()), [
            {"name": "invoice.pdf", "size": 8},
            {"name": "счёт.csv", "size": len("строка;1\n".encode("utf-8"))},
            {"name": "attachment-3.png", "size": 6},
        ])

    def test_get_attachment_returns_original_bytes(self):
        raw = mail_with_attachments()
        self.assertEqual(mail_inbox.get_attachment(raw, 0), ("invoice.pdf", b"%PDF-1.4"))
        self.assertEqual(mail_inbox.get_attachment(raw, 2), ("attachment-3.png", b"\x89PNG\r\n"))

    def test_get_attachment_out_of_range(self):
        with self.assertRaises(IndexError):
            mail_inbox.get_attachment(mail_with_attachments(), 3)

    def test_attachment_label_shows_readable_size(self):
        self.assertEqual(mail_inbox.attachment_label({"name": "a.pdf", "size": 512}), "📎 a.pdf · 512 Б")
        self.assertEqual(mail_inbox.attachment_label({"name": "a.pdf", "size": 120 * 1024}), "📎 a.pdf · 120 КБ")
        self.assertEqual(mail_inbox.attachment_label({"name": "a.pdf", "size": 5 * 1024 * 1024 + 1}), "📎 a.pdf · 5.0 МБ")


if __name__ == "__main__":
    unittest.main()
