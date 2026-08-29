import logging
import unittest
from unittest.mock import patch

from botchan.settings import load_bot_config


class SettingsTests(unittest.TestCase):
    def test_load_bot_config_reads_token_and_defaults_log_level(self) -> None:
        with patch.dict("os.environ", {"DISCORD_TOKEN": " token "}, clear=True):
            config = load_bot_config()

        self.assertEqual(config.token, "token")
        self.assertEqual(config.log_level, logging.INFO)
        self.assertEqual(config.git_rev, "unknown")

    def test_load_bot_config_reads_git_revision(self) -> None:
        with patch.dict(
            "os.environ",
            {"DISCORD_TOKEN": "token", "BOTCHAN_GIT_REV": "abc123"},
            clear=True,
        ):
            config = load_bot_config()

        self.assertEqual(config.git_rev, "abc123")

    def test_load_bot_config_rejects_missing_token(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(RuntimeError, "DISCORD_TOKEN"),
        ):
            load_bot_config()

    def test_load_bot_config_rejects_unknown_log_level(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {"DISCORD_TOKEN": "token", "LOG_LEVEL": "LOUD"},
                clear=True,
            ),
            self.assertRaisesRegex(RuntimeError, "Unknown LOG_LEVEL: LOUD"),
        ):
            load_bot_config()


if __name__ == "__main__":
    unittest.main()
