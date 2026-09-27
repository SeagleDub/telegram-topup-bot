"""
Тесты раздела «📧 Почта доменов» — то, что проверяется без сети.

  - normalize_address: ошибка здесь покажет пустой ящик вместо писем, и
    человек решит, что код не пришёл.
  - render_message: письма бывают в cp1251, в quoted-printable, только в HTML;
    ссылка «Подтвердить» обязана дожить до Telegram, чужой текст экранируется,
    сообщение не превышает лимит Telegram.
  - button_label: тема в списке приходит как encoded-word и должна читаться.

Запуск: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import datetime
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


class NormalizeAddressTest(unittest.TestCase):
    def test_variants_become_info_address(self):
        for text in ("site1.com", " Site1.COM ", "info@site1.com", "https://www.site1.com/page?x=1", "site1.com."):
            with self.subTest(text=text):
                self.assertEqual(mail_inbox.normalize_address(text), "info@site1.com")

    def test_other_local_part_kept(self):
        self.assertEqual(mail_inbox.normalize_address("Admin@site1.com"), "admin@site1.com")

    def test_cyrillic_domain_becomes_punycode(self):
        self.assertEqual(mail_inbox.normalize_address("сайт.укр"), "info@xn--80aswg.xn--j1amh")

    def test_garbage_rejected(self):
        for text in ("", "hello", "site1", "a@b@c", "site one.com", "@site1.com"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    mail_inbox.normalize_address(text)


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


class RenderMessageTest(unittest.TestCase):
    def test_plain_cp1251_quoted_printable(self):
        raw = build_mail("Ваш код: 123456", charset="cp1251", cte="quoted-printable").as_bytes()
        text = mail_inbox.render_message(raw, kyiv_ms(28))
        self.assertIn("Ваш код: 123456", text)
        self.assertIn("<b>Код подтверждения</b>", text)
        self.assertIn("28.09.2026 12:41", text)

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


if __name__ == "__main__":
    unittest.main()
