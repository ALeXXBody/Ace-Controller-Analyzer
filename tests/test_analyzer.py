"""Tests for CD3217B12 analyzer modules."""

import unittest
from unittest.mock import MagicMock, patch

from cd3217_analyzer.registers import (
    KNOWN_ACE2_ADDRESSES,
    REGISTERS,
    decode_i2c_address_straps,
    decode_mode_reg,
    decode_vid,
    is_ace2_address,
    PortMode,
)
from cd3217_analyzer.analyzer import CD3217Analyzer, HealthStatus, FaultType
from cd3217_analyzer.report import format_compact_result


class TestRegisters(unittest.TestCase):
    """Test register definitions and decoding functions."""

    def test_known_ace2_addresses(self):
        """Test that known ACE2 addresses are correctly identified."""
        self.assertTrue(is_ace2_address(0x38))
        self.assertTrue(is_ace2_address(0x3F))
        self.assertTrue(is_ace2_address(0x3B))
        self.assertTrue(is_ace2_address(0x6B))
        self.assertTrue(is_ace2_address(0x50))  # A2141 map
        self.assertFalse(is_ace2_address(0x00))

    def test_decode_mode_reg_app(self):
        """Test Mode register decoding for APP mode.

        4CC characters arrive LSB-first on the wire (verified from real
        board captures): wire bytes [0x04,'A','P','P'] = value 0x50504104.
        """
        result = decode_mode_reg(0x50504104)
        self.assertEqual(result, "APP")

    def test_decode_mode_reg_boot(self):
        """Test Mode register decoding for BOOT mode (wire-order bytes)."""
        result = decode_mode_reg(0x544F4F42)  # wire 'B','O','O','T'
        self.assertEqual(result, "BOOT")

    def test_decode_mode_reg_ptch(self):
        """Test Mode register decoding for PTCH mode (wire-order bytes)."""
        result = decode_mode_reg(0x48435450)  # wire 'P','T','C','H'
        self.assertEqual(result, "PTCH")

    def test_decode_mode_reg_zero(self):
        """Test Mode register decoding for zero value."""
        result = decode_mode_reg(0)
        self.assertEqual(result, "Unknown/Zero")

    def test_decode_vid_ti(self):
        """Test Vendor ID decoding for TI."""
        result = decode_vid(0x0451)
        self.assertIn("Texas Instruments", result)
        self.assertIn("0x0451", result)

    def test_decode_vid_unknown(self):
        """Test Vendor ID decoding for unknown vendor."""
        result = decode_vid(0x1234)
        self.assertIn("0x1234", result)
        self.assertIn("Unknown", result)

    def test_decode_vid_masks_high_ff_bytes(self):
        """A 32-bit read that returns high bytes as 0xFF must still decode
        the 16-bit VID (e.g. 0xFF002804 -> Apple 0x2804)."""
        result = decode_vid(0xFF002804)
        self.assertIn("0x2804", result)
        self.assertIn("Apple", result)

    def test_strap_decode(self):
        """Test I2C address strap configuration decoding."""
        # Port1=0x38 (111000b), Port2=0x38 (111000b)
        info = decode_i2c_address_straps(0x38, 0x38)
        self.assertEqual(info["port1_addr"], "0x38")
        self.assertEqual(info["port2_addr"], "0x38")
        self.assertEqual(info["addr_bits"], "000")
        self.assertEqual(info["addr_resistor"], "0Ω (GND)")
        self.assertEqual(info["cntl1"], 0)
        self.assertEqual(info["cntl1_source"], "GND")
        self.assertEqual(info["cntl2"], 1)
        self.assertEqual(info["cntl2_source"], "LDO_3V3")

    def test_strap_decode_floating(self):
        """Test strap decoding with floating ADDR."""
        # Port1=0x3F (111111b) -> ADDR=111 (floating)
        info = decode_i2c_address_straps(0x3F, 0x2F)
        self.assertEqual(info["addr_bits"], "111")
        self.assertIn("floating", info["addr_resistor"])

    def test_all_registers_have_required_fields(self):
        """Test that all register definitions have required fields."""
        for offset, reg in REGISTERS.items():
            self.assertEqual(reg.offset, offset)
            self.assertGreater(reg.length, 0)
            self.assertIsInstance(reg.name, str)
            self.assertIsInstance(reg.description, str)


class TestAnalyzer(unittest.TestCase):
    """Test CD3217Analyzer with mock adapter."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_adapter = MagicMock()
        self.mock_adapter.ping.return_value = True
        self.mock_adapter.read_bytes.return_value = bytes([0x51, 0x04, 0x00, 0x00])  # TI VID
        self.analyzer = CD3217Analyzer(self.mock_adapter, addresses=[0x38])

    def test_quick_scan_found(self):
        """Test quick scan finds device."""
        self.mock_adapter.ping.return_value = True
        found = self.analyzer.quick_scan()
        self.assertIn(0x38, found)

    def test_quick_scan_not_found(self):
        """Test quick scan finds no devices."""
        self.mock_adapter.ping.return_value = False
        found = self.analyzer.quick_scan()
        self.assertEqual(len(found), 0)

    def test_diagnose_no_response(self):
        """Test diagnosis when device doesn't respond."""
        self.mock_adapter.ping.return_value = False
        result = self.analyzer.diagnose_device(0x38)
        self.assertFalse(result.responds)
        self.assertEqual(result.health, HealthStatus.FAIL)
        self.assertIn(FaultType.NO_RESPONSE, result.faults)

    def test_diagnose_responds(self):
        """Test diagnosis when device responds."""
        # Mock successful register reads
        def mock_read_bytes(addr, reg, length):
            if reg == 0x00:  # VID
                return bytes([0x51, 0x04, 0x00, 0x00])
            elif reg == 0x03:  # Mode
                return bytes([0x20, 0x50, 0x50, 0x41])  # "APP "
            elif reg == 0x04:  # Type
                return bytes([0x20, 0x43, 0x32, 0x49])  # "I2C "
            return bytes([0x00] * length)

        self.mock_adapter.read_bytes.side_effect = mock_read_bytes
        self.mock_adapter.ping.return_value = True

        result = self.analyzer.diagnose_device(0x38)
        self.assertTrue(result.responds)
        self.assertIn(result.health, (HealthStatus.PASS, HealthStatus.WARN))

    def test_diagnose_apple_ace2_vid_and_ppa_mode(self):
        """Apple ACE2 parts (VID 0x2804) in PPA power-path mode must NOT fault."""
        def mock_read_bytes(addr, reg, length):
            if reg == 0x00:  # VID -> 0x2804 (Apple ACE2)
                return bytes([0x04, 0x28, 0x00, 0x00])
            elif reg == 0x03:  # Mode -> "PPA "
                return bytes([0x20, 0x41, 0x50, 0x50])
            elif reg == 0x04:  # Type -> "I2C "
                return bytes([0x20, 0x43, 0x32, 0x49])
            elif reg == 0x2F:  # DeviceInfo string (non-zero, like a live chip)
                return b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"[:length] \
                    or b"\x00" * length
            # Remaining DETAIL_REGS come back non-zero (as on a live chip).
            return bytes([0x5A, 0xA5, 0x00, 0x01])[:length]

        self.mock_adapter.read_bytes.side_effect = mock_read_bytes
        self.mock_adapter.ping.return_value = True
        result = self.analyzer.diagnose_device(0x3A)
        self.assertNotIn(FaultType.WRONG_VID, result.faults)
        self.assertNotIn(FaultType.WRONG_MODE, result.faults)
        self.assertNotIn(FaultType.CORRUPTED_REGISTERS, result.faults)
        self.assertIn(result.health, (HealthStatus.PASS, HealthStatus.WARN))

    def test_diagnose_apple_vid_with_ff_padded_high_bytes(self):
        """A real ACE2 returns the 16-bit VID with high bytes as 0xFF (e.g.
        0xFF002804). This must NOT produce a WRONG_VID fault."""
        def mock_read_bytes(addr, reg, length):
            if reg == 0x00:  # VID -> 0xFF002804 (Apple ACE2, high byte 0xFF)
                return bytes([0x04, 0x28, 0x00, 0xFF])
            elif reg == 0x03:  # Mode -> "PPA "
                return bytes([0x20, 0x41, 0x50, 0x50])
            elif reg == 0x04:  # Type -> "I2C "
                return bytes([0x20, 0x43, 0x32, 0x49])
            elif reg == 0x2F:
                return b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"[:length] \
                    or b"\x00" * length
            return bytes([0x5A, 0xA5, 0x00, 0x01])[:length]

        self.mock_adapter.read_bytes.side_effect = mock_read_bytes
        self.mock_adapter.ping.return_value = True
        result = self.analyzer.diagnose_device(0x3A)
        self.assertNotIn(FaultType.WRONG_VID, result.faults)

    def test_diagnose_retries_contaminated_mode_read(self):
        """Reproduces 'diagnose-all faults but single diag passes': the first
        reads after a NACKed dead address come back 0xFF-contaminated; the
        analyzer must re-read and use the clean value."""
        calls = {}

        def mock_read_bytes(addr, reg, length):
            calls[reg] = calls.get(reg, 0) + 1
            first = calls[reg] == 1
            if reg == 0x00:  # VID: first read contaminated, retry clean
                if first:
                    return bytes([0x04, 0x28, 0x00, 0xFF])   # 0xFF002804
                return bytes([0x04, 0x28, 0x00, 0x00])       # 0x00002804
            if reg == 0x03:  # Mode: first read contaminated, retry clean
                if first:
                    return bytes([0x50, 0x50, 0x41, 0xFF])   # "PP" + 0xFF junk
                return bytes([0x20, 0x41, 0x50, 0x50])       # "PPA "
            if reg == 0x04:
                return bytes([0x20, 0x43, 0x32, 0x49])       # "I2C "
            if reg == 0x2F:
                return b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"[:length] \
                    or b"\x00" * length
            return bytes([0x5A, 0xA5, 0x00, 0x01])[:length]

        self.mock_adapter.read_bytes.side_effect = mock_read_bytes
        self.mock_adapter.ping.return_value = True
        result = self.analyzer.diagnose_device(0x3F)
        self.assertNotIn(FaultType.WRONG_VID, result.faults)
        self.assertNotIn(FaultType.WRONG_MODE, result.faults)
        self.assertIn("APP", (result.mode or "").upper())
        self.assertGreaterEqual(calls.get(0x00, 0), 2)  # VID was retried
        self.assertGreaterEqual(calls.get(0x03, 0), 2)  # Mode was retried

    def test_decode_mode_reg_skips_undriven_bytes(self):
        """4CC decoding must skip non-printable bytes (0x00/0xFF undriven),
        not render them as '0xFF' text that breaks mode matching."""
        # wire ['P','P',0xFF,'A'] -> value 0x41FF5050 -> "PPA"
        self.assertEqual(decode_mode_reg(0x41FF5050), "PPA")
        # wire [0xFF,'P','A',0x00] -> value 0x004150FF -> "PA"
        self.assertEqual(decode_mode_reg(0x004150FF), "PA")

    def test_decode_4cc_wire_order_from_real_capture(self):
        """Real captures (samples/820-02382.json): wire bytes are
        [0x04,'A','P','P'] -> 'APP' and [0x04,'I','2','C'] -> 'I2C'.
        The decoder must read 4CC characters LSB-first (wire order)."""
        self.assertEqual(decode_mode_reg(0x50504104), "APP")
        self.assertEqual(decode_mode_reg(0x43324904), "I2C")

    def test_decode_mode_reg_completes_truncated_boot(self):
        """The Mode register returns [len][first 3 chars], so the 4-char
        'BOOT' mode truncates to 'BOO' (seen on a live 820-01700). The
        decoder must complete it — a truncated BOOT was previously shown
        as 'Unexpected mode: BOO' (WRONG_MODE) instead of BOOT_FAILED."""
        self.assertEqual(decode_mode_reg(0x4F4F4204), "BOOT")  # [4,'B','O','O']
        self.assertEqual(decode_mode_reg(0x48435450), "PTCH")  # [?, 'P','T','C']

    def test_diagnose_truncated_boot_raises_boot_failed(self):
        """A chip reporting truncated 'BOO' must be diagnosed BOOT_FAILED
        (real fault), not WRONG_MODE."""
        def mock_read_bytes(addr, reg, length):
            if reg == 0x00:
                return bytes([0x04, 0x28, 0x00, 0x00])       # Apple VID
            if reg == 0x03:
                return bytes([0x04, 0x42, 0x4F, 0x4F])       # [4,'B','O','O']
            if reg == 0x04:
                return bytes([0x20, 0x49, 0x32, 0x43])       # "I2C"
            return bytes([0x5A, 0xA5, 0x00, 0x01])[:length]

        self.mock_adapter.read_bytes.side_effect = mock_read_bytes
        self.mock_adapter.ping.return_value = True
        result = self.analyzer.diagnose_device(0x3C)
        self.assertIn(FaultType.BOOT_FAILED, result.faults)
        self.assertNotIn(FaultType.WRONG_MODE, result.faults)
        self.assertEqual((result.mode or "").upper(), "BOOT")

    def test_scan_bus_excludes_broadcast_address(self):
        """The ACE2 all-call address (0x6B) must not be reported as a device."""
        self.mock_adapter.scan.return_value = [0x38, 0x3B, 0x3F, 0x6B]
        found = self.analyzer.scan_bus()
        self.assertNotIn(0x6B, found)
        self.assertIn(0x38, found)
        self.assertEqual(len(found), 3)

    def test_diagnose_ping_retry_after_flaky_nack(self):
        """Reproduces '0x3B NO_RESPONSE in diagnose-all but single diag
        passes': the first ping right after a dead address NACK fails on a
        healthy chip; the analyzer must retry before declaring NO_RESPONSE."""
        def mock_read_bytes(addr, reg, length):
            if reg == 0x00:
                return bytes([0x04, 0x28, 0x00, 0x00])
            if reg == 0x03:
                return bytes([0x20, 0x41, 0x50, 0x50])  # "PPA "
            if reg == 0x04:
                return bytes([0x20, 0x43, 0x32, 0x49])  # "I2C "
            return bytes([0x5A, 0xA5, 0x00, 0x01])[:length]

        self.mock_adapter.read_bytes.side_effect = mock_read_bytes
        self.mock_adapter.ping.side_effect = [False, False, True]  # 2 NACKs, then OK
        result = self.analyzer.diagnose_device(0x3B)
        self.assertTrue(result.responds)
        self.assertNotIn(FaultType.NO_RESPONSE, result.faults)

    def test_diagnose_garbled_mode_is_not_wrong_mode(self):
        """A mode register that still reads garbage after retries (e.g.
        '0x04' from byte-shifted bus noise) must NOT raise WRONG_MODE —
        it is an I2C symptom, not a chip fault."""
        def mock_read_bytes(addr, reg, length):
            if reg == 0x00:
                return bytes([0x04, 0x28, 0x00, 0x00])
            if reg == 0x03:
                return bytes([0x04, 0x00, 0x00, 0x00])  # garbage every retry
            if reg == 0x04:
                return bytes([0x04, 0x49, 0x00, 0x00])  # "I0x04" garbage
            return bytes([0x5A, 0xA5, 0x00, 0x01])[:length]

        self.mock_adapter.read_bytes.side_effect = mock_read_bytes
        self.mock_adapter.ping.return_value = True
        result = self.analyzer.diagnose_device(0x3F)
        self.assertNotIn(FaultType.WRONG_MODE, result.faults)
        self.assertTrue(any("unreadable" in d.lower()
                            for d in result.fault_details))

    def _diagnose_with_regs(self, addr, vid_bytes, did_bytes):
        def mock_read_bytes(a, reg, length):
            if reg == 0x00:
                return vid_bytes
            if reg == 0x01:
                return did_bytes
            if reg == 0x03:
                return bytes([0x20, 0x41, 0x50, 0x50])  # "PPA"
            if reg == 0x04:
                return bytes([0x20, 0x49, 0x32, 0x43])  # "I2C"
            if reg == 0x2F:
                return b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"[:length] \
                    or b"\x00" * length
            return bytes([0x5A, 0xA5, 0x00, 0x01])[:length]

        self.mock_adapter.read_bytes.side_effect = mock_read_bytes
        self.mock_adapter.ping.return_value = True
        return self.analyzer.diagnose_device(addr)

    def test_chip_mismatch_vanilla_in_otp_socket(self):
        """Vanilla TI chip (VID 0x0451) in an OTP socket (UG400@0x3B on
        A2485) must be flagged CHIP_MISMATCH."""
        from cd3217_analyzer.models import get_model
        pos = {p.address: p for p in get_model("A2485").positions}[0x3B]
        result = self._diagnose_with_regs(
            0x3B, bytes([0x51, 0x04, 0x00, 0x00]),   # TI VID 0x0451
            bytes([0x04, 0x17, 0x32, 0xCD]))          # DID 0xCD321704
        self.analyzer.apply_socket_expectations(result, pos)
        self.assertIn(FaultType.CHIP_MISMATCH, result.faults)
        self.assertTrue(result.is_vanilla)
        self.assertTrue(any("Vanilla" in d for d in result.fault_details))

    def test_chip_mismatch_not_raised_for_correct_otp_chip(self):
        """Apple OTP-ed chip (VID 0x2804, DID CD3218) in U5500@0x3A on
        A2485 is the correct part — no CHIP_MISMATCH."""
        from cd3217_analyzer.models import get_model
        pos = {p.address: p for p in get_model("A2485").positions}[0x3A]
        result = self._diagnose_with_regs(
            0x3A, bytes([0x04, 0x28, 0x00, 0x00]),   # Apple VID 0x2804
            bytes([0x04, 0x18, 0x32, 0xCD]))          # DID 0xCD321804
        self.analyzer.apply_socket_expectations(result, pos)
        self.assertNotIn(FaultType.CHIP_MISMATCH, result.faults)
        self.assertFalse(result.is_vanilla)

    def test_chip_mismatch_wrong_generation(self):
        """An ACE1 generation part (CD3215) in an ACE2 socket (U5500@0x3A)
        must be flagged CHIP_MISMATCH even with the right VID — genuinely
        wrong generation."""
        from cd3217_analyzer.models import get_model
        pos = {p.address: p for p in get_model("A2485").positions}[0x3A]
        result = self._diagnose_with_regs(
            0x3A, bytes([0x04, 0x28, 0x00, 0x00]),   # Apple VID
            bytes([0x04, 0x15, 0x32, 0xCD]))          # DID 0xCD321500 (ACE1)
        self.analyzer.apply_socket_expectations(result, pos)
        self.assertIn(FaultType.CHIP_MISMATCH, result.faults)
        self.assertTrue(any("generation" in d for d in result.fault_details))

    def test_chip_mismatch_not_raised_same_ace2_core(self):
        """A CD3217-marked board (A2251) whose chips report the shared
        ACE2/Burnside CD3218 die must NOT be flagged CHIP_MISMATCH — the
        part numbers share one core and retail boards legitimately report
        the CD3218 family. This was the 'healthy A2251 shows all chips
        faulty' bug: all four sockets falsely faulted."""
        from cd3217_analyzer.models import get_model
        regs = self._diagnose_with_regs(
            0x38, bytes([0x04, 0x28, 0x00, 0x00]),   # Apple VID 0x2804
            bytes([0x04, 0x18, 0x32, 0xCD]))          # DID 0xCD321804 (CD3218 die)
        for pos in get_model("A2251").positions:
            if pos.address != 0x38:
                continue
            self.analyzer.apply_socket_expectations(regs, pos)
        self.assertNotIn(FaultType.CHIP_MISMATCH, regs.faults)
        # donor-revision note is present, but no fault / no WARN downgrade
        self.assertTrue(any("donor-revision" in d for d in regs.fault_details))

    def test_parse_silicon(self):
        self.assertEqual(self.analyzer.parse_silicon("0xCD321804"), "CD3218")
        self.assertEqual(self.analyzer.parse_silicon("0xCD321704"), "CD3217")
        self.assertEqual(self.analyzer.parse_silicon("0x00000000"), "")
        self.assertEqual(self.analyzer.parse_silicon(None), "")
        # raw DID path preferred over the display string
        self.assertEqual(self.analyzer.parse_silicon(None, 0xCD321804), "CD3218")
        self.assertEqual(self.analyzer.parse_silicon(None, 0xCD321704), "CD3217")
        self.assertEqual(self.analyzer.parse_silicon("0xCD321804", 0xCD321704),
                         "CD3217")
        self.assertEqual(self.analyzer.parse_silicon("0x00000000", 0), "")
        # raw path wins even when the string is ambiguous/garbage
        self.assertEqual(self.analyzer.parse_silicon("0xDEADBEEF", 0xCD3215F0),
                         "CD3215")
        # ACE1 vs ACE2 generations both recognized
        self.assertEqual(self.analyzer.parse_silicon("0xCD3215A0"), "CD3215")

    def test_health_score_zero_when_no_response(self):
        """Test health score is 0 when device doesn't respond."""
        self.mock_adapter.ping.return_value = False
        result = self.analyzer.diagnose_device(0x38)
        self.assertEqual(result.health_score, 0)

    def test_register_suspicious_accepts_4cc_length_prefix(self):
        """A healthy 4CC register read with a length prefix byte (verified
        wire format [0x04,'A','P','P'] = 'APP') must NOT be flagged as
        contaminated — a leading 0x04 is the length byte, not bus noise."""
        from cd3217_analyzer.analyzer import RegisterRead
        mode = RegisterRead(offset=0x03, name="Mode",
                            raw_bytes=bytes([0x04, 0x41, 0x50, 0x50]),
                            raw_value=0x50504104)
        self.assertFalse(self.analyzer._register_suspicious(0x03, mode))
        # real bus garbage (undriven 0xFF) still flags
        bad = RegisterRead(offset=0x03, name="Mode",
                           raw_bytes=bytes([0xFF, 0xFF, 0xFF, 0xFF]),
                           raw_value=0xFFFFFFFF)
        self.assertTrue(self.analyzer._register_suspicious(0x03, bad))


class TestPowerPortRules(unittest.TestCase):
    """v0.12.0: the owner's bench scenario matrix as codified rules."""

    def _mk(self, data):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = MagicMock()
        mock.ping.return_value = True
        mock.read_bytes.side_effect = lambda a, r, l: \
            data.get(r, bytes([0x00] * l))[:l]   # unmapped live regs = 0
        return CD3217Analyzer(mock, addresses=[0x38])

    @staticmethod
    def _rb(contract_rdo=None, offers=None,
            mode=b"\x04AP ", role=0x1):
        data = {
            0x00: bytes([0x04, 0x28, 0x00, 0x00]),
            0x01: bytes([0x04, 0x17, 0x32, 0xCD]),
            0x03: mode,
            0x04: bytes([0x04, 0x49, 0x32, 0x43]),
            0x2F: (b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
                   + b"\x00" * 4),
            0x3F: bytes([role, 0x00]),
        }
        if contract_rdo is not None:
            data[0x36] = contract_rdo.to_bytes(4, "little")
        if offers is not None:
            data[0x30] = offers
        return data

    def test_scenario1_healthy_20v(self):
        rdo = (5 << 28) | (200 << 10) | 225            # 20V/2.25A PDO #5
        offers = ((3 << 12).to_bytes(2, "little")
                  + b"".join(p.to_bytes(4, "little") for p in
                             ((50 << 10) | 300, (200 << 10) | 225)))
        r = self._mk(self._rb(rdo, offers)).power_port_test(0x38)
        self.assertEqual(r.verdict, "healthy")
        self.assertTrue(r.offers_20v)
        self.assertIn("20V-class", r.direction)

    def test_scenario2_stuck_5v_supply_offers_20v(self):
        rdo = (1 << 28) | (50 << 10) | 300             # 5V/3A PDO #1
        offers = ((3 << 12).to_bytes(2, "little")
                  + b"".join(p.to_bytes(4, "little") for p in
                             ((50 << 10) | 300, (200 << 10) | 225)))
        r = self._mk(self._rb(rdo, offers)).power_port_test(0x38)
        self.assertEqual(r.verdict, "stuck-5V")
        self.assertIn("request path", r.direction)

    def test_scenario2b_stuck_5v_supply_offers_only_5v(self):
        rdo = (1 << 28) | (50 << 10) | 300
        offers = ((1 << 12).to_bytes(2, "little")
                  + (50 << 10 | 300).to_bytes(4, "little"))
        r = self._mk(self._rb(rdo, offers)).power_port_test(0x38)
        self.assertEqual(r.verdict, "stuck-5V")
        self.assertIn("source/cable", r.direction)

    def test_scenario3_no_negotiation_i2c_alive(self):
        r = self._mk(self._rb(None)).power_port_test(0x38)
        self.assertEqual(r.verdict, "no-negotiation")
        self.assertTrue(r.responds)
        self.assertIn("internal", r.direction)

    def test_chip_dead(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = MagicMock()
        mock.ping.return_value = False
        an = CD3217Analyzer(mock, addresses=[0x38])
        r = an.power_port_test(0x38)
        self.assertEqual(r.verdict, "chip-not-responding")
        self.assertFalse(r.responds)

    def test_boot_mode(self):
        r = self._mk(self._rb(None, mode=b"\x04BOO")).power_port_test(0x38)
        self.assertEqual(r.verdict, "boot-mode")

    def test_role_source_when_bit0_clear(self):
        r = self._mk(self._rb((5 << 28) | (200 << 10) | 225,
                              role=0x0)).power_port_test(0x38)
        self.assertEqual(r.role, "SOURCE")

    def test_role_sink_when_bit0_set(self):
        r = self._mk(self._rb((1 << 28) | (50 << 10) | 300,
                              role=0x1)).power_port_test(0x38)
        self.assertEqual(r.role, "SINK")


class TestGoldenPathLock(unittest.TestCase):
    """THE REGRESSION LOCK — pins the behavior that produced the 100%
    clean A2485 sessions (0.0% NACK, zero-warning exports).

    On a healthy bus (every read succeeds first try, no truncation):
      - exactly ONE ping per chip
      - exactly one read per DETAIL_REGS register — NO repair passes,
        NO re-read storms, NO 50 kHz clock changes
      - health PASS, zero faults
    Any future change that makes the healthy path chatty or alters it
    fails here before it ships.
    """

    def test_healthy_bus_minimal_reads_and_pass(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer, HealthStatus
        from cd3217_analyzer.registers import REGISTERS

        reads = []

        def rb(addr, reg, length):
            reads.append(reg)
            data = {
                0x00: bytes([0x04, 0x28, 0x00, 0x00]),
                0x01: bytes([0x04, 0x17, 0x32, 0xCD]),
                0x03: bytes([0x04, 0x41, 0x50, 0x50]),
                0x04: bytes([0x04, 0x49, 0x32, 0x43]),
                0x0F: bytes([0x04, 0x00, 0x99, 0x20]),
                0x26: bytes([0xE1, 0x20, 0x03, 0x50]),
                0x2F: (b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
                       + b"\x00" * 4),
                0x2E: bytes([0x04, 0x11, 0x22, 0x33]),
                0xA1: bytes([0x04, 0x11, 0x22, 0x33]),
            }
            return data.get(reg, bytes([0x11] * length))[:length]

        mock = MagicMock()
        mock.ping.return_value = True
        mock.read_bytes.side_effect = rb

        an = CD3217Analyzer(mock, addresses=[0x38])
        result = an.diagnose_device(0x38)

        # one read per detail register (the mocked ping issues no I2C read)
        expected = len(an.DETAIL_REGS)
        self.assertEqual(len(reads), expected,
                         f"healthy path issued {len(reads)} reads, "
                         f"expected exactly {expected}")

        # the verdict is a clean PASS
        self.assertEqual(result.health, HealthStatus.PASS)
        self.assertEqual(result.faults, [])

        # no clock meddling, no repair machinery on a healthy bus
        mock.set_i2c_clock.assert_not_called()

    def test_healthy_path_no_reread_storm(self):
        """Every identity register read exactly once (no garble rechecks,
        no truncation passes) when the data is clean."""
        from cd3217_analyzer.analyzer import CD3217Analyzer

        counts = {}

        def rb(addr, reg, length):
            counts[reg] = counts.get(reg, 0) + 1
            data = {
                0x00: bytes([0x04, 0x28, 0x00, 0x00]),
                0x01: bytes([0x04, 0x17, 0x32, 0xCD]),
                0x03: bytes([0x04, 0x41, 0x50, 0x50]),
                0x04: bytes([0x04, 0x49, 0x32, 0x43]),
                0x2F: (b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
                       + b"\x00" * 4),
            }
            return data.get(reg, bytes([0x5A] * length))[:length]

        mock = MagicMock()
        mock.ping.return_value = True
        mock.read_bytes.side_effect = rb
        an = CD3217Analyzer(mock, addresses=[0x38])
        an.diagnose_device(0x38)
        for reg, count in counts.items():
            self.assertEqual(count, 1,
                             f"register 0x{reg:02X} read {count}x on a "
                             "CLEAN bus — the happy path got chatty")


class TestLiveStateImmunity(unittest.TestCase):
    """THE A2485 SCENARIO (v0.11.13 regression): ports with no active
    contract read 0x00000000 in EVERY live register — the app must
    report PASS, never WARN. Live registers are telemetry, not health
    signals."""

    def test_idle_ports_pass(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer, HealthStatus
        from unittest.mock import MagicMock

        def rb(addr, reg, length):
            identity = {
                0x00: bytes([0x04, 0x28, 0x00, 0x00]),
                0x01: bytes([0x04, 0x17, 0x32, 0xCD]),
                0x03: bytes([0x04, 0x41, 0x50, 0x50]),
                0x04: bytes([0x04, 0x49, 0x32, 0x43]),
                0x2F: (b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
                       + b"\x00" * 4),
            }
            if reg in identity:
                return identity[reg][:length]
            # EVERY live register reads zero (idle ports, no contracts)
            return bytes([0x00] * length)

        mock = MagicMock()
        mock.ping.return_value = True
        mock.read_bytes.side_effect = rb
        an = CD3217Analyzer(mock, addresses=[0x38])
        result = an.diagnose_device(0x38)
        self.assertNotIn("CORRUPTED_REGISTERS",
                         [f.value for f in result.faults])
        self.assertEqual(result.health, HealthStatus.PASS)
        # the contract telemetry still reports the idle state
        self.assertIn("no contract",
                      result.registers[0x36].decoded)


class TestDuplicateProbe(unittest.TestCase):
    """v0.12.5: the empirical two-chips-at-one-address detector."""

    def _mk(self, did_sequence):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        calls = {"n": 0}

        def rb(addr, reg, length):
            if reg == 0x01:
                i = min(calls["n"], len(did_sequence) - 1)
                calls["n"] += 1
                return did_sequence[i].to_bytes(4, "little")
            return {
                0x00: bytes([0x04, 0x28, 0x00, 0x00]),
                0x03: bytes([0x04, 0x41, 0x50, 0x50]),
                0x04: bytes([0x04, 0x49, 0x32, 0x43]),
            }.get(reg, bytes([0x5A] * length))[:length]

        mock = MagicMock()
        mock.ping.return_value = True
        mock.read_bytes.side_effect = rb
        return CD3217Analyzer(mock, addresses=[0x3F])

    def test_alternating_dids_confirm_duplicate(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        a = 0xCD321704
        b = 0xCD321804
        an = self._mk([a, b, a, b, a])
        conf = an.probe_duplicate_address(0x3F)
        self.assertIsNotNone(conf)
        self.assertIn("two chips", conf)
        self.assertIn("0x%08X" % a, conf)
        self.assertIn("0x%08X" % b, conf)

    def test_stable_did_no_duplicate(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        an = self._mk([0xCD321704] * 5)
        self.assertIsNone(an.probe_duplicate_address(0x3F))

    def test_garbage_reads_ignored(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        an = self._mk([0xFFFFFFFF, 0x00000000, 0xCD321704, 0xCD321704])
        self.assertIsNone(an.probe_duplicate_address(0x3F))


class TestDuplicateAddressSignature(unittest.TestCase):
    """v0.12.5: the generic duplicate-address hint — an OTP socket
    missing while another chip's identity reads garble."""

    def _results(self, respond_3b=False, garble_3f=True):
        from cd3217_analyzer.analyzer import DeviceResult
        from cd3217_analyzer.analyzer import RegisterRead
        r38 = DeviceResult(address=0x38)
        r38.responds = True
        r3f = DeviceResult(address=0x3F)
        r3f.responds = True
        r3f.registers[0x00] = RegisterRead(0x00, "VID",
            bytes([0xFF, 0x04, 0x00, 0x00]), 0x000004FF, "")
        r3f.registers[0x01] = RegisterRead(0x01, "DID",
            bytes([0x11, 0x11, 0x11, 0x11]), 0x11111111, "")
        r3b = DeviceResult(address=0x3B)
        r3b.responds = respond_3b
        r3c = DeviceResult(address=0x3C)
        r3c.responds = True
        return {0x38: r38, 0x3F: r3f, 0x3B: r3b, 0x3C: r3c}

    def test_fires_on_missing_plus_garbled(self):
        from cd3217_analyzer.analyzer import duplicate_address_signature
        from cd3217_analyzer.models import get_model
        sig = duplicate_address_signature(get_model("A2251"),
                                          self._results())
        self.assertIsNotNone(sig)
        self.assertIn("U3100_W", sig)   # the A2251's full refdes
        self.assertIn("0x3F", sig)

    def test_silent_when_all_healthy(self):
        from cd3217_analyzer.analyzer import duplicate_address_signature
        from cd3217_analyzer.models import get_model
        res = self._results(respond_3b=True, garble_3f=False)
        # make 0x3F clean
        from cd3217_analyzer.analyzer import RegisterRead
        res[0x3F].registers[0x00] = RegisterRead(0x00, "VID",
            bytes([0x04, 0x28, 0x00, 0x00]), 0x00002804, "")
        res[0x3F].registers[0x01] = RegisterRead(0x01, "DID",
            bytes([0x04, 0x17, 0x32, 0xCD]), 0xCD321704, "")
        sig = duplicate_address_signature(get_model("A2251"), res)
        self.assertIsNone(sig)

    def test_silent_without_otp_pair(self):
        """A model with <2 OTP sockets cannot produce the signature."""
        from cd3217_analyzer.analyzer import duplicate_address_signature
        from cd3217_analyzer.models import get_model
        sig = duplicate_address_signature(get_model("A2337"),
                                          self._results())
        self.assertIsNone(sig)


class TestPingFallback(unittest.TestCase):
    """v0.12.3: a board whose PINGS fail at 100 kHz gets the half-clock
    at the ping level — the diagnose proceeds instead of dying with
    0 register reads (the A2141 T2-era session pattern)."""

    def test_ping_fallback_engages_half_clock(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer, HealthStatus
        state = {"hz": 100_000}

        class SlowChip:
            def __init__(self):
                self.clocks = []
            def set_i2c_clock(self, hz):
                self.clocks.append(hz)
                state["hz"] = hz
            def open(self): pass
            def ping(self, addr):
                return state["hz"] <= 50_000   # only answers at 50 kHz
            def read_bytes(self, addr, reg, length):
                if state["hz"] > 50_000:
                    raise OSError("stretches too long at 100 kHz")
                return TestAdaptiveSettle._rb_all_clean(addr, reg, length)

        chip = SlowChip()
        an = CD3217Analyzer(chip, addresses=[0x38])
        result = an.diagnose_device(0x38)
        self.assertIn(50_000, chip.clocks)             # 50_000 engaged
        self.assertEqual(chip.clocks[-1], 100_000)     # restored
        self.assertEqual(result.health, HealthStatus.PASS)
        self.assertGreater(len(result.registers), 0)   # reads happened


class TestRDO(unittest.TestCase):
    """v0.11.7: live PD contract (register 0x26 RDO) — the direct
    pointer for 0V/5V/20V port complaints."""

    def setUp(self):
        from cd3217_analyzer.registers import decode_rdo
        self.decode = decode_rdo

    def test_20v_contract(self):
        rdo = (5 << 28) | (200 << 10) | 225   # PDO 5, 20.0V, 2.25A
        self.assertEqual(self.decode(rdo), "20.0V/2.25A (PDO #5)")

    def test_5v_contract(self):
        rdo = (1 << 28) | (50 << 10) | 300    # PDO 1, 5.0V, 3.0A
        self.assertEqual(self.decode(rdo), "5.0V/3.00A (PDO #1)")

    def test_no_contract(self):
        self.assertEqual(self.decode(0), "no contract")

    def test_zero_voltage_is_no_contract(self):
        rdo = (3 << 28) | 150                 # current but no voltage
        self.assertEqual(self.decode(rdo), "no contract")

    def test_diagnose_reads_rdo(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        from unittest.mock import MagicMock

        def rb(addr, reg, length):
            data = {
                0x00: bytes([0x04, 0x28, 0x00, 0x00]),
                0x01: bytes([0x04, 0x17, 0x32, 0xCD]),
                0x03: bytes([0x04, 0x41, 0x50, 0x50]),
                0x04: bytes([0x04, 0x49, 0x32, 0x43]),
                0x36: bytes([0xE1, 0x20, 0x03, 0x50]),   # 20V/2.25A PDO#5 contract (ActiveRDO @0x36)
            }
            return data.get(reg, bytes([0x5A] * length))[:length]

        mock = MagicMock()
        mock.ping.return_value = True
        mock.read_bytes.side_effect = rb
        an = CD3217Analyzer(mock, addresses=[0x3A])
        result = an.diagnose_device(0x3A)
        rdo = result.registers.get(0x36)
        self.assertIsNotNone(rdo)
        self.assertIn("20.0V", rdo.decoded)


class TestBusHealth(unittest.TestCase):
    """Tests for the bus-integrity counter (NACK/retry/garbled-read)."""

    def setUp(self):
        self.mock_adapter = MagicMock()
        self.mock_adapter.ping.return_value = True
        self.mock_adapter.read_bytes.return_value = bytes([0x51, 0x04, 0x00, 0x00])
        self.analyzer = CD3217Analyzer(self.mock_adapter, addresses=[0x38])

    def test_clean_bus_no_counters(self):
        self.analyzer.diagnose_device(0x38)
        s = self.analyzer.bus_stats
        self.assertGreater(s.pings, 0)
        self.assertGreater(s.reads, 0)
        self.assertEqual(s.ping_failures, 0)
        self.assertEqual(s.read_failures, 0)
        self.assertEqual(s.contaminated_rereads, 0)
        self.assertFalse(s.marginal)
        # bus_health_summary reports clean
        self.assertIn("Bus clean", self.analyzer.bus_health_summary())

    def test_nack_marks_bus_marginal(self):
        # A ping that only ACKs after a retry = recovered flakiness = the
        # bus-margin signal (probe/pull-up margin), even though the chip is OK.
        self.mock_adapter.ping.side_effect = [False, True, True]
        self.analyzer.diagnose_device(0x38)
        s = self.analyzer.bus_stats
        self.assertEqual(s.ping_failures, 1)
        self.assertEqual(s.ping_recovered, 1)
        self.assertTrue(s.marginal)
        summary = self.analyzer.bus_health_summary()
        self.assertIn("WARN", summary)
        self.assertIn("answered only after retries", summary)

    def test_hard_read_failure_not_marginal(self):
        # Reads that never succeed are hard failures — a dead/absent chip
        # produces them on every scan. With a whole pass failing (95%),
        # the summary escalates to the SEVERE physical checklist; the
        # per-chip verdict stays NO_RESPONSE either way.
        self.mock_adapter.read_bytes.side_effect = [OSError("nack")]
        self.analyzer.diagnose_device(0x38)
        s = self.analyzer.bus_stats
        self.assertGreaterEqual(s.read_failures, 1)
        self.assertFalse(s.marginal)
        self.assertIn("SEVERE", self.analyzer.bus_health_summary())

    def test_contaminated_read_counts_reread(self):
        def flaky(a, reg, length):
            if reg == 0x03:  # mode garbled on first try
                return bytes([0xFF, 0xFF, 0xFF, 0xFF])
            return bytes([0x51, 0x04, 0x00, 0x00])

        self.mock_adapter.read_bytes.side_effect = flaky
        self.analyzer.diagnose_device(0x38)
        s = self.analyzer.bus_stats
        self.assertGreaterEqual(s.contaminated_rereads, 1)
        self.assertTrue(s.marginal)

    def test_reset_bus_stats(self):
        self.mock_adapter.ping.side_effect = [False, True, True]
        self.analyzer.diagnose_device(0x38)
        self.assertGreater(self.analyzer.bus_stats.ping_failures, 0)
        self.analyzer.reset_bus_stats()
        s = self.analyzer.bus_stats
        self.assertEqual(s.ping_failures, 0)
        self.assertEqual(s.reads, 0)
        self.assertFalse(s.marginal)

    def test_catastrophic_nack_rate_escalates_message(self):
        """v0.9.9: >30% failures with enough volume escalates to the SEVERE
        physical checklist (GND reference, wires, power)."""
        bs = self.analyzer.bus_stats
        for _ in range(90):
            bs.add_ping(False)
        for _ in range(20):
            bs.add_read(False)
        summary = self.analyzer.bus_health_summary()
        self.assertIn("SEVERE", summary)
        self.assertIn("GROUND", summary)

    def test_normal_failure_rate_stays_marginal(self):
        bs = self.analyzer.bus_stats
        for _ in range(90):
            bs.add_ping(True)
        bs.add_ping(False)
        summary = self.analyzer.bus_health_summary()
        self.assertNotIn("SEVERE", summary)

    def test_bus_stats_nack_rate(self):
        self.analyzer.bus_stats.add_ping(True)
        self.analyzer.bus_stats.add_ping(False)
        self.assertEqual(self.analyzer.bus_stats.nack_rate, 0.5)

    def test_quick_scan_excludes_all_call_address(self):
        """L3: the ACE2 all-call (0x6B) ACKs by construction (every chip
        answers it at once) and must never be reported as a device."""
        self.mock_adapter.ping.return_value = True
        found = self.analyzer.quick_scan()
        self.assertNotIn(0x6B, found)

    def test_identify_chip_type_uses_vid_not_address(self):
        """L2: vanilla/OTP must come from the Vendor ID (0x0451 TI stock /
        0x2804 Apple OTP), not from the address — the old heuristic labeled
        strap addresses like 0x7E as OTP."""
        self.mock_adapter.read_bytes.return_value = bytes([0x51, 0x04, 0x00, 0x00])
        result = self.analyzer.identify_chip_type(0x7E)
        self.assertIn("Vanilla", result)          # strap address, NOT otp label
        self.assertIn("0x0451", result)

        self.mock_adapter.read_bytes.return_value = bytes([0x04, 0x28, 0x00, 0x00])
        result = self.analyzer.identify_chip_type(0x3B)
        self.assertIn("OTP-ed", result)
        self.assertIn("0x2804", result)

        # unreadable VID -> None
        self.mock_adapter.read_bytes.return_value = None
        self.assertIsNone(self.analyzer.identify_chip_type(0x38))

    def test_boot_mode_recheck_transition_is_not_a_fault(self):
        """L4: BOOT right after power-on is normal; a re-check that shows
        APP (or any non-BOOT mode) means a healthy boot transition — no
        BOOT_FAILED fault."""
        self.mock_adapter.ping.return_value = True
        state = {"n": 0}

        def rb(addr, reg, length):
            if reg == 0x03:
                state["n"] += 1
                if state["n"] == 1:
                    return bytes([0x04, 0x42, 0x4F, 0x4F])  # "BOO"->BOOT
                return bytes([0x20, 0x41, 0x50, 0x50])      # "APP "
            if reg == 0x00:
                return bytes([0x51, 0x04, 0x00, 0x00])
            if reg == 0x04:
                return bytes([0x20, 0x49, 0x32, 0x43])      # "I2C"
            return bytes([0x00] * length)

        self.mock_adapter.read_bytes.side_effect = rb
        result = self.analyzer.diagnose_device(0x38)
        self.assertNotIn(FaultType.BOOT_FAILED, result.faults)
        self.assertEqual(result.mode.strip(), "APP")
        self.assertTrue(any("healthy boot transition" in d
                            for d in result.fault_details))

    def test_boot_mode_persistent_still_faults(self):
        """L4: BOOT that persists after the settle re-check is still a real
        BOOT_FAILED."""
        self.mock_adapter.ping.return_value = True

        def rb(addr, reg, length):
            if reg == 0x03:
                return bytes([0x04, 0x42, 0x4F, 0x4F])  # BOOT every read
            if reg == 0x00:
                return bytes([0x51, 0x04, 0x00, 0x00])
            return bytes([0x00] * length)

        self.mock_adapter.read_bytes.side_effect = rb
        result = self.analyzer.diagnose_device(0x38)
        self.assertIn(FaultType.BOOT_FAILED, result.faults)


class TestDeviceInfo(unittest.TestCase):
    """F2/S2: register-0x2F identity parsing + DID/string garble rechecks."""

    def setUp(self):
        from cd3217_analyzer.registers import parse_device_info
        self.parse = parse_device_info
        self.mock_adapter = MagicMock()
        self.mock_adapter.ping.return_value = True
        self.analyzer = CD3217Analyzer(self.mock_adapter, addresses=[0x38])

    def test_parse_apple_identity_string(self):
        raw = b"CD3217   HW0022 FW002.170.00 ZACE2-J316P01P" + b"\x00" * 4
        ident = self.parse(raw)
        self.assertEqual(ident.silicon, "CD3217")
        self.assertEqual(ident.hw, "22")
        self.assertEqual(ident.fw, "002.170.00")
        self.assertEqual(ident.variant, "ZACE2-J316P01P")
        self.assertIn("ZACE2-J316P01P", ident.raw)

    def test_parse_ti_identity_string(self):
        ident = self.parse(b"TPS65988 HW0030 FWF807.12.00 ZAce1\x00")
        self.assertEqual(ident.silicon, "TPS65988")
        self.assertEqual(ident.hw, "30")
        self.assertEqual(ident.fw, "F807.12.00")
        self.assertEqual(ident.variant, "ZAce1")

    def test_parse_real_a2485_sample_bytes(self):
        """Regression: exact 0x2F bytes from the real A2485 (820-02382)
        capture — leading '@' marker, RACE2 variant tag (not ZACE2)."""
        hexstr = ("404344333231382020204857303032322046573030322e3137302e"
                  "30302052414345322d4a33313650355520202020")
        ident = self.parse(bytes.fromhex(hexstr))
        self.assertEqual(ident.silicon, "CD3218")
        self.assertEqual(ident.hw, "22")
        self.assertEqual(ident.fw, "002.170.00")
        self.assertEqual(ident.variant, "RACE2-J316P5U")
        self.assertIn("RACE2-J316P5U", ident.raw)

    def test_parse_garbled_or_empty(self):
        self.assertEqual(self.parse(b"\x00" * 47).silicon, "")
        self.assertEqual(self.parse(b"").silicon, "")
        self.assertEqual(self.parse(None).silicon, "")

    def test_diagnose_collects_identity(self):
        ident_bytes = (b"CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
                       + b"\x00" * 4)
        state = {"n": 0}

        def rb(addr, reg, length):
            if reg == 0x2F:
                return ident_bytes[:length]
            if reg == 0x00:
                return bytes([0x51, 0x04, 0x00, 0x00])
            if reg == 0x01:
                return bytes([0x04, 0x18, 0x32, 0xCD])
            if reg == 0x03:
                state["n"] += 1
                return bytes([0x20, 0x41, 0x50, 0x50])   # APP
            if reg == 0x04:
                return bytes([0x20, 0x49, 0x32, 0x43])   # I2C
            return bytes([0x00] * length)

        self.mock_adapter.read_bytes.side_effect = rb
        result = self.analyzer.diagnose_device(0x38)
        self.assertIn("ZACE2-J316P01P", result.device_info)
        self.assertEqual(result.hw_version, "22")
        self.assertEqual(result.fw_version, "002.170.00")
        self.assertEqual(result.fw_variant, "ZACE2-J316P01P")

    def test_did_garbled_read_is_reread(self):
        """S2: DID reads that come back all-0xFF/0x00 garbage get re-read;
        the clean retry replaces the garbage value."""
        calls = {"0x01": 0}

        def rb(addr, reg, length):
            if reg == 0x01:
                calls["0x01"] += 1
                if calls["0x01"] == 1:
                    return bytes([0xFF, 0xFF, 0xFF, 0xFF])
                return bytes([0x04, 0x18, 0x32, 0xCD])
            if reg == 0x00:
                return bytes([0x51, 0x04, 0x00, 0x00])
            if reg == 0x03:
                return bytes([0x20, 0x41, 0x50, 0x50])
            if reg == 0x04:
                return bytes([0x20, 0x49, 0x32, 0x43])
            return bytes([0x5A] * length)

        self.mock_adapter.read_bytes.side_effect = rb
        result = self.analyzer.diagnose_device(0x38)
        self.assertEqual(calls["0x01"], 2)
        self.assertEqual(result.did_raw, 0xCD321804)
        self.assertGreaterEqual(
            self.analyzer.bus_stats.contaminated_rereads, 1)
        self.assertTrue(self.analyzer.bus_stats.marginal)

    def test_did_plausible_ti_did_not_reread(self):
        """S2/v0.9.0: a non-0xCD DID (e.g. ACE1 donor) triggers exactly one
        byte-wise verification pass (harmless, read-only) and the original
        DID is preserved when it still does not decode."""
        calls = {"0x01": 0}

        def rb(addr, reg, length):
            if reg == 0x01:
                calls["0x01"] += 1
                if length >= 4:
                    return bytes([0x01, 0x98, 0x65, 0x12])  # 0x12986501
                return bytes([0x01])          # chunked single-byte reads
            if reg == 0x00:
                return bytes([0x51, 0x04, 0x00, 0x00])
            if reg == 0x03:
                return bytes([0x20, 0x41, 0x50, 0x50])
            if reg == 0x04:
                return bytes([0x20, 0x49, 0x32, 0x43])
            return bytes([0x5A] * length)

        self.mock_adapter.read_bytes.side_effect = rb
        result = self.analyzer.diagnose_device(0x38)
        self.assertEqual(calls["0x01"], 2)   # 1 direct + 1 verification byte
        self.assertEqual(result.did_raw, 0x12659801)  # preserved
        self.assertEqual(self.analyzer.bus_stats.contaminated_rereads, 0)

    def test_device_info_garbled_read_is_reread(self):
        """S2: 0x2F identity strings are printable+NUL — garbled reads get
        re-read, and the clean retry populates the identity fields."""
        calls = {"0x2F": 0}
        good = b"CD3217   HW0022 FW002.170.00 ZACE2-J316P01P" + b"\x00" * 4

        def rb(addr, reg, length):
            if reg == 0x2F:
                calls["0x2F"] += 1
                if calls["0x2F"] == 1:
                    return bytes([0xA5, 0x5A, 0x01, 0x02, 0x03, 0x04, 0x05,
                                  0x06, 0x07, 0x08, 0x09, 0x0A])[:length]
                return good[:length]
            if reg == 0x00:
                return bytes([0x51, 0x04, 0x00, 0x00])
            if reg == 0x01:
                return bytes([0x04, 0x18, 0x32, 0xCD])
            if reg == 0x03:
                return bytes([0x20, 0x41, 0x50, 0x50])
            if reg == 0x04:
                return bytes([0x20, 0x49, 0x32, 0x43])
            return bytes([0x00] * length)

        self.mock_adapter.read_bytes.side_effect = rb
        result = self.analyzer.diagnose_device(0x38)
        self.assertGreaterEqual(calls["0x2F"], 2)
        self.assertEqual(result.fw_variant, "ZACE2-J316P01P")

    def test_save_json_report_includes_identity(self):
        import json
        import tempfile
        from cd3217_analyzer.analyzer import DeviceResult, DiagnosticReport
        from cd3217_analyzer.report import save_json_report
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/r.json"
            dev = DeviceResult(address=0x38)
            dev.device_info = "CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
            dev.hw_version = "22"
            dev.fw_version = "002.170.00"
            dev.fw_variant = "ZACE2-J316P01P"
            report = DiagnosticReport()
            report.devices = [dev]
            save_json_report(report, path)
            with open(path) as f:
                data = json.load(f)
        d = data["devices"][0]
        self.assertIn("ZACE2-J316P01P", d["device_info"])
        self.assertEqual(d["fw_variant"], "ZACE2-J316P01P")


class _BridgeMock:
    """Bridge adapter stand-in with controllable clock + register data."""

    def __init__(self, rb, ping=True, with_clock=True):
        self._rb = rb
        self._ping = ping
        self.clocks = []
        self.hz = 100_000
        if with_clock:
            self.set_i2c_clock = self._set_clock

    def _set_clock(self, hz):
        self.hz = hz
        self.clocks.append(hz)

    def open(self):
        pass

    def ping(self, addr):
        return self._ping

    def read_bytes(self, addr, reg, length):
        return self._rb(reg, length)[:length]


def _clean_regs(holder=None):
    """Register data: clean at 100 kHz; when ``holder`` is given, mode
    register garbles at >100 kHz (the marginal-bus scenario)."""
    def rb(reg, length):
        if reg == 0x00:
            return bytes([0x51, 0x04, 0x00, 0x00])
        if reg == 0x01:
            return bytes([0x04, 0x18, 0x32, 0xCD])
        if reg == 0x03:
            if holder is not None and holder.hz > 100_000:
                return bytes([0xFF, 0x41, 0x50, 0x50])  # garbled top byte
            return bytes([0x20, 0x41, 0x50, 0x50])      # "APP "
        if reg == 0x04:
            return bytes([0x20, 0x49, 0x32, 0x43])      # "I2C"
        if reg == 0x2F:
            return (b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
                    + b"\x00" * 4)[:length]
        if reg == 0x26:
            return bytes([0x00, 0x00, 0x00, 0x00])
        if reg == 0x36:
            return bytes([0xE1, 0x20, 0x03, 0x50])
        if reg == 0x35:
            return bytes([0x00, 0x32, 0x00, 0x00])
        if reg == 0x30:
            return (b"\x03\x00" + (200 << 10 | 300).to_bytes(4, "little"))[:length]
        return bytes([0x5A] * length)
    return rb


class TestStressMargin(unittest.TestCase):
    """S1: bus-speed stress probe verdicts + clock restoration."""

    def test_ample_margin_restores_clock(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = _BridgeMock(_clean_regs())
        an = CD3217Analyzer(mock, addresses=[0x38])
        res = an.stress_test_margin(0x38)
        self.assertEqual(res["verdict"], "ample-margin")
        self.assertEqual(mock.clocks, [400_000, 100_000])

    def test_marginal_garbles_only_at_400k(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = _BridgeMock(lambda r, l: b"\x00" * l)
        mock._rb = _clean_regs(mock)          # rb watches mock.hz
        an = CD3217Analyzer(mock, addresses=[0x38])
        res = an.stress_test_margin(0x38)
        self.assertEqual(res["verdict"], "marginal")
        self.assertIn("0x03", res["detail"])
        self.assertIn("SLVA689", res["detail"])
        self.assertEqual(mock.clocks, [400_000, 100_000])

    def test_bus_problem_leaves_clock_untouched(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer

        def rb(reg, length):
            if reg in (0x00, 0x01, 0x03, 0x04):
                return bytes([0xFF, 0xFF, 0xFF, 0xFF])
            return bytes([0x00] * length)

        mock = _BridgeMock(rb)
        an = CD3217Analyzer(mock, addresses=[0x38])
        res = an.stress_test_margin(0x38)
        self.assertEqual(res["verdict"], "bus-problem")
        self.assertEqual(mock.clocks, [])     # no clock change performed

    def test_no_response_verdict(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = _BridgeMock(_clean_regs(), ping=False)
        an = CD3217Analyzer(mock, addresses=[0x38])
        res = an.stress_test_margin(0x38)
        self.assertEqual(res["verdict"], "no-response")
        # the ping-level fallback engages 50k then the diagnose restores
        self.assertEqual(mock.clocks, [400_000 // 8, 100_000])

    def test_unavailable_without_bridge(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = _BridgeMock(_clean_regs(), with_clock=False)
        an = CD3217Analyzer(mock, addresses=[0x38])
        res = an.stress_test_margin(0x38)
        self.assertEqual(res["verdict"], "unavailable")
        self.assertFalse(res["supported"])


class TestAdaptiveSettle(unittest.TestCase):
    """S3: settle sleeps only after recovered flakiness."""

    @staticmethod
    def _rb_all_clean(addr, reg, length):
        if reg == 0x00:
            return bytes([0x51, 0x04, 0x00, 0x00])
        if reg == 0x01:
            return bytes([0x04, 0x18, 0x32, 0xCD])
        if reg in (0x03, 0x04):
            return bytes([0x20, 0x41, 0x50, 0x50])
        if reg == 0x2F:
            return (b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
                    + b"\x00" * 4)[:length]
        if reg == 0x26:
            return bytes([0x00, 0x00, 0x00, 0x00])
        if reg == 0x36:
            return bytes([0xE1, 0x20, 0x03, 0x50])
        if reg == 0x35:
            return bytes([0x00, 0x32, 0x00, 0x00])
        if reg == 0x30:
            return (b"\x03\x00" + (200 << 10 | 300).to_bytes(4, "little"))[:length]
        return bytes([0x5A] * length)

    def _mk(self, rb, ping=True):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = MagicMock()
        mock.ping.return_value = ping
        mock.read_bytes.side_effect = rb
        return CD3217Analyzer(mock, addresses=[0x38])

    def test_adaptive_settle_after_flaky_diagnosis(self):
        from unittest.mock import patch
        an = self._mk(self._rb_all_clean)
        an.adapter.ping.side_effect = [False, True, True]   # recovered ping
        with patch("cd3217_analyzer.analyzer.time.sleep") as slp:
            an.diagnose_device(0x38)
        delays = [c.args[0] for c in slp.call_args_list]
        self.assertIn(an.ADAPTIVE_PING_SETTLE, delays)      # after retry ping
        self.assertIn(an.ADAPTIVE_SETTLE, delays)           # diagnosis-level

    def test_no_adaptive_settle_on_clean_diagnosis(self):
        from unittest.mock import patch
        an = self._mk(self._rb_all_clean)
        with patch("cd3217_analyzer.analyzer.time.sleep") as slp:
            an.diagnose_device(0x38)
        delays = [c.args[0] for c in slp.call_args_list]
        self.assertNotIn(an.ADAPTIVE_PING_SETTLE, delays)
        self.assertNotIn(an.ADAPTIVE_SETTLE, delays)


class TestBatchRetry(unittest.TestCase):
    """v0.7.2: Diagnose-All retry ladder — only transport failures retry,
    and the best verdict is kept."""

    def test_retryable(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        self.assertTrue(CD3217Analyzer.is_retryable_failure(None))
        r = MagicMock()
        r.health = HealthStatus.FAIL
        r.faults = [FaultType.NO_RESPONSE]
        self.assertTrue(CD3217Analyzer.is_retryable_failure(r))
        r.faults = [FaultType.I2C_ERROR]
        self.assertTrue(CD3217Analyzer.is_retryable_failure(r))

    def test_not_retryable(self):
        from cd3217_analyzer.analyzer import CD3217Analyzer
        r = MagicMock()
        r.health = HealthStatus.FAIL
        # WRONG_VID is retryable since v0.8.4: a CD3217-family chip always
        # reports TI/Apple, so an unexpected VID is wire corruption, which
        # the user's "2 faulty in Diagnose All, all good per-chip" proved.
        r.faults = [FaultType.WRONG_VID]
        self.assertTrue(CD3217Analyzer.is_retryable_failure(r))
        r.health = HealthStatus.PASS
        r.faults = []
        self.assertFalse(CD3217Analyzer.is_retryable_failure(r))
        r.health = HealthStatus.WARN
        r.faults = [FaultType.CHIP_MISMATCH]
        self.assertFalse(CD3217Analyzer.is_retryable_failure(r))

    def test_batch_recover_transient_nack(self):
        """A chip that NACKs on the first Diagnose-All pass recovers on a
        later pass and the PASS verdict wins."""
        from unittest.mock import patch
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = MagicMock()
        mock.ping.return_value = True
        mock.read_bytes.side_effect = TestAdaptiveSettle._rb_all_clean

        # pass 1: chip does not answer; pass 2: healthy
        an = CD3217Analyzer(mock, addresses=[0x3F])
        best = None
        with patch("cd3217_analyzer.analyzer.time.sleep"):
            for settle in (0.0, 0.8, 1.6):
                result = an.diagnose_device(0x3F)
                if best is None or CD3217Analyzer._HEALTH_RANK.get(
                        result.health, 0) > CD3217Analyzer._HEALTH_RANK.get(
                        best.health, 0):
                    best = result
                if not CD3217Analyzer.is_retryable_failure(result):
                    break
        self.assertEqual(best.health, HealthStatus.PASS)

    def test_corruption_repass_recovers_healthy_chip(self):
        """v0.7.5: a garbled read burst (user saw 0x3C FAIL(55) in Diagnose
        All, PASS(90) per-chip) must be re-read once; the clean snapshot
        wins and CORRUPTED_REGISTERS is not raised."""
        mock = MagicMock()
        mock.ping.return_value = True
        state = {"pass": 0}   # completed read passes

        def counting_rb(addr, reg, length):
            if reg == 0x2F:            # last reg in DETAIL_REGS = pass marker
                state["pass"] += 1
            if state["pass"] == 0 and reg in (0x00, 0x01, 0x03, 0x04):
                return bytes([0x00] * length)   # pass-1 garbled identity burst
            if reg == 0x00:
                return bytes([0x04, 0x28, 0x00, 0x00])
            if reg == 0x01:
                return bytes([0x04, 0x18, 0x32, 0xCD])
            if reg in (0x03, 0x04):
                return bytes([0x20, 0x41, 0x50, 0x50])
            if reg == 0x2F:
                return (b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P"
                        + b"\x00" * 4)[:length]
            # plausible non-corrupt filler for detail regs
            return bytes([0x5A] * length)

        mock.read_bytes.side_effect = counting_rb
        an = CD3217Analyzer(mock, addresses=[0x3C])
        result = an.diagnose_device(0x3C)
        self.assertNotIn(FaultType.CORRUPTED_REGISTERS, result.faults)
        self.assertGreaterEqual(state["pass"], 2)   # a second pass happened
        # the bundled VID comes from the CLEAN pass-2 snapshot
        vid_entry = result.registers.get(0x00)
        if vid_entry is not None:
            self.assertNotEqual(vid_entry.raw_value, 0x00000000)

    def test_truncation_merge_recovers_deviceinfo(self):
        """v0.9.1: the chip prefixes each response (0x04/0x40...) and
        truncates mid-data on slow setups; merging repeated reads
        assembles the full DeviceInfo string."""
        responses = [
            b"\x40CD\xff\xff\xff",   # attempt 1: prefix + 2 chars
            b"\x40\xff3217\xff\xff",  # attempt 2: later bytes
            b"\x40CD3217   HW00\xff\xff",
            b"\x40CD3217   HW0022 FW00\xff",
        ]
        calls = {"n": 0}

        def rb(addr, reg, length):
            if reg == 0x2F:
                r = responses[min(calls["n"], len(responses) - 1)]
                calls["n"] += 1
                return (r + b"\xff" * 47)[:length]
            if reg == 0x00:
                return bytes([0x04, 0x28, 0x00, 0x00])
            if reg == 0x01:
                return bytes([0x04, 0x17, 0x32, 0xCD])
            if reg in (0x03, 0x04):
                return bytes([0x04, 0x41, 0x50, 0x50])
            return bytes([0x5A] * length)

        mock = MagicMock()
        mock.ping.return_value = True
        mock.read_bytes.side_effect = rb
        an = CD3217Analyzer(mock, addresses=[0x38])
        result = an.diagnose_device(0x38)
        merged = result.registers.get(0x2F)
        self.assertIsNotNone(merged)
        self.assertIn(b"CD3217", merged.raw_bytes)
        self.assertIn(b"HW0022", merged.raw_bytes)
        self.assertIn("CD3217", result.device_info)

    def test_retryable_includes_corrupted_registers(self):
        """v0.7.5: a CORRUPTED_REGISTERS FAIL is transport-shaped and must
        be retried by the Diagnose-All ladder."""
        r = MagicMock()
        r.health = HealthStatus.FAIL
        r.faults = [FaultType.CORRUPTED_REGISTERS]
        self.assertTrue(CD3217Analyzer.is_retryable_failure(r))

    def test_transient_nack_flow(self):
        """Real ladder flow: pass 1 NO_RESPONSE (ping fails), then the chip
        answers — best verdict must be PASS, not the first FAIL."""
        from unittest.mock import patch
        from cd3217_analyzer.analyzer import CD3217Analyzer
        mock = MagicMock()
        mock.ping.side_effect = [False, False, False,   # pass 1: dead
                                 True, True, True]      # pass 2: answers
        mock.read_bytes.side_effect = TestAdaptiveSettle._rb_all_clean
        an = CD3217Analyzer(mock, addresses=[0x3F])
        best = None
        with patch("cd3217_analyzer.analyzer.time.sleep"):
            for settle in (0.0, 0.8, 1.6):
                result = an.diagnose_device(0x3F)
                if best is None or CD3217Analyzer._HEALTH_RANK.get(
                        result.health, 0) > CD3217Analyzer._HEALTH_RANK.get(
                        best.health, 0):
                    best = result
                if not CD3217Analyzer.is_retryable_failure(result):
                    break
        self.assertIsNotNone(best)
        self.assertEqual(best.health, HealthStatus.PASS)
        self.assertEqual(best.address, 0x3F)


class TestReport(unittest.TestCase):
    """Test reporting functions."""

    def test_format_compact_result_pass(self):
        """Test compact result formatting for passing device."""
        result = MagicMock()
        result.health = HealthStatus.PASS
        result.address = 0x38
        result.mode = "APP "
        result.health_score = 95
        result.faults = []

        formatted = format_compact_result(result)
        self.assertIn("[OK]", formatted)
        self.assertIn("0x38", formatted)
        self.assertIn("score=95", formatted)

    def test_format_compact_result_fail(self):
        """Test compact result formatting for failing device."""
        result = MagicMock()
        result.health = HealthStatus.FAIL
        result.address = 0x3B
        result.mode = None
        result.health_score = 0
        result.faults = [FaultType.NO_RESPONSE]

        formatted = format_compact_result(result)
        self.assertIn("[FAIL]", formatted)
        self.assertIn("0x3B", formatted)
        self.assertIn("NO_RESPONSE", formatted)

    def test_save_json_report_includes_did_silicon_and_bus_stats(self):
        import json
        import tempfile
        from cd3217_analyzer.analyzer import (
            BusStats, DeviceResult, DiagnosticReport,
        )
        from cd3217_analyzer.report import (
            bus_stats_to_dict, save_json_report,
        )
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/report.json"
            dev = DeviceResult(address=0x38)
            dev.responds = True
            dev.device_id = "0xCD321804"
            dev.did_raw = 0xCD321804
            dev.silicon = "CD3218"
            report = DiagnosticReport(adapter_type="UsbBridgeAdapter")
            report.bus_scan_results = [0x38]
            report.devices = [dev]
            bs = BusStats()
            bs.add_ping(True)
            bs.add_ping(False)
            bs.add_recovered_ping()
            save_json_report(report, path, bus_stats=bus_stats_to_dict(bs))
            with open(path) as f:
                data = json.load(f)
        d = data["devices"][0]
        self.assertEqual(d["did_raw"], "0xCD321804")
        self.assertEqual(d["silicon"], "CD3218")
        self.assertEqual(data["bus_stats"]["pings"], 2)
        self.assertEqual(data["bus_stats"]["ping_failures"], 1)
        self.assertEqual(data["bus_stats"]["ping_recovered"], 1)
        self.assertTrue(data["bus_stats"]["bus_marginal"])
        self.assertEqual(data["bus_scan_results"], ["0x38"])


if __name__ == "__main__":
    unittest.main()
