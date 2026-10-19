"""Per-domain change counters behind the tab view keys."""
import unittest

from daykeeper.revisions import DOMAINS, VIEW_DOMAINS, Revisions


class RevisionsTest(unittest.TestCase):
    def test_touch_moves_only_its_own_domain(self):
        rev = Revisions()
        before = rev.of(*DOMAINS)
        rev.touch("vault")
        rev.touch("vault")
        rev.touch("notes")
        self.assertEqual(before, (0,) * len(DOMAINS))
        self.assertEqual(rev.of("vault", "notes", "bookmarks"), (2, 1, 0))
        self.assertEqual(rev.snapshot()["journal"], 0)

    def test_key_keeps_the_order_asked_for(self):
        rev = Revisions(("a", "b"))
        rev.touch("b")
        self.assertEqual(rev.of("b", "a"), (1, 0))
        self.assertEqual(rev.of(), ())

    def test_unknown_domain_is_an_error(self):
        rev = Revisions()
        with self.assertRaises(KeyError):
            rev.touch("vualt")
        with self.assertRaises(KeyError):
            rev.of("vault", "cards")

    def test_every_tab_draws_from_known_domains(self):
        for mode, domains in VIEW_DOMAINS.items():
            self.assertTrue(domains, mode)
            for d in domains:
                self.assertIn(d, DOMAINS, (mode, d))
        self.assertNotIn("vault", VIEW_DOMAINS)  # the Passwords tab keeps itself up to date


if __name__ == "__main__":
    unittest.main()
