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
        """Test Mode register decoding for APP mode."""
        # 0x41505020 = 'APP ' in MSB-first 4CC
        result = decode_mode_reg(0x41505020)
        self.assertEqual(result, "APP ")

    def test_decode_mode_reg_boot(self):
        """Test Mode register decoding for BOOT mode."""
        result = decode_mode_reg(0x424F4F54)
        self.assertEqual(result, "BOOT")

    def test_decode_mode_reg_ptch(self):
        """Test Mode register decoding for PTCH mode."""
        result = decode_mode_reg(0x50544348)
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
        self.assertIn("PPA", (result.mode or "").upper())
        self.assertGreaterEqual(calls.get(0x00, 0), 2)  # VID was retried
        self.assertGreaterEqual(calls.get(0x03, 0), 2)  # Mode was retried

    def test_decode_mode_reg_skips_undriven_bytes(self):
        """4CC decoding must skip 0x00/0xFF undriven bytes, not render them
        as '0xFF' text that breaks mode matching."""
        self.assertEqual(decode_mode_reg(0x50504120).strip(), "PPA")
        self.assertEqual(decode_mode_reg(0xFF504120).strip(), "PA")

    def test_scan_bus_excludes_broadcast_address(self):
        """The ACE2 all-call address (0x6B) must not be reported as a device."""
        self.mock_adapter.scan.return_value = [0x38, 0x3B, 0x3F, 0x6B]
        found = self.analyzer.scan_bus()
        self.assertNotIn(0x6B, found)
        self.assertIn(0x38, found)
        self.assertEqual(len(found), 3)

    def test_health_score_zero_when_no_response(self):
        """Test health score is 0 when device doesn't respond."""
        self.mock_adapter.ping.return_value = False
        result = self.analyzer.diagnose_device(0x38)
        self.assertEqual(result.health_score, 0)


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


if __name__ == "__main__":
    unittest.main()
