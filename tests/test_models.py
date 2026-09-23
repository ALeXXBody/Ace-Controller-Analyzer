"""Tests for models.py: model registry consistency + placement logic.

The BUG CLASS this guards: models.py is the verified socket-map source of
truth (downstream: strap refs, batch targets, power-tab rows, placement
guide) — duplicates or addressing mistakes here mislead every repair.
"""

import unittest

from cd3217_analyzer.models import (
    get_model, list_models, model_ids, check_model_placement,
    build_placement_guide,
)


class TestModelRegistry(unittest.TestCase):
    def test_ids_and_models_agree(self):
        self.assertEqual(sorted(model_ids()),
                         sorted(m.model_id for m in list_models()))

    def test_known_model_loads(self):
        m = get_model("A2485")
        self.assertIsNotNone(m)
        self.assertEqual(m.chip_count, len(m.positions))

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(get_model("a2485").model_id, "A2485")

    def test_unknown_model_none(self):
        self.assertIsNone(get_model("NOPE"))
        self.assertIsNone(get_model(""))

    def test_addresses_unique_within_a_model(self):
        for m in list_models():
            addrs = [p.address for p in m.positions]
            dups = [a for a in set(addrs) if addrs.count(a) > 1]
            self.assertEqual(
                dups, [],
                f"{m.model_id} has duplicate addresses: {dups}")

    def test_no_overlap_between_strap_and_otp_sockets(self):
        """An OTP socket must never share an address a strap socket uses —
        over I2C they would collide on the same bus."""
        for m in list_models():
            strap = {p.address for p in m.positions
                     if p.addressing == "strap"}
            otp = {p.address for p in m.positions
                   if p.addressing == "otp"}
            self.assertFalse(
                strap & otp,
                f"{m.model_id}: strap/otp address collision")

    def test_a2141_known_map(self):
        m = get_model("A2141")
        self.assertIsNotNone(m)
        by_addr = {p.address: p for p in m.positions}
        self.assertEqual(sorted(by_addr), [0x38, 0x3B, 0x3C, 0x3F])
        # §3.16 field-verified addressing classes
        self.assertEqual({p.addressing for p in m.positions} <=
                         {"strap", "otp"}, True)
        stats = {}
        for p in m.positions:
            stats.setdefault(p.addressing, []).append(hex(p.address))
        self.assertIn("strap", stats)


class TestPlacement(unittest.TestCase):
    def test_placement_flags_unexpected_address(self):
        m = get_model("A2485")
        guide = build_placement_guide(m, [0x38])
        self.assertTrue(guide)
        guidance = "\n".join(guide)
        self.assertIn("UF400", guidance)   # 0x38 maps to UF400 on A2485

    def test_placement_skips_all_call(self):
        m = get_model("A2485")
        guide = build_placement_guide(m, [0x6B])
        self.assertNotIn("0x6B", "\n".join(guide))


if __name__ == "__main__":
    unittest.main()
