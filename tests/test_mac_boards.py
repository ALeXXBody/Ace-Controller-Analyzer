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

    def test_verified_strap_maps(self):
        """Straps verified in the boardviews (I2C_ADDR pin 111).
        A2485: UF400 GND, UF500 float, UG400 GND (RG201), U5500 float.
        A2141: primaries (U3100/UB300) GND, secondaries (U3200/UB400) float."""
        from cd3217_analyzer.models import get_model
        a2485 = {p.ref: p.addr_pin for p in get_model("A2485").positions}
        self.assertEqual(a2485, {"UF400": "GND", "UF500": "float",
                                 "UG400": "GND", "U5500": "float"})
        a2141 = {p.ref: p.addr_pin for p in get_model("A2141").positions}
        self.assertEqual(a2141, {"U3100": "GND", "U3200": "float",
                                 "UB300": "GND", "UB400": "float"})

    def test_a2289_verified_map(self):
        """820-01987 (MBP 13" 2020, 2-port): exactly two CD3217B12 —
        U3100=XA@0x38 (GND strap), U3200=XB@0x3F (float), verified from
        schematic I2C table (WRITE 0x70/7E) + boardview pin 111 nets."""
        from cd3217_analyzer.models import get_model
        m = get_model("A2289")
        self.assertEqual(m.board_id, "820-01987")
        self.assertEqual(len(m.positions), 2)
        by_ref = {p.ref: (p.address, p.addr_pin, p.silicon, p.chip_class)
                  for p in m.positions}
        self.assertEqual(by_ref, {
            "U3100": (0x38, "GND", "CD3217B12", "vanilla"),
            "U3200": (0x3F, "float", "CD3217B12", "vanilla"),
        })

    def test_a2251_verified_map(self):
        """820-01949 (MBP 13\" 2020, 4-port): four CD3217B12 (ACE2) —
        U3100_X@0x38 (GND), U3100_T@0x3F (float) vanilla; U3100_W@0x3B and
        U3100_R@0x3C OTP. Verified from 820-01949 (X1795) schematic I2C
        table + boardview (ports W/R on the right via J3300_CWR, T/X on the
        left via J3300_CXT)."""
        from cd3217_analyzer.models import get_model
        m = get_model("A2251")
        self.assertEqual(m.board_id, "820-01949")
        self.assertEqual(m.chip_count, 4)
        self.assertEqual(len(m.positions), 4)
        by_ref = {p.ref: (p.address, p.addr_pin, p.silicon, p.chip_class)
                  for p in m.positions}
        self.assertEqual(by_ref, {
            "U3100_X": (0x38, "GND", "CD3217B12", "vanilla"),
            "U3100_T": (0x3F, "float", "CD3217B12", "vanilla"),
            "U3100_W": (0x3B, "float", "CD3217B12", "otp"),
            "U3100_R": (0x3C, "float", "CD3217B12", "otp"),
        })

    def test_a2337_a2179_use_seven_bit_addresses(self):
        """L1 regression: A2337/A2179 schematics list 0x70/0x7E, which are
        the 8-BIT WRITE forms (0x38<<1, 0x3F<<1). The chips answer at the
        7-bit addresses 0x38/0x3F (same straps as A2338). The model map
        must hold 7-bit addresses or scans miss the real chips."""
        from cd3217_analyzer.models import get_model
        for mid in ("A2337", "A2179"):
            m = get_model(mid)
            by_ref = {p.ref: (p.address, p.addr_pin, p.addressing)
                      for p in m.positions}
            self.assertEqual(by_ref, {
                "UF400": (0x38, "GND", "strap"),
                "UF500": (0x3F, "float", "strap"),
            }, mid)

    def test_connect_mentions_tap_method(self):
        # every board should explain where/how to tap the bus
        for key, b in MAC_BOARDS.items():
            joined = " ".join(b.connect).lower()
            self.assertIn("tap", joined, key)

    def test_check_model_placement_all_ok(self):
        from cd3217_analyzer.models import check_model_placement, get_model
        m = get_model("A2251")
        expected = [p.address for p in m.positions]
        by_addr = check_model_placement(m, expected)
        for p in m.positions:
            self.assertEqual(by_addr[p.address]["verdict"], "OK")
        self.assertEqual(len(by_addr), len(m.positions))

    def test_check_model_placement_missing_and_unexpected(self):
        from cd3217_analyzer.models import check_model_placement, get_model
        m = get_model("A2251")
        # only the two vanilla straps respond; an OTP donor answers off-map
        live = [0x38, 0x3F, 0x7E]
        by_addr = check_model_placement(m, live)
        # responding vanilla sockets OK
        self.assertEqual(by_addr[0x38]["verdict"], "OK")
        self.assertEqual(by_addr[0x3F]["verdict"], "OK")
        # expected OTP sockets empty -> MISSING
        self.assertEqual(by_addr[0x3B]["verdict"], "MISSING")
        self.assertEqual(by_addr[0x3C]["verdict"], "MISSING")
        # 0x7E is not an A2251 address -> UNEXPECTED (mismatched OTP donor)
        got = by_addr[0x7E]
        self.assertEqual(got["verdict"], "UNEXPECTED")
        self.assertIn("wrong OTP donor", got["message"])

    def test_check_model_placement_skips_broadcast(self):
        from cd3217_analyzer.models import check_model_placement, get_model
        m = get_model("A2289")
        by_addr = check_model_placement(m, [0x38, 0x3F, 0x6B])
        self.assertNotIn(0x6B, by_addr)
        self.assertEqual(by_addr[0x38]["verdict"], "OK")
        self.assertEqual(by_addr[0x3F]["verdict"], "OK")

    def test_check_model_placement_unknown_model_has_no_positions(self):
        from cd3217_analyzer.models import check_model_placement
        from cd3217_analyzer.models import MacBookModel
        empty = MacBookModel("X", "Test", "000-0000", 0, [])
        by_addr = check_model_placement(empty, [0x38])
        self.assertEqual(by_addr[0x38]["verdict"], "UNEXPECTED")


if __name__ == "__main__":
    unittest.main()
