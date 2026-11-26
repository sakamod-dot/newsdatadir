import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from config import load_config
from main import RunStats, print_run_summary


class RunStatsTests(unittest.TestCase):
  def test_summary_displays_fetched_analyzed_and_registered_counts(self) -> None:
    stats = RunStats()
    stats.add_fetched(12)
    stats.add_analyzed()
    stats.add_registered()
    with patch.dict(
        os.environ, {"FILTERED_OUTPUT_CSV": "filtered_news_stocks.csv"}, clear=True
    ), patch("config.load_dotenv"):
      config = load_config(mode_override="test")
    output = io.StringIO()

    with redirect_stdout(output):
      print_run_summary(config, stats)

    self.assertIn("取得したニュース: 12件", output.getvalue())
    self.assertIn("分析したニュース: 1件", output.getvalue())
    self.assertIn("登録したニュース: 1件", output.getvalue())


if __name__ == "__main__":
  unittest.main()
