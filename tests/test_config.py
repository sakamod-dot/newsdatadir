import os
import unittest
from unittest.mock import patch

from config import AppMode, load_config


class ConfigTests(unittest.TestCase):
  def test_native_mode_loads_independent_trigger_settings(self) -> None:
    environment = {
        "APP_MODE": "native_server",
        "NATIVE_TRIGGER_ON_UPDATE": "false",
        "NATIVE_TRIGGER_NEWS_COUNT": "7",
        "RSS_JITTER_SECONDS": "0",
    }
    with patch.dict(os.environ, environment, clear=True), patch("config.load_dotenv"):
      config = load_config()

    self.assertIs(config.mode, AppMode.NATIVE_SERVER)
    self.assertFalse(config.native.trigger_on_update)
    self.assertEqual(config.native.trigger_news_count, 7)
    self.assertEqual(config.rss.jitter_seconds, 0)
    self.assertFalse(config.native.should_process(got_update=True, pending_count=6))
    self.assertTrue(config.native.should_process(got_update=False, pending_count=7))

  def test_native_mode_can_trigger_only_on_rss_updates(self) -> None:
    environment = {
        "APP_MODE": "native_server",
        "NATIVE_TRIGGER_ON_UPDATE": "true",
        "NATIVE_TRIGGER_NEWS_COUNT": "",
    }
    with patch.dict(os.environ, environment, clear=True), patch("config.load_dotenv"):
      config = load_config()

    self.assertIsNone(config.native.trigger_news_count)
    self.assertTrue(config.native.should_process(got_update=True, pending_count=1))
    self.assertFalse(config.native.should_process(got_update=False, pending_count=10))

  def test_test_mode_can_be_selected_by_command_override(self) -> None:
    with patch.dict(os.environ, {}, clear=True), patch("config.load_dotenv"):
      config = load_config(mode_override="test")

    self.assertIs(config.mode, AppMode.TEST)

  def test_invalid_mode_has_actionable_error(self) -> None:
    with patch.dict(os.environ, {"APP_MODE": "unknown"}, clear=True), patch(
        "config.load_dotenv"
    ):
      with self.assertRaisesRegex(ValueError, "native_server, local_server, test"):
        load_config()


if __name__ == "__main__":
  unittest.main()
