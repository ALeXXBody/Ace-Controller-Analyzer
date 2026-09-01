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
        # produces them on every scan, so they are NOT a bus-margin signal.
        self.mock_adapter.read_bytes.side_effect = [OSError("nack")]
        self.analyzer.diagnose_device(0x38)
        s = self.analyzer.bus_stats
        self.assertGreaterEqual(s.read_failures, 1)
        self.assertFalse(s.marginal)
        self.assertIn("hard", self.analyzer.bus_health_summary())

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
            return bytes([0x00] * length)

        self.mock_adapter.read_bytes.side_effect = rb
        result = self.analyzer.diagnose_device(0x38)
        self.assertEqual(calls["0x01"], 2)
        self.assertEqual(result.did_raw, 0xCD321804)
        self.assertGreaterEqual(
            self.analyzer.bus_stats.contaminated_rereads, 1)
        self.assertTrue(self.analyzer.bus_stats.marginal)

    def test_did_plausible_ti_did_not_reread(self):
        """S2: a non-0xCD DID (e.g. ACE1 donor) must NOT trigger rereads —
        it is valid silicon, just not Apple."""
        calls = {"0x01": 0}

        def rb(addr, reg, length):
            if reg == 0x01:
                calls["0x01"] += 1
                return bytes([0x01, 0x98, 0x65, 0x12])  # 0x12986501
            if reg == 0x00:
                return bytes([0x51, 0x04, 0x00, 0x00])
            if reg == 0x03:
                return bytes([0x20, 0x41, 0x50, 0x50])
            if reg == 0x04:
                return bytes([0x20, 0x49, 0x32, 0x43])
            return bytes([0x00] * length)

        self.mock_adapter.read_bytes.side_effect = rb
        self.analyzer.diagnose_device(0x38)
        self.assertEqual(calls["0x01"], 1)
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
