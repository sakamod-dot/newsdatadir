import csv
import random
import time
from _thread import LockType
from collections import deque
from pathlib import Path
from typing import Any, Iterable

import feedparser

from config import load_config


def _entry_to_row(entry: Any) -> dict[str, str]:
  return {
      "title": (entry.get("title") or "").replace("\n", " ").strip(),
      "summary": (entry.get("summary") or "").replace("\n", " ").strip(),
  }


def fetch_entries(
    url: str,
    user_agent: str,
    limit: int | None = None,
) -> list[dict[str, str]]:
  feed = feedparser.parse(url, agent=user_agent)
  if getattr(feed, "bozo", False) and not feed.entries:
    raise RuntimeError(f"RSS feed could not be parsed: {feed.bozo_exception}")

  entries = []
  for entry in feed.entries:
    link = entry.get("link", "")
    row = _entry_to_row(entry)
    if link and row["title"]:
      entries.append({"link": link, **row})
    if limit is not None and len(entries) >= limit:
      break
  return entries


class RSSPoller:
  def __init__(
      self,
      url: str,
      user_agent: str,
      max_seen: int = 1000,
      seen_links: Iterable[str] = (),
  ):
    self.url = url
    self.user_agent = user_agent
    self.max_seen = max_seen
    self.seen_links: deque[str] = deque()
    self.seen_link_set: set[str] = set()
    self.etag: str | None = None
    self.modified: time.struct_time | None = None
    self.remember({"link": link} for link in seen_links)

  def remember(self, entries: Iterable[dict[str, str]]) -> None:
    for entry in entries:
      link = entry.get("link", "")
      if not link or link in self.seen_link_set:
        continue
      if len(self.seen_links) >= self.max_seen:
        self.seen_link_set.discard(self.seen_links.popleft())
      self.seen_links.append(link)
      self.seen_link_set.add(link)

  def poll(self) -> list[dict[str, str]]:
    feed = feedparser.parse(
        self.url,
        agent=self.user_agent,
        etag=self.etag,
        modified=self.modified,
    )
    self.etag = feed.get("etag") or self.etag
    self.modified = feed.get("modified") or self.modified
    if getattr(feed, "bozo", False) and not feed.entries:
      raise RuntimeError(f"RSS feed could not be parsed: {feed.bozo_exception}")
    if getattr(feed, "status", 200) == 304:
      return []

    new_entries = []
    feed_links: set[str] = set()
    for entry in reversed(feed.entries):
      link = entry.get("link", "")
      if not link or link in self.seen_link_set or link in feed_links:
        continue
      feed_links.add(link)
      row = _entry_to_row(entry)
      if row["title"]:
        new_entries.append({"link": link, **row})
    return new_entries


def load_seen_urls(file_path: Path) -> list[str]:
    if not file_path.exists():
      return []
    with file_path.open("r", encoding="utf-8") as file:
      return [line.strip() for line in file if line.strip()]


def append_seen_urls(file_path: Path, entries: Iterable[dict[str, str]]) -> None:
    links = [entry["link"] for entry in entries if entry.get("link")]
    if not links:
      return
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8", newline="\n") as file:
      for link in links:
        file.write(f"{link}\n")


def append_entries(
    csv_path: Path,
    entries: list[dict[str, str]],
    lock: LockType | None = None,
) -> None:
  if not entries:
    return

  if lock is not None:
    lock.acquire()
  try:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_file = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", encoding="utf-8-sig", newline="") as file:
      writer = csv.DictWriter(file, fieldnames=["title", "summary"])
      if is_new_file:
        writer.writeheader()
      writer.writerows(
          {"title": entry["title"], "summary": entry["summary"]}
          for entry in entries
      )
  finally:
    if lock is not None:
      lock.release()


def run_collector() -> None:
  config = load_config(mode_override="native_server")
  poller = RSSPoller(
      config.rss.url,
      config.rss.user_agent,
      seen_links=load_seen_urls(config.native.seen_urls_file),
  )
  print(f"RSS監視を開始しました（保存先: {config.rss.output_csv}）")

  while True:
    try:
      entries = poller.poll()
      append_entries(config.rss.output_csv, entries)
      poller.remember(entries)
      append_seen_urls(config.native.seen_urls_file, entries)
      for entry in entries:
        print(f"[新規保存] {entry['title']}")
    except Exception as exc:
      print(f"[RSS取得エラー - 次回リトライします]: {exc}")

    sleep_seconds = config.rss.poll_seconds + random.uniform(
        -config.rss.jitter_seconds, config.rss.jitter_seconds
    )
    time.sleep(max(1, sleep_seconds))


if __name__ == "__main__":
  run_collector()
