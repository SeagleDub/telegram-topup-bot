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

    def test_catch_all_forward_is_skipped(self):
        # Catch-all уже доставляет и info@ — наше правило увело бы эти письма.
        catch_all = {"enabled": True, "actions": [{"type": "forward", "value": ["boss@gmail.com"]}]}
        plan = sr.plan_domain(DOMAIN, CF_MX, [], True, [], catch_all=catch_all)
        self.assertIn("boss@gmail.com", plan.skip)

    def test_catch_all_drop_or_disabled_is_fine(self):
        for catch_all in ({"enabled": True, "actions": [{"type": "drop"}]},
                          {"enabled": False, "actions": [{"type": "forward", "value": ["x@y.com"]}]}):
            with self.subTest(catch_all=catch_all):
                self.assertIsNone(sr.plan_domain(DOMAIN, CF_MX, [], True, [], catch_all=catch_all).skip)

    def test_broken_routing_is_reported_not_called_configured(self):
        plan = sr.plan_domain(DOMAIN, CF_MX, [], True, [OUR_RULE], routing_status="misconfigured")
        self.assertIn("misconfigured", plan.skip)

    def test_null_mx_reason_is_readable(self):
        plan = sr.plan_domain(DOMAIN, [{"name": DOMAIN, "content": "."}], [], False, [])
        self.assertTrue(plan.skip.endswith(": ."))


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

    def test_russian_excel_decimal_comma_in_column_2(self):
        # Русский Excel: разделитель «;», а «,» — десятичная запятая.
        # По одной запятой в каждой строке: для csv.Sniffer «,» выглядит таким же
        # стабильным разделителем, как «;», и он выбирает «,».
        rows, skipped = self._read("a@b.com:tok1;12,50;site1.com\nc@d.com:tok2;7,25;site2.com\n")
        self.assertEqual(skipped, [])
        self.assertEqual(rows, [sr.Row(1, "a@b.com", "tok1", "site1.com"),
                                sr.Row(2, "c@d.com", "tok2", "site2.com")])


class ScriptedAPI(sr.CloudflareAPI):
    """CloudflareAPI без сети: _send отдаёт заготовленные ответы по очереди."""

    def __init__(self, outcomes):
        super().__init__("a@b.com", "tok")
        self.outcomes = list(outcomes)
        self.modes = []

    def _send(self, mode, method, path, body, raw, content_type):
        self.modes.append(mode)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class CloudflareAPITest(unittest.TestCase):
    def test_falls_back_to_global_key_and_remembers(self):
        api = ScriptedAPI([sr.CFError(400, "not a token"), ["zone"], ["again"]])
        self.assertEqual(api.call("GET", "/zones"), ["zone"])
        self.assertEqual(api.call("GET", "/zones"), ["again"])
        self.assertEqual(api.modes, ["token", "key", "key"])

    def test_server_error_does_not_trigger_fallback(self):
        api = ScriptedAPI([sr.CFError(500, "boom")])
        with self.assertRaises(sr.CFError):
            api.call("GET", "/zones")
        self.assertEqual(api.modes, ["token"])

    def test_both_auth_modes_rejected_reports_both(self):
        api = ScriptedAPI([sr.CFError(403, "token says no"), sr.CFError(403, "key says no")])
        with self.assertRaises(sr.CFError) as ctx:
            api.call("GET", "/zones")
        self.assertIn("token says no", str(ctx.exception))
        self.assertIn("key says no", str(ctx.exception))
        self.assertIsNone(api.mode)

    def test_list_all_walks_pages(self):
        api = ScriptedAPI([[1, 2], [3]])
        api.mode = "token"
        self.assertEqual(api.list_all("/rules", per_page=2), [1, 2, 3])


class FakeAPI:
    """Аккаунт a1 с активной зоной z1 без почты; записывает вызовы."""

    def __init__(self, routing=None):
        self.calls = []
        self.routing = routing or {"enabled": False, "status": "unconfigured"}

    def call(self, method, path, body=None, *, raw=None, content_type=None):
        self.calls.append((method, path.split("?")[0]))
        if path.startswith("/zones?"):
            return [{"id": "z1", "status": "active", "account": {"id": "a1"}}]
        if path.endswith("/email/routing"):
            return self.routing
        if path.endswith("/catch_all"):
            return {"enabled": False, "actions": [{"type": "drop"}]}
        return []

    def list_all(self, path, per_page):
        self.calls.append(("GET", path))
        return []

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


CONFIG = {"ingest_url": "https://mail-inbox.x.workers.dev/ingest", "relay_token": "t", "relay_code": b"code"}


class ProcessTest(unittest.TestCase):
    def test_relay_is_uploaded_before_dns_changes(self):
        # Сбой заливки (раздел 9 спеки) не должен оставить домен с
        # включённой маршрутизацией и заблокированными MX без пересыльщика.
        api = FakeAPI()
        status, _ = sr.process(sr.Row(1, "a@b.com", "t", DOMAIN), api, CONFIG, set(), dry_run=False)
        self.assertEqual(status, sr.OK)
        self.assertEqual(api.writes(), [
            ("PUT", "/accounts/a1/workers/scripts/mail-inbox-relay"),
            ("POST", "/zones/z1/email/routing/dns"),
            ("POST", "/zones/z1/email/routing/rules"),
        ])

    def test_dry_run_writes_nothing(self):
        api = FakeAPI()
        status, _ = sr.process(sr.Row(1, "a@b.com", "t", DOMAIN), api, None, set(), dry_run=True)
        self.assertEqual(status, sr.PLANNED)
        self.assertEqual(api.writes(), [])

    def test_relay_uploaded_once_per_account(self):
        api, uploaded = FakeAPI(), set()
        for row in (sr.Row(1, "a@b.com", "t", DOMAIN), sr.Row(2, "a@b.com", "t", "site2.com")):
            sr.process(row, api, CONFIG, uploaded, dry_run=False)
        self.assertEqual([c for c in api.writes() if c[0] == "PUT"],
                         [("PUT", "/accounts/a1/workers/scripts/mail-inbox-relay")])


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
