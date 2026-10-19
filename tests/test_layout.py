"""Geometry helpers: columns, the card flow, tile columns, window geometry on screen."""
import unittest

from daykeeper.layout import DEFAULT_GEOMETRY, column_width, flow_layout, on_screen, parse_geometry, tile_columns


class ColumnsTest(unittest.TestCase):
    def test_columns_follow_the_space(self):
        self.assertEqual(column_width(1000, gap=10, minimum=300), (3, (1000 - 20) / 3))
        self.assertEqual(column_width(200, gap=10, minimum=300)[0], 1)

    def test_cards_flow_left_to_right_and_wrap(self):
        spots, total = flow_layout([(300, 100), (300, 180), (300, 50)], 700, gap=10)
        self.assertEqual(spots, [(0, 0), (310, 0), (0, 190)], "a row is as tall as its tallest card")
        self.assertEqual(total, 240)
        self.assertEqual(flow_layout([(900, 40)], 700, gap=10), ([(0, 0)], 40), "too wide still starts the row")
        self.assertEqual(flow_layout([], 700), ([], 0))

    def test_pixel_sizes(self):
        spots, _ = flow_layout([(301, 10), (301, 10)], 613, gap=10)
        self.assertEqual(spots[1], (311, 0), "one pixel wider still fits — no jump to columns")

    def test_tile_columns(self):
        self.assertEqual(tile_columns(48 + 270 * 3), 3)
        self.assertEqual(tile_columns(48 + 270 * 3 - 1), 2)
        self.assertEqual(tile_columns(10), 1, "never fewer than one")
        self.assertEqual(tile_columns(1000, minimum=400, margin=0), 2)


class GeometryTest(unittest.TestCase):
    def test_parse_geometry(self):
        self.assertEqual(parse_geometry("1500x900+40+60"), ("1500x900", "40+60"))
        self.assertEqual(parse_geometry("1500x900"), ("1500x900", ""))
        self.assertEqual(parse_geometry("garbage+1+2"), (DEFAULT_GEOMETRY, "1+2"))
        self.assertEqual(parse_geometry(""), (DEFAULT_GEOMETRY, ""))
        self.assertEqual(parse_geometry("1x1+0+0"), (DEFAULT_GEOMETRY, "0+0"), "a size below three digits is not a size")

    def test_on_screen_with_a_known_screen(self):
        screen = (-1920, 0, 3840 + 1920, 1080)  # a second monitor on the left
        self.assertTrue(on_screen("1180x720+100+100", screen))
        self.assertTrue(on_screen("1180x720-1800+10", screen), "on the left monitor")
        self.assertTrue(on_screen("1180x720-40+0", screen), "a corner just off the edge still counts")
        self.assertFalse(on_screen("1180x720+9000+100", screen))
        self.assertFalse(on_screen("1180x720+100+2000", screen))
        self.assertFalse(on_screen("1180x720", screen), "no position")
        self.assertFalse(on_screen("", screen))

    def test_on_screen_without_a_screen(self):
        self.assertTrue(on_screen("1180x720+0+0", None))
        self.assertFalse(on_screen("1180x720-5+0", None))


if __name__ == "__main__":
    unittest.main()
