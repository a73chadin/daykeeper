import tempfile
import unittest
from pathlib import Path

from daykeeper.envimport import (
    GROUP_ENVIRONMENTS,
    GROUP_INTEGRATIONS,
    GROUP_OTHER,
    apply_plan,
    classify,
    describe_plan,
    is_secret,
    own_rules,
    parse_env,
    plan_import,
)
from daykeeper.vault import Vault

SAMPLE = """
# environments
DEV_API_BASE_URL=https://dev.example
DEV_UI_BASE_URL="https://dev-ui.example"
DEV_API_TOKEN='tok-dev'
PROD_EU_ADMIN_EMAIL=admin@example
PROD_EU_ADMIN_PASSWORD=pw-prod-eu  # trailing comment
export STAGE_ADMIN_EMAIL=stage@example

ACME_TOKEN_ALICE=acme-1
STRIPE_API_KEY=sk
STRIPE_API_SECRET=ss
STRIPE_API_KEY=sk-last

TELEGRAM_BOT_TOKEN=tg
TELEGRAM_CHAT_ID=-100
OPENAI_KEY=oa
JIRA_API_TOKEN=jt

BUILD_TAG=nightly
EMPTY_ONE=
not a pair
"""


class ParseTest(unittest.TestCase):
    def test_parse(self):
        pairs = dict(parse_env(SAMPLE))
        self.assertEqual(pairs["DEV_UI_BASE_URL"], "https://dev-ui.example")  # quotes stripped
        self.assertEqual(pairs["DEV_API_TOKEN"], "tok-dev")
        self.assertEqual(pairs["PROD_EU_ADMIN_PASSWORD"], "pw-prod-eu")  # trailing comment cut off
        self.assertEqual(pairs["STAGE_ADMIN_EMAIL"], "stage@example")  # export stripped
        self.assertEqual(pairs["STRIPE_API_KEY"], "sk-last")  # repeated key — the last one wins
        self.assertEqual(pairs["EMPTY_ONE"], "")
        self.assertNotIn("not a pair", pairs)

    def test_secret_detection(self):
        for key in ("DEV_API_TOKEN", "PROD_EU_ADMIN_PASSWORD", "STRIPE_API_SECRET", "OPENAI_KEY", "GITLAB_PAT"):
            self.assertTrue(is_secret(key), key)
        for key in ("DEV_API_BASE_URL", "PROD_EU_ADMIN_EMAIL", "TELEGRAM_CHAT_ID", "BUILD_TAG", "SENTRY_ENDPOINT"):
            self.assertFalse(is_secret(key), key)

    def test_classify(self):
        self.assertEqual(classify("DEV_API_BASE_URL"), (GROUP_ENVIRONMENTS, "dev"))
        self.assertEqual(classify("PROD_EU_UI_BASE_URL"), (GROUP_ENVIRONMENTS, "prod-eu"))
        self.assertEqual(classify("STAGING_DB_HOST"), (GROUP_ENVIRONMENTS, "staging"))
        self.assertEqual(classify("TELEGRAM_BOT_TOKEN"), (GROUP_INTEGRATIONS, "Telegram"))
        self.assertEqual(classify("STRIPE_API_SECRET"), (GROUP_INTEGRATIONS, "Stripe"))
        self.assertEqual(classify("JIRA_API_TOKEN"), (GROUP_INTEGRATIONS, "Jira"))
        self.assertEqual(classify("ACME_TOKEN_ALICE"), (GROUP_OTHER, "Acme"))
        self.assertEqual(classify("BUILD_TAG"), (GROUP_OTHER, "Build"))

    def test_own_rules_go_first(self):
        rules = own_rules({"env_import": [{"prefix": "ACME_", "list": "Partners", "entry": "Acme"}, {"prefix": " "}, "junk"]})
        self.assertEqual(classify("ACME_TOKEN_ALICE", rules), ("Partners", "Acme"))
        self.assertEqual(classify("DEV_API_TOKEN", rules), (GROUP_ENVIRONMENTS, "dev"))

    def test_plan_groups_and_skips_empty(self):
        plan = plan_import(parse_env(SAMPLE))
        names = {(i.group, i.name): [f.key for f in i.fields] for i in plan}
        self.assertEqual(names[(GROUP_ENVIRONMENTS, "dev")], ["DEV_API_BASE_URL", "DEV_UI_BASE_URL", "DEV_API_TOKEN"])
        self.assertEqual(names[(GROUP_ENVIRONMENTS, "prod-eu")], ["PROD_EU_ADMIN_EMAIL", "PROD_EU_ADMIN_PASSWORD"])
        self.assertEqual(names[(GROUP_INTEGRATIONS, "Stripe")], ["STRIPE_API_KEY", "STRIPE_API_SECRET"])
        self.assertNotIn((GROUP_OTHER, "Empty"), names)  # an empty value is not imported
        text = describe_plan(plan)
        self.assertIn("▌ Environments", text)
        self.assertIn("🔒 DEV_API_TOKEN", text)
        self.assertNotIn("tok-dev", text)  # no values in the preview


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Vault.create(Path(self.tmp.name) / "v.pkv", "master-pass-1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_creates_groups_and_keeps_main(self):
        main = self.vault.add_entry("My entry")
        result = apply_plan(self.vault, plan_import(parse_env(SAMPLE)))
        self.assertEqual(result.groups_added, [GROUP_ENVIRONMENTS, GROUP_OTHER, GROUP_INTEGRATIONS])
        self.assertEqual([e.name for e in self.vault.search("")], ["My entry"])  # the main list is untouched
        self.assertEqual(main.fields[0].key, "login")
        envs = {e.name: e for e in self.vault.search("", GROUP_ENVIRONMENTS)}
        self.assertEqual(envs["dev"].fields[0].value, "https://dev.example")
        self.assertFalse(envs["dev"].fields[0].secret)
        token = next(f for f in envs["dev"].fields if f.key == "DEV_API_TOKEN")
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
