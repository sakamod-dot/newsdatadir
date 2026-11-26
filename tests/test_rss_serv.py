import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rss_serv import RSSPoller, fetch_entries, load_seen_urls


class FakeFeed(dict):
  def __init__(self, entries: list[dict[str, str]]):
    super().__init__()
    self.entries = entries


class RSSServiceTests(unittest.TestCase):
  def test_fetch_entries_limits_the_number_of_news_items(self) -> None:
    feed = FakeFeed([
        {"link": "url-1", "title": "first", "summary": ""},
        {"link": "url-2", "title": "second", "summary": ""},
        {"link": "url-3", "title": "third", "summary": ""},
    ])
    with patch("rss_serv.feedparser.parse", return_value=feed):
      entries = fetch_entries("https://example.test/rss", "test-agent", limit=2)

    self.assertEqual([entry["link"] for entry in entries], ["url-1", "url-2"])

  def test_poller_deduplicates_items_across_restarts(self) -> None:
    feed = FakeFeed([
        {"link": "already-seen", "title": "old", "summary": ""},
        {"link": "new-link", "title": "new", "summary": ""},
    ])
    poller = RSSPoller(
        "https://example.test/rss", "test-agent", seen_links=["already-seen"]
    )
    with patch("rss_serv.feedparser.parse", return_value=feed):
      entries = poller.poll()
      poller.remember(entries)
      repeated_entries = poller.poll()

    self.assertEqual([entry["link"] for entry in entries], ["new-link"])
    self.assertEqual(repeated_entries, [])

  def test_seen_urls_are_loaded_from_persistent_state(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      seen_file = Path(directory) / "seen.txt"
      seen_file.write_text("first\n\nsecond\n", encoding="utf-8")

      self.assertEqual(load_seen_urls(seen_file), ["first", "second"])


if __name__ == "__main__":
  unittest.main()
