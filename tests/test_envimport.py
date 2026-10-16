import tempfile
import unittest
from pathlib import Path

from daykeeper.envimport import (
    GROUP_BROKERS,
    GROUP_INTEGRATIONS,
    GROUP_OTHER,
    GROUP_STANDS,
    apply_plan,
    classify,
    describe_plan,
    is_secret,
    parse_env,
    plan_import,
)
from daykeeper.vault import Vault

SAMPLE = """
# stands
DEV_API_BASE_URL=https://dev.example
DEV_UI_BASE_URL="https://dev-ui.example"
DEV_QA_API_TOKEN='tok-dev-qa'
QA_EU_ADMIN_EMAIL=admin@example
QA_EU_ADMIN_PASSWORD=pw-qa-eu  # trailing comment
export UAT_ADMIN_EMAIL=uat@example

ALOR_TOKEN_ALICE_A=alor-1
TINVEST_TOKEN_BOB_B=tinv-1
BYBIT_API_KEY=bk
BYBIT_API_SECRET=bs
BYBIT_API_KEY=bk-last

TELEGRAM_BOT_TOKEN=tg
TELEGRAM_CHAT_ID=-100
ALLURE_TOKEN=al
LITE_LLM_KEY=llm
JIRA_API_TOKEN=jt

AUTOTEST_TAG=smoke
EMPTY_ONE=
not a pair
"""


class ParseTest(unittest.TestCase):
    def test_parse(self):
        pairs = dict(parse_env(SAMPLE))
        self.assertEqual(pairs["DEV_UI_BASE_URL"], "https://dev-ui.example")  # quotes stripped
        self.assertEqual(pairs["DEV_QA_API_TOKEN"], "tok-dev-qa")
        self.assertEqual(pairs["QA_EU_ADMIN_PASSWORD"], "pw-qa-eu")  # trailing comment cut off
        self.assertEqual(pairs["UAT_ADMIN_EMAIL"], "uat@example")  # export stripped
        self.assertEqual(pairs["BYBIT_API_KEY"], "bk-last")  # repeated key — the last one wins
        self.assertEqual(pairs["EMPTY_ONE"], "")
        self.assertNotIn("not a pair", pairs)

    def test_secret_detection(self):
        for key in ("DEV_QA_API_TOKEN", "QA_EU_ADMIN_PASSWORD", "BYBIT_API_SECRET", "LITE_LLM_KEY", "GITLAB_PAT"):
            self.assertTrue(is_secret(key), key)
        for key in ("DEV_API_BASE_URL", "QA_EU_ADMIN_EMAIL", "TELEGRAM_CHAT_ID", "AUTOTEST_TAG", "ALLURE_ENDPOINT"):
            self.assertFalse(is_secret(key), key)

    def test_classify(self):
        self.assertEqual(classify("DEV_API_BASE_URL"), (GROUP_STANDS, "dev"))
        self.assertEqual(classify("DEV_QA_API_TOKEN"), (GROUP_STANDS, "dev / qa (shared)"))
        self.assertEqual(classify("QA_EU_ADMIN_EMAIL"), (GROUP_STANDS, "qa-eu"))
        self.assertEqual(classify("PROD_EU_UI_BASE_URL"), (GROUP_STANDS, "prod-eu"))
        self.assertEqual(classify("ALOR_TOKEN_ALICE_A"), (GROUP_BROKERS, "Alor — ALICE_A"))
        self.assertEqual(classify("BYBIT_API_SECRET"), (GROUP_BROKERS, "Bybit"))
        self.assertEqual(classify("TELEGRAM_BOT_TOKEN"), (GROUP_INTEGRATIONS, "Telegram"))
        self.assertEqual(classify("LITE_LLM_KEY"), (GROUP_INTEGRATIONS, "LiteLLM"))
        self.assertEqual(classify("JIRA_API_TOKEN"), (GROUP_INTEGRATIONS, "Jira"))
        self.assertEqual(classify("AUTOTEST_TAG"), (GROUP_OTHER, "Autotest"))

    def test_plan_groups_and_skips_empty(self):
        plan = plan_import(parse_env(SAMPLE))
        names = {(i.group, i.name): [f.key for f in i.fields] for i in plan}
        self.assertEqual(names[(GROUP_STANDS, "dev")], ["DEV_API_BASE_URL", "DEV_UI_BASE_URL"])
        self.assertEqual(names[(GROUP_STANDS, "qa-eu")], ["QA_EU_ADMIN_EMAIL", "QA_EU_ADMIN_PASSWORD"])
        self.assertEqual(names[(GROUP_BROKERS, "Bybit")], ["BYBIT_API_KEY", "BYBIT_API_SECRET"])
        self.assertNotIn((GROUP_OTHER, "Empty"), names)  # an empty value is not imported
        text = describe_plan(plan)
        self.assertIn("▌ Stands", text)
        self.assertIn("🔒 DEV_QA_API_TOKEN", text)
        self.assertNotIn("tok-dev-qa", text)  # no values in the preview


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Vault.create(Path(self.tmp.name) / "v.pkv", "master-pass-1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_creates_groups_and_keeps_main(self):
        main = self.vault.add_entry("My entry")
        result = apply_plan(self.vault, plan_import(parse_env(SAMPLE)))
        self.assertEqual(result.groups_added, [GROUP_STANDS, GROUP_BROKERS, GROUP_INTEGRATIONS, GROUP_OTHER])
        self.assertEqual([e.name for e in self.vault.search("")], ["My entry"])  # the main list is untouched
        self.assertEqual(main.fields[0].key, "login")
        stands = {e.name: e for e in self.vault.search("", GROUP_STANDS)}
        self.assertEqual(stands["dev"].fields[0].value, "https://dev.example")
        self.assertFalse(stands["dev"].fields[0].secret)
        token = next(f for f in stands["dev / qa (shared)"].fields if f.key == "DEV_QA_API_TOKEN")
        self.assertTrue(token.secret)
        self.assertEqual(result.entries_added, len(plan_import(parse_env(SAMPLE))))
        self.assertEqual(result.entries_updated, 0)

    def test_apply_twice_merges_instead_of_duplicating(self):
        apply_plan(self.vault, plan_import(parse_env(SAMPLE)))
        count_before = len(self.vault.entries)
        updated = SAMPLE.replace("TELEGRAM_BOT_TOKEN=tg", "TELEGRAM_BOT_TOKEN=tg-new") + "\nTELEGRAM_THREAD_ID=42\n"
        result = apply_plan(self.vault, plan_import(parse_env(updated)))
        self.assertEqual(len(self.vault.entries), count_before)
        self.assertEqual(result.entries_added, 0)
        self.assertEqual(result.entries_updated, 1)
        self.assertEqual(result.fields_added, 1)
        self.assertEqual(result.fields_updated, 1)
        tg = next(e for e in self.vault.entries if e.name == "Telegram")
        self.assertEqual({f.key: f.value for f in tg.fields}["TELEGRAM_BOT_TOKEN"], "tg-new")
        self.assertIn("TELEGRAM_THREAD_ID", [f.key for f in tg.fields])


if __name__ == "__main__":
    unittest.main()
