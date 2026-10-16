"""SidebarKeeper keeps the first pane visible through window resizes and sash drags."""
import tkinter as tk
import unittest
from tkinter import ttk

from daykeeper.sidebar import CARD_MIN, SidebarKeeper


def _root() -> tk.Tk | None:
    try:
        root = tk.Tk()
    except tk.TclError:  # no display
        return None
    root.withdraw()
    return root


class SidebarKeeperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _root()
        if self.root is None:
            self.skipTest("no display")
        self.root.deiconify()
        self.paned = ttk.PanedWindow(self.root, orient="horizontal")
        self.paned.pack(fill="both", expand=True)
        self.left = ttk.Frame(self.paned, width=200, height=100)
        self.right = ttk.Frame(self.paned, width=200, height=100)
        self.paned.add(self.left, weight=0)
        self.paned.add(self.right, weight=1)

    def tearDown(self) -> None:
        if self.root is not None:
            self.root.destroy()

    def _resize(self, width: int) -> None:
        self.root.geometry(f"{width}x300")
        self.root.update()

    def test_sidebar_survives_shrinking_from_a_wide_window(self) -> None:
        self._resize(3000)
        SidebarKeeper(self.paned, minimum=300, width=400)
        self.root.update()
        self.assertEqual(self.paned.sashpos(0), 400)
        self._resize(1000)
        self.assertEqual(self.paned.sashpos(0), 400)
        self._resize(3000)
        self.assertEqual(self.paned.sashpos(0), 400)

    def test_sidebar_is_placed_once_the_window_is_laid_out(self) -> None:
        SidebarKeeper(self.paned, minimum=300, width=400)
        self._resize(1200)
        self.assertEqual(self.paned.sashpos(0), 400)

    def test_narrow_window_keeps_both_panes(self) -> None:
        self._resize(1200)
        keeper = SidebarKeeper(self.paned, minimum=300, width=500)
        self._resize(600)
        self.assertEqual(self.paned.sashpos(0), 300)
        self.assertEqual(keeper.target(900), 500)
        self.assertEqual(keeper.target(700), 700 - CARD_MIN)

    def test_drag_below_minimum_snaps_back(self) -> None:
        self._resize(1200)
        keeper = SidebarKeeper(self.paned, minimum=300, width=400)
        keeper._dragging = True
        self.paned.sashpos(0, 0)
        self.root.update()
        keeper._release(None)
        self.root.update()
        self.assertEqual(self.paned.sashpos(0), 300)
        self.assertEqual(keeper.width, 300)

    def test_drag_remembers_the_new_width(self) -> None:
        self._resize(1200)
        keeper = SidebarKeeper(self.paned, minimum=300, width=400)
        keeper._dragging = True
        self.paned.sashpos(0, 550)
        self.root.update()
        keeper._release(None)
        self._resize(2000)
        self.assertEqual(self.paned.sashpos(0), 550)


if __name__ == "__main__":
    unittest.main()
