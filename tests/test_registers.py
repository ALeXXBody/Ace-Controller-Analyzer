"""Tests for the REGISTERS map integrity (duplicate-key regression, §4.15).

0x30/0x35/0x36 were each defined TWICE in REGISTERS; python's later-wins
silently kept only the second definition, so two names/semantics mixes
shipped (e.g. 0x36 showed name SinkRequestRDO while analyzer decodes it
as the live contract RDO per §4.9). These tests lock ONE definition per
offset and the canonical semantics.
"""

import ast
import unittest
from pathlib import Path

from cd3217_analyzer.registers import (
    REGISTERS,
    decode_rdo,
    decode_pdo,
)
from cd3217_analyzer import registers as registers_module


class TestRegistersIntegrity(unittest.TestCase):
    def _dict_offset_keys(self):
        """Ast-parse the module and collect every literal dict key in the
        REGISTERS assignment — duplicate keys don't survive into runtime
        dicts, so the source must be checked."""
        src = (Path(registers_module.__file__)).read_text()
        tree = ast.parse(src)
        reg_node = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "REGISTERS"
                for t in node.targets
            ):
                reg_node = node.value
        self.assertIsNotNone(reg_node, "REGISTERS assignment not found")
        keys = [ast.literal_eval(k) for k in reg_node.keys]
        return keys

    def test_no_duplicate_offsets(self):
        keys = self._dict_offset_keys()
        dups = {k for k in keys if keys.count(k) > 1}
        self.assertEqual(dups, set(),
                         f"duplicate REGISTERS keys: {sorted(dups)}")

    def test_canonical_semantics_survive(self):
        """The single definitions match field-verified behavior:
        0x36 = the live contract RDO (decode_rdo at analyzer), 0x35 =
        the contract PDO (decode_pdo), 0x30 = received source caps."""
        self.assertEqual(REGISTERS[0x36].name, "ActiveRDO")
        self.assertEqual(REGISTERS[0x36].length, 4)
        self.assertEqual(REGISTERS[0x35].name, "ActivePDO")
        self.assertEqual(REGISTERS[0x35].length, 4)
        self.assertEqual(REGISTERS[0x30].name, "RxCapabilities")
        self.assertEqual(REGISTERS[0x30].length, 29)

    def test_decode_dispatch_consistent_with_defs(self):
        """decode_rdo exists for the contract register and decode_pdo for
        the contract PDO — the §4.9 phantom-contract guard runs through
        decode_rdo(0x36) which must produce the 'not a contract' string."""
        out = decode_rdo(0xFFFFFF04)
        self.assertIn("CORRUPT RDO", out)
        self.assertIn("not a contract", out)
        # decode_pdo is exercised for 0x35 — a valid fixed PDO decodes.
        fixed = (200 << 10) | 100   # fixed supply (B31:30=00), 20.0V/1.0A
        self.assertIn("20.0V", decode_pdo(fixed))


if __name__ == "__main__":
    unittest.main()
