"""SecretClipboard on fake backends: the Windows way when it is there, the window's own
clipboard otherwise, and a wipe that only wipes its own text."""
import unittest

from daykeeper.clipboard import SecretClipboard


class FakeWindowClipboard:
    def __init__(self):
        self.text = None
        self.puts = 0

    def put(self, text):
        self.text = text
        self.puts += 1

    def get(self):
        return self.text

    def clear(self):
        self.text = None


class SecretClipboardTest(unittest.TestCase):
    def test_falls_back_to_the_window_and_wipes_only_its_own_text(self):
        win = FakeWindowClipboard()
        clip = SecretClipboard(win.put, win.get, win.clear, put=lambda text: False, clear_if=lambda expected: None)
        self.assertFalse(clip.put("s3cret"), "put plainly")
        self.assertEqual(win.text, "s3cret")
        win.text = "something else the user copied"
        self.assertFalse(clip.wipe_if("s3cret"))
        self.assertEqual(win.text, "something else the user copied")
        win.text = "s3cret"
        self.assertTrue(clip.wipe_if("s3cret"))
        self.assertIsNone(win.text)

    def test_the_windows_way_when_available(self):
        win = FakeWindowClipboard()
        store = {}

        def put(text):
            store["text"] = text
            return True

        def clear_if(expected):
            if store.get("text") != expected:
                return False
            store.pop("text")
            return True

        clip = SecretClipboard(win.put, win.get, win.clear, put=put, clear_if=clear_if)
        self.assertTrue(clip.put("tok"), "hidden from history")
        self.assertEqual(win.puts, 0, "the window's clipboard was not used")
        self.assertFalse(clip.wipe_if("other"))
        self.assertEqual(store, {"text": "tok"})
        self.assertTrue(clip.wipe_if("tok"))
        self.assertEqual(store, {})


if __name__ == "__main__":
    unittest.main()
