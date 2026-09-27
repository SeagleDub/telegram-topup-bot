"""
Тесты скрипта настройки почты.

Покрыто то, что решает, трогать ли чужой домен: plan_domain (ошибка здесь
ломает кому-то работающую почту) и разбор CSV (ошибка — тихо пропущенные или
перепутанные домены). Плюс тело заливки пересыльщика: опечатка в имени биндинга
всплыла бы только на первом письме.

Запуск: .venv/bin/python -m unittest discover -s mail-inbox/setup -v
"""
import email
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import setup_routing as sr

DOMAIN = "site1.com"
CF_MX = [{"name": DOMAIN, "content": "route1.mx.cloudflare.net"}]
OUR_RULE = {
    "id": "r1",
    "enabled": True,
    "matchers": [{"type": "literal", "field": "to", "value": "info@site1.com"}],
    "actions": [{"type": "worker", "value": ["mail-inbox-relay"]}],
}


class PlanDomainTest(unittest.TestCase):
    def test_fresh_domain_gets_everything(self):
        plan = sr.plan_domain(DOMAIN, [], [], False, [])
        self.assertIsNone(plan.skip)
        self.assertTrue(plan.enable_routing)
        self.assertTrue(plan.create_rule)
        self.assertIsNone(plan.enable_rule)

    def test_foreign_mx_is_skipped(self):
        mx = [{"name": DOMAIN, "content": "aspmx.l.google.com"}]
        self.assertIn("aspmx.l.google.com", sr.plan_domain(DOMAIN, mx, [], False, []).skip)

    def test_mx_on_subdomain_is_not_a_conflict(self):
        mx = [{"name": "send.site1.com", "content": "feedback-smtp.amazonses.com"}]
        self.assertIsNone(sr.plan_domain(DOMAIN, mx, [], False, []).skip)

    def test_own_spf_blocks_enabling(self):
        txt = [{"name": DOMAIN, "content": '"v=spf1 include:_spf.google.com ~all"'}]
        self.assertIn("SPF", sr.plan_domain(DOMAIN, [], txt, False, []).skip)

    def test_own_spf_ignored_when_routing_already_enabled(self):
        txt = [{"name": DOMAIN, "content": "v=spf1 include:_spf.google.com ~all"}]
        self.assertIsNone(sr.plan_domain(DOMAIN, CF_MX, txt, True, []).skip)

    def test_cloudflare_spf_is_not_a_conflict(self):
        txt = [{"name": DOMAIN, "content": "v=spf1 include:_spf.mx.cloudflare.net ~all"}]
        self.assertIsNone(sr.plan_domain(DOMAIN, CF_MX, txt, False, []).skip)

    def test_info_forwarded_elsewhere_is_skipped(self):
        rule = dict(OUR_RULE, actions=[{"type": "forward", "value": ["boss@gmail.com"]}])
        self.assertIn("boss@gmail.com", sr.plan_domain(DOMAIN, CF_MX, [], True, [rule]).skip)

    def test_rule_match_ignores_case(self):
        rule = dict(OUR_RULE, matchers=[{"type": "literal", "field": "to", "value": "INFO@Site1.com"}],
                    actions=[{"type": "drop"}])
        self.assertIsNotNone(sr.plan_domain(DOMAIN, CF_MX, [], True, [rule]).skip)

    def test_configured_domain_needs_nothing(self):
        plan = sr.plan_domain(DOMAIN, CF_MX, [], True, [OUR_RULE])
        self.assertIsNone(plan.skip)
        self.assertTrue(plan.nothing_to_do)

    def test_our_disabled_rule_gets_enabled(self):
        rule = dict(OUR_RULE, enabled=False)
        plan = sr.plan_domain(DOMAIN, CF_MX, [], True, [rule])
        self.assertIs(plan.enable_rule, rule)
        self.assertFalse(plan.create_rule)


class ReadRowsTest(unittest.TestCase):
    def _read(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "in.csv"
            path.write_text(text, encoding="utf-8")
            return sr.read_rows(path)

    def test_comma_with_header(self):
        rows, skipped = self._read("account,x,domain\na@b.com:tok123,foo,Site1.com\n")
        self.assertEqual(skipped, [(1, "в колонке 1 нет «email:token»")])
        self.assertEqual(rows, [sr.Row(2, "a@b.com", "tok123", "site1.com")])

    def test_semicolon_and_bom(self):
        rows, _ = self._read("﻿a@b.com:tok;x;site2.com\n")
        self.assertEqual(rows, [sr.Row(1, "a@b.com", "tok", "site2.com")])

    def test_missing_domain_is_reported(self):
        rows, skipped = self._read("a@b.com:tok,x\n")
        self.assertEqual(rows, [])
        self.assertEqual(skipped, [(1, "нет домена в колонке 3")])


class RelayUploadTest(unittest.TestCase):
    def test_metadata_and_module(self):
        body, content_type = sr.build_relay_upload(
            "https://mail-inbox.x.workers.dev/ingest", "secret", b"export default {}")
        msg = email.message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
        parts = {p.get_param("name", header="content-disposition"): p for p in msg.get_payload()}

        meta = json.loads(parts["metadata"].get_payload(decode=True))
        self.assertEqual(meta["main_module"], "relay.mjs")
        self.assertIn("global_fetch_strictly_public", meta["compatibility_flags"])
        self.assertEqual({b["name"]: (b["type"], b["text"]) for b in meta["bindings"]}, {
            "INGEST_URL": ("plain_text", "https://mail-inbox.x.workers.dev/ingest"),
            "RELAY_TOKEN": ("secret_text", "secret"),
        })
        self.assertEqual(parts["relay.mjs"].get_content_type(), "application/javascript+module")
        self.assertEqual(parts["relay.mjs"].get_payload(decode=True), b"export default {}")

    def test_relay_file_is_where_script_expects(self):
        self.assertTrue(sr.RELAY_FILE.is_file())


if __name__ == "__main__":
    unittest.main()
