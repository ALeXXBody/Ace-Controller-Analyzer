"""Per-instance repair state on CD3217Analyzer (audit fix 3, §4.16).

truncation_seen / _slow_clock_engaged were CLASS attributes: instances
shadowed them on write, but the class seed meant any code reading them
before the instance wrote (or getattr-fallback consumers) saw shared
state, and the flags' lifecycle was ambiguous across analyzer instances
sharing one adapter.
"""

import unittest
from unittest.mock import MagicMock

from cd3217_analyzer.analyzer import CD3217Analyzer


def _mk():
    mock = MagicMock()
    mock.ping.return_value = True
    return mock


class TestInstanceState(unittest.TestCase):
    def test_flags_are_per_instance(self):
        a = CD3217Analyzer(_mk(), addresses=[0x38])
        b = CD3217Analyzer(_mk(), addresses=[0x3F])
        a.truncation_seen = True
        a._slow_clock_engaged = True
        self.assertFalse(b.truncation_seen)
        self.assertFalse(b._slow_clock_engaged)

    def test_class_no_longer_carries_the_state(self):
        self.assertFalse(hasattr(CD3217Analyzer, "truncation_seen"))
        self.assertFalse(hasattr(CD3217Analyzer, "_slow_clock_engaged"))

    def test_fresh_instance_starts_clean(self):
        a = CD3217Analyzer(_mk(), addresses=[0x38])
        self.assertFalse(a.truncation_seen)
        self.assertFalse(a._slow_clock_engaged)
        # and the export's getattr consumer keeps working
        self.assertFalse(bool(getattr(a, "truncation_seen", False)))


if __name__ == "__main__":
    unittest.main()
