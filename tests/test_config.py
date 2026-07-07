import os
import unittest
from unittest.mock import patch

from bot.config import Settings


class SettingsTests(unittest.TestCase):
    def test_settings_are_loaded_from_environment(self) -> None:
        env = {
            "BOT_TOKEN": "123456:test-token",
            "BOT_TIMEZONE": "Asia/Qyzylorda",
            "BOT_DATABASE": "test.sqlite3",
            "REMINDER_POLL_SECONDS": "30",
        }

        with patch("bot.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.bot_token, "123456:test-token")
        self.assertEqual(settings.timezone, "Asia/Qyzylorda")
        self.assertEqual(settings.database, "test.sqlite3")
        self.assertEqual(settings.reminder_poll_seconds, 30)

    def test_missing_token_raises_error(self) -> None:
        with patch("bot.config.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
