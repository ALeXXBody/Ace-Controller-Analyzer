"""Tests for the MacBook logic-board reference (Board tab)."""

import unittest

from cd3217_analyzer.boards import (
    MAC_BOARDS,
    mac_from_model_key,
    mac_from_model_name,
)


class TestMacBoards(unittest.TestCase):
    def test_known_models_present(self):
        for key in ("a2141", "a2337", "a2338", "a2442", "a2485", "a1989",
                    "a1708", "a2251"):
            self.assertIn(key, MAC_BOARDS, key)

    def test_lookup_by_key_case_insensitive(self):
        self.assertEqual(mac_from_model_key("A2141").model,
                         mac_from_model_key("a2141").model)
        self.assertIsNone(mac_from_model_key("nope"))

    def test_lookup_by_name(self):
        b = mac_from_model_name('MacBook Pro 16" 2019')
        self.assertIsNotNone(b)
        self.assertIn("820-01700", b.board_nos)
        self.assertEqual(b.ports, 4)

    def test_all_entries_have_required_fields(self):
        for key, b in MAC_BOARDS.items():
            self.assertTrue(b.model, key)
            self.assertTrue(b.board_nos, key)
            self.assertTrue(b.ports >= 2, key)
            self.assertTrue(b.ace, key)
            self.assertTrue(b.bus, key)
            self.assertTrue(b.connect, key)
            self.assertTrue(b.notes, key)

    def test_ace2_models_use_cd3217(self):
        # Sorted into specific 2019 T2 boards, these are all CD3217 (ACE2);
        # the Apple-Silicon and Intel T2 16" (A2141) all use CD3217.
        for key in ("a2141", "a2337", "a2338", "a2442", "a2485"):
            self.assertIn("CD3217", MAC_BOARDS[key].ace, key)

    def test_a2141_uses_cd3217_ace2(self):
        self.assertIn("CD3217", MAC_BOARDS["a2141"].ace)

    def test_a2141_verified_address_map(self):
        from cd3217_analyzer.models import get_model
        m = get_model("A2141")
        self.assertIsNotNone(m)
        self.assertEqual(m.board_id, "820-01700")
        self.assertEqual(len(m.positions), 4)
        by_ref = {p.ref: p.address for p in m.positions}
        self.assertEqual(by_ref, {"U3100": 0x38, "U3200": 0x3F,
                                  "UB300": 0x3B, "UB400": 0x3C})

    def test_connect_mentions_tap_method(self):
        # every board should explain where/how to tap the bus
        for key, b in MAC_BOARDS.items():
            joined = " ".join(b.connect).lower()
            self.assertIn("tap", joined, key)


if __name__ == "__main__":
    unittest.main()
