import unittest

from core.live_mode_guard import (
    LIVE_ACK_ENV_VAR,
    LIVE_ACK_VALUE,
    LiveModeInterlockError,
    is_live_mode,
    live_mode_acknowledged,
    require_live_mode_ack,
)


class LiveModeGuardTests(unittest.TestCase):
    def test_is_live_mode_normalizes_config_value(self):
        self.assertTrue(is_live_mode({"mode": " LIVE "}))
        self.assertFalse(is_live_mode({"mode": "paper"}))
        self.assertFalse(is_live_mode({}))

    def test_live_acknowledgement_requires_exact_value_after_strip(self):
        self.assertTrue(live_mode_acknowledged({LIVE_ACK_ENV_VAR: f" {LIVE_ACK_VALUE} "}))
        self.assertFalse(live_mode_acknowledged({LIVE_ACK_ENV_VAR: "true"}))
        self.assertFalse(live_mode_acknowledged({}))

    def test_paper_mode_does_not_require_acknowledgement(self):
        require_live_mode_ack({"mode": "paper"}, {})

    def test_live_mode_without_acknowledgement_fails_closed(self):
        with self.assertRaisesRegex(LiveModeInterlockError, LIVE_ACK_ENV_VAR):
            require_live_mode_ack({"mode": "live"}, {})

    def test_live_mode_with_acknowledgement_is_allowed(self):
        require_live_mode_ack({"mode": "live"}, {LIVE_ACK_ENV_VAR: LIVE_ACK_VALUE})
