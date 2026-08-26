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
