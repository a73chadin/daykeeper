import unittest

from daykeeper.tabs import TAB_IDS, moved, tab_order, tabs_hidden, visible_tabs


class TabsTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(tab_order({}), list(TAB_IDS))
        self.assertEqual(tabs_hidden({}), set())
        self.assertEqual(visible_tabs({}), list(TAB_IDS))

    def test_saved_order_is_cleaned_and_completed(self):
        order = tab_order({"tab_order": ["notes", "nope", "notes", "vault", 7]})
        self.assertEqual(order[:2], ["notes", "vault"])
        self.assertEqual(sorted(order), sorted(TAB_IDS))

    def test_hidden_tabs_and_the_last_one_standing(self):
        s = {"tab_order": ["notes", "vault"], "tabs_hidden": ["vault", "journal", "x"]}
        self.assertEqual(tabs_hidden(s), {"vault", "journal"})
        self.assertNotIn("vault", visible_tabs(s))
        everything = {"tab_order": ["routines"], "tabs_hidden": list(TAB_IDS)}
        self.assertEqual(visible_tabs(everything), ["routines"])

    def test_moved(self):
        self.assertEqual(moved(["a", "b", "c"], "c", -1), ["a", "c", "b"])
        self.assertEqual(moved(["a", "b", "c"], "a", -1), ["a", "b", "c"])
        self.assertEqual(moved(["a", "b", "c"], "a", 5), ["b", "c", "a"])


if __name__ == "__main__":
    unittest.main()
