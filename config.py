import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv


class AppMode(str, Enum):
  NATIVE_SERVER = "native_server"
  LOCAL_SERVER = "local_server"
  TEST = "test"


@dataclass(frozen=True)
class RSSConfig:
  url: str
  user_agent: str
  poll_seconds: int
  jitter_seconds: int
  output_csv: Path


@dataclass(frozen=True)
class NativeServerConfig:
  processing_csv: Path
  seen_urls_file: Path
  trigger_on_update: bool
  trigger_news_count: int | None

  def should_process(self, got_update: bool, pending_count: int) -> bool:
    return (
        self.trigger_on_update and got_update
    ) or (
        self.trigger_news_count is not None
        and pending_count >= self.trigger_news_count
    )


@dataclass(frozen=True)
class LocalServerConfig:
  source: str
  input_csv: Path
  processing_csv: Path
  ssh_host: str | None
  remote_csv: str
  remote_processing_csv: str


@dataclass(frozen=True)
class TestConfig:
  news_count: int


@dataclass(frozen=True)
class AppConfig:
  mode: AppMode
  rss: RSSConfig
  native: NativeServerConfig
  local: LocalServerConfig
  test: TestConfig
  ollama_host: str
  ollama_model: str
  ollama_api_key: str | None
  stock_data_csv: Path
  output_csv: Path
  cut_price: float
  price_lookup_delay: float


def _read_bool(name: str, default: bool) -> bool:
  value = os.getenv(name)
  if value is None:
    return default
  normalized = value.strip().lower()
  if normalized in {"1", "true", "yes", "on"}:
    return True
  if normalized in {"0", "false", "no", "off"}:
    return False
  raise ValueError(f"{name} must be a boolean value (true/false): {value}")


def _read_positive_int(name: str, default: int | None) -> int | None:
  value = os.getenv(name)
  if value is None:
    result = default
  elif not value.strip():
    result = None
  else:
    try:
      result = int(value)
    except ValueError as exc:
      raise ValueError(f"{name} must be a positive integer: {value}") from exc
  if result is not None and result < 1:
    raise ValueError(f"{name} must be at least 1")
  return result


def _read_nonnegative_int(name: str, default: int) -> int:
  value = os.getenv(name)
  try:
    result = int(value) if value is not None else default
  except ValueError as exc:
    raise ValueError(f"{name} must be a non-negative integer: {value}") from exc
  if result < 0:
    raise ValueError(f"{name} must be non-negative")
  return result


def load_config(mode_override: str | None = None) -> AppConfig:
  load_dotenv()
  raw_mode = mode_override or os.getenv("APP_MODE", AppMode.LOCAL_SERVER.value)
  try:
    mode = AppMode(raw_mode.strip().lower())
  except ValueError as exc:
    supported = ", ".join(item.value for item in AppMode)
    raise ValueError(f"APP_MODE must be one of: {supported}") from exc

  rss = RSSConfig(
      url=os.getenv(
          "RSS_URL", "https://www.nhk.or.jp/rss/news/cat5.xml"
      ).strip(),
      user_agent=os.getenv(
          "RSS_USER_AGENT", "NewsDataDir/1.0"
      ).strip(),
      poll_seconds=_read_positive_int("RSS_POLL_SECONDS", 900) or 900,
      jitter_seconds=_read_nonnegative_int("RSS_JITTER_SECONDS", 20),
      output_csv=Path(os.getenv("RSS_OUTPUT_CSV", "news_data.csv")),
  )

  trigger_news_count = _read_positive_int("NATIVE_TRIGGER_NEWS_COUNT", 10)
  native = NativeServerConfig(
      processing_csv=Path(
          os.getenv("NATIVE_PROCESSING_CSV", "processing_news_data.csv")
      ),
      seen_urls_file=Path(
          os.getenv("NATIVE_SEEN_URLS_FILE", ".news_seen_urls.txt")
      ),
      trigger_on_update=_read_bool("NATIVE_TRIGGER_ON_UPDATE", True),
      trigger_news_count=trigger_news_count,
  )
  if not native.trigger_on_update and native.trigger_news_count is None:
    raise ValueError(
        "Native server mode requires NATIVE_TRIGGER_ON_UPDATE=true "
        "or NATIVE_TRIGGER_NEWS_COUNT"
    )
  native_paths = {
      rss.output_csv.resolve(),
      native.processing_csv.resolve(),
      native.seen_urls_file.resolve(),
  }
  if len(native_paths) != 3:
    raise ValueError(
        "RSS_OUTPUT_CSV, NATIVE_PROCESSING_CSV, and "
        "NATIVE_SEEN_URLS_FILE must be different"
    )

  local_source = os.getenv("LOCAL_SERVER_SOURCE", "ssh").strip().lower()
  if local_source not in {"ssh", "local"}:
    raise ValueError("LOCAL_SERVER_SOURCE must be 'ssh' or 'local'")
  local = LocalServerConfig(
      source=local_source,
      input_csv=Path(
          os.getenv("LOCAL_SERVER_INPUT_CSV", "news_data.csv")
      ),
      processing_csv=Path(
          os.getenv("LOCAL_SERVER_PROCESSING_CSV", "processing_news_data.csv")
      ),
      ssh_host=os.getenv("LOCAL_SERVER_SSH_HOST"),
      remote_csv=os.getenv(
          "LOCAL_SERVER_REMOTE_CSV", "/mnt/usb1/news_data.csv"
      ),
      remote_processing_csv=os.getenv(
          "LOCAL_SERVER_REMOTE_PROCESSING_CSV", "/mnt/usb1/processing.csv"
      ),
  )
  if mode is AppMode.LOCAL_SERVER and local.source == "ssh" and not local.ssh_host:
    raise ValueError(
        "LOCAL_SERVER_SSH_HOST is required when LOCAL_SERVER_SOURCE=ssh"
    )
  if local.source == "local" and (
      local.input_csv.resolve() == local.processing_csv.resolve()
  ):
    raise ValueError(
        "LOCAL_SERVER_INPUT_CSV and LOCAL_SERVER_PROCESSING_CSV must be different"
    )

  test = TestConfig(
      news_count=_read_positive_int("TEST_RSS_NEWS_COUNT", 5) or 5
  )

  return AppConfig(
      mode=mode,
      rss=rss,
      native=native,
      local=local,
      test=test,
      ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434").strip(),
      ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:4b").strip(),
      ollama_api_key=os.getenv("OLLAMA_API_KEY") or None,
      stock_data_csv=Path(os.getenv("STOCK_DATA_CSV", "data_j.csv")),
      output_csv=Path(
          os.getenv("FILTERED_OUTPUT_CSV", "filtered_news_stocks.csv")
      ),
      cut_price=float(os.getenv("CUT_PRICE", "500")),
      price_lookup_delay=float(os.getenv("PRICE_LOOKUP_DELAY", "0.5")),
  )
