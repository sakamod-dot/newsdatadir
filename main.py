import argparse
import csv
import os
import random
import shlex
import subprocess
import threading
import time
import unicodedata
from pathlib import Path
from typing import Iterable

import ollama
import yfinance as yf
from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process

from config import AppConfig, AppMode, load_config
from rss_serv import (
    RSSPoller,
    append_entries,
    append_seen_urls,
    fetch_entries,
    load_seen_urls,
)


NEWS_OUTPUT_FIELDS = [
    "ニュースT",
    "ポジティブ判定",
    "銘柄名",
    "候補1",
    "候補1株価",
    "候補2",
    "候補2株価",
    "候補3",
    "候補3株価",
]


class RunStats:
  def __init__(self) -> None:
    self.fetched_news = 0
    self.analyzed_news = 0
    self.registered_news = 0
    self._lock = threading.Lock()

  def add_fetched(self, count: int) -> None:
    with self._lock:
      self.fetched_news += count

  def add_analyzed(self) -> None:
    with self._lock:
      self.analyzed_news += 1

  def add_registered(self) -> None:
    with self._lock:
      self.registered_news += 1

  def totals(self) -> tuple[int, int, int]:
    with self._lock:
      return self.fetched_news, self.analyzed_news, self.registered_news


def normalize(text: str) -> str:
  return unicodedata.normalize("NFKC", text).lower().strip()


class NewsFilterResult(BaseModel):
  is_corporate_news: bool = Field(description="企業に関するニュースか")
  company_name: str | None = Field(description="該当する企業名（なければnull）")
  is_positive: bool = Field(description="その企業にとってポジティブな内容か")


def load_stock_dict(csv_path: Path) -> dict[str, tuple[str, str]]:
  stocks = {}
  with csv_path.open(mode="r", encoding="shift-jis") as file:
    for row in csv.DictReader(file):
      raw_name = row["銘柄名"]
      code = row["コード"]
      stocks[normalize(raw_name)] = (raw_name, code)
  return stocks


def fuzzy_search_stock(
    query: str,
    stock_dict: dict[str, tuple[str, str]],
    limit: int = 3,
    score_cutoff: int = 80,
) -> list[tuple[str, str, float]]:
  norm_query = normalize(query)
  results = process.extract(
      norm_query,
      stock_dict.keys(),
      scorer=fuzz.WRatio,
      limit=limit,
      score_cutoff=score_cutoff,
  )
  return [
      (stock_dict[norm_name][0], stock_dict[norm_name][1], round(score, 1))
      for norm_name, score, _ in results
  ]


def create_ollama_client(config: AppConfig) -> ollama.Client:
  if config.ollama_api_key:
    return ollama.Client(
        host=config.ollama_host,
        headers={"Authorization": f"Bearer {config.ollama_api_key}"},
    )
  return ollama.Client(host=config.ollama_host)


def analyze_news(
    title: str,
    summary: str,
    client: ollama.Client,
    model: str,
) -> NewsFilterResult:
  summary_text = summary.strip() if summary else "（なし）"
  prompt = f"""以下のニュースから企業情報とポジティブ判定を抽出してください。
要約が「（なし）」の場合は、タイトルの情報のみで判定してください。

タイトル: {title}
要約: {summary_text}"""

  response = client.chat(
      model=model,
      messages=[{"role": "user", "content": prompt}],
      format=NewsFilterResult.model_json_schema(),
      think=False,
      options={
          "temperature": 0.0,
          "num_ctx": 2048,
          "num_predict": 300,
      },
  )
  return NewsFilterResult.model_validate_json(response["message"]["content"])


def get_current_price(code: str) -> float | None:
  try:
    ticker = yf.Ticker(f"{code}.T")
    df = ticker.history(period="1d", interval="1m")
    if not df.empty:
      return round(float(df["Close"].iloc[-1]), 1)

    df_daily = ticker.history(period="5d")
    if not df_daily.empty:
      return round(float(df_daily["Close"].iloc[-1]), 1)
  except Exception as exc:
    print(f"  [株価取得エラー: {code}] {exc}")
  return None


def process_news_rows(
    rows: Iterable[dict[str, str]],
    config: AppConfig,
    client: ollama.Client,
    stock_dict: dict[str, tuple[str, str]],
    stats: RunStats,
) -> None:
  output_csv = config.output_csv
  output_csv.parent.mkdir(parents=True, exist_ok=True)
  is_new_output = not output_csv.exists() or output_csv.stat().st_size == 0

  with output_csv.open(mode="a", encoding="utf-8-sig", newline="") as output_file:
    writer = csv.writer(output_file)
    if is_new_output:
      writer.writerow(NEWS_OUTPUT_FIELDS)

    for row in rows:
      title = row.get("title") or ""
      summary = row.get("summary") or ""
      if not title.strip():
        continue

      print("タイトル：", title)
      print("要約：", summary)
      print("分析開始します。")

      result = analyze_news(title, summary, client, config.ollama_model)
      stats.add_analyzed()
      print("分析結果：", result)

      if result.is_corporate_news and result.company_name:
        stocks = fuzzy_search_stock(result.company_name, stock_dict)
        print("該当銘柄：", stocks)

        if not stocks:
          print("-" * 20)
          continue

        candidates_data: list[str | float] = ["", "", "", "", "", ""]
        has_under_cutprice = False

        for index, (raw_name, code, _) in enumerate(stocks[:3]):
          price = get_current_price(code)
          if config.price_lookup_delay:
            time.sleep(config.price_lookup_delay)

          candidates_data[index * 2] = f"{raw_name}({code})"
          candidates_data[index * 2 + 1] = price if price is not None else ""
          print(f"  候補{index + 1}: {raw_name}({code}) -> 株価: {price}円")

          if price is not None and price <= config.cut_price:
            has_under_cutprice = True

        if has_under_cutprice:
          writer.writerow([
              title,
              result.is_positive,
              result.company_name,
              *candidates_data,
          ])
          stats.add_registered()
          print(
              f"  => ★条件一致（{config.cut_price}円以下あり）のため"
              "CSVに登録しました！"
          )

      print("-" * 20)

def process_news_file(
    input_csv: Path,
    config: AppConfig,
    client: ollama.Client,
    stock_dict: dict[str, tuple[str, str]],
    stats: RunStats,
) -> None:
  with input_csv.open(mode="r", encoding="utf-8-sig", newline="") as input_file:
    process_news_rows(
        csv.DictReader(input_file), config, client, stock_dict, stats
    )


def _remote_command(
    ssh_host: str, command: str
) -> subprocess.CompletedProcess[str]:
  result = subprocess.run(
      ["ssh", ssh_host, command],
      capture_output=True,
      text=True,
      check=False,
  )
  if result.returncode == 255:
    raise RuntimeError(f"SSH接続に失敗しました: {result.stderr.strip()}")
  return result


def fetch_and_rotate_csv(config: AppConfig) -> Path | None:
  local = config.local
  if local.source == "local":
    local.processing_csv.parent.mkdir(parents=True, exist_ok=True)
    if local.processing_csv.exists():
      return local.processing_csv
    if not local.input_csv.exists():
      return None
    os.replace(local.input_csv, local.processing_csv)
    return local.processing_csv

  if not local.ssh_host:
    raise ValueError("LOCAL_SERVER_SSH_HOST is required for SSH input")

  remote_processing = shlex.quote(local.remote_processing_csv)
  remote_input = shlex.quote(local.remote_csv)
  check_processing = _remote_command(
      local.ssh_host, f"test -f {remote_processing}"
  )
  if check_processing.returncode not in {0, 1}:
    raise RuntimeError(
        f"リモート処理ファイルを確認できません: "
        f"{check_processing.stderr.strip()}"
    )

  if check_processing.returncode == 1:
    check_input = _remote_command(local.ssh_host, f"test -f {remote_input}")
    if check_input.returncode == 1:
      return None
    if check_input.returncode != 0:
      raise RuntimeError(
          f"リモートCSVを確認できません: {check_input.stderr.strip()}"
      )
    move_result = _remote_command(
        local.ssh_host, f"mv -- {remote_input} {remote_processing}"
    )
    if move_result.returncode != 0:
      raise RuntimeError(f"リモートCSVを退避できません: {move_result.stderr.strip()}")

  local.processing_csv.parent.mkdir(parents=True, exist_ok=True)
  temporary_csv = local.processing_csv.with_name(
      f"{local.processing_csv.name}.part"
  )
  copy_result = subprocess.run(
      [
          "scp",
          f"{local.ssh_host}:{local.remote_processing_csv}",
          str(temporary_csv),
      ],
      capture_output=True,
      text=True,
      check=False,
  )
  if copy_result.returncode != 0:
    temporary_csv.unlink(missing_ok=True)
    raise RuntimeError(f"CSVダウンロードに失敗しました: {copy_result.stderr}")
  os.replace(temporary_csv, local.processing_csv)
  return local.processing_csv


def cleanup_local_batch(config: AppConfig) -> None:
  local = config.local
  if local.source == "ssh":
    if not local.ssh_host:
      raise ValueError("LOCAL_SERVER_SSH_HOST is required for SSH input")
    result = _remote_command(
        local.ssh_host,
        f"rm -f -- {shlex.quote(local.remote_processing_csv)}",
    )
    if result.returncode != 0:
      raise RuntimeError(f"リモートCSVを削除できません: {result.stderr.strip()}")
  local.processing_csv.unlink(missing_ok=True)


def run_local_server(
    config: AppConfig,
    client: ollama.Client,
    stock_dict: dict[str, tuple[str, str]],
    stats: RunStats,
) -> None:
  input_csv = fetch_and_rotate_csv(config)
  if input_csv is None:
    print("新規ニュースファイルがないため処理をスキップします。")
    return

  stats.add_fetched(_count_news_rows(input_csv))
  process_news_file(input_csv, config, client, stock_dict, stats)
  cleanup_local_batch(config)
  print("処理済みCSVをクリーンアップしました。")


def _count_news_rows(csv_path: Path) -> int:
  if not csv_path.exists():
    return 0
  with csv_path.open(mode="r", encoding="utf-8-sig", newline="") as file:
    return sum(1 for _ in csv.DictReader(file))


def run_native_server(
    config: AppConfig,
    client: ollama.Client,
    stock_dict: dict[str, tuple[str, str]],
    stats: RunStats,
) -> None:
  lock = threading.Lock()
  new_news_event = threading.Event()
  poller = RSSPoller(
      config.rss.url,
      config.rss.user_agent,
      seen_links=load_seen_urls(config.native.seen_urls_file),
  )

  def collect_forever() -> None:
    while True:
      try:
        entries = poller.poll()
        if entries:
          with lock:
            append_entries(config.rss.output_csv, entries)
            poller.remember(entries)
            stats.add_fetched(len(entries))
            new_news_event.set()
            append_seen_urls(config.native.seen_urls_file, entries)
            for entry in entries:
              print(f"[新規保存] {entry['title']}")
      except Exception as exc:
        print(f"[RSS取得エラー - 次回リトライします]: {exc}")

      sleep_seconds = config.rss.poll_seconds + random.uniform(
          -config.rss.jitter_seconds, config.rss.jitter_seconds
      )
      time.sleep(max(1, sleep_seconds))

  startup_count = _count_news_rows(
      config.rss.output_csv
  ) + _count_news_rows(config.native.processing_csv)
  stats.add_fetched(startup_count)
  if startup_count:
    new_news_event.set()

  collector_thread = threading.Thread(
      target=collect_forever, name="rss-collector", daemon=True
  )
  collector_thread.start()
  print(
      "ネイティブサーバーモードを開始しました。"
      f"（更新時実行: {config.native.trigger_on_update}, "
      f"新着件数: {config.native.trigger_news_count}）"
  )

  while True:
    got_update = new_news_event.wait(timeout=1)
    with lock:
      if got_update:
        new_news_event.clear()
      pending_count = _count_news_rows(config.rss.output_csv)
    if not config.native.processing_csv.exists() and not config.native.should_process(
        got_update, pending_count
    ):
      continue

    with lock:
      if not config.native.processing_csv.exists():
        if not config.rss.output_csv.exists():
          continue
        config.native.processing_csv.parent.mkdir(parents=True, exist_ok=True)
        os.replace(config.rss.output_csv, config.native.processing_csv)

    process_news_file(
        config.native.processing_csv, config, client, stock_dict, stats
    )
    config.native.processing_csv.unlink()


def run_test_mode(
    config: AppConfig,
    client: ollama.Client,
    stock_dict: dict[str, tuple[str, str]],
    stats: RunStats,
) -> None:
  entries = fetch_entries(
      config.rss.url,
      config.rss.user_agent,
      limit=config.test.news_count,
  )
  print(f"テストRSSから{len(entries)}件取得しました。")
  stats.add_fetched(len(entries))
  process_news_rows(entries, config, client, stock_dict, stats)
  print(f"結果を {config.output_csv} に保存しました。")


def print_run_summary(config: AppConfig, stats: RunStats) -> None:
  fetched, analyzed, registered = stats.totals()
  print("\n=== 実行結果 ===")
  print(f"モード: {config.mode.value}")
  print(f"取得したニュース: {fetched}件")
  print(f"分析したニュース: {analyzed}件")
  print(f"登録したニュース: {registered}件")
  print(f"結果ファイル: {config.output_csv}")


def main() -> None:
  parser = argparse.ArgumentParser(description="ニュース収集・推論パイプライン")
  parser.add_argument(
      "--mode",
      choices=[mode.value for mode in AppMode],
      help="APP_MODEの設定を一時的に上書きします。",
  )
  args = parser.parse_args()
  config = load_config(mode_override=args.mode)
  client = create_ollama_client(config)
  stock_dict = load_stock_dict(config.stock_data_csv)
  stats = RunStats()

  try:
    if config.mode is AppMode.NATIVE_SERVER:
      run_native_server(config, client, stock_dict, stats)
    elif config.mode is AppMode.LOCAL_SERVER:
      run_local_server(config, client, stock_dict, stats)
    else:
      run_test_mode(config, client, stock_dict, stats)
  except KeyboardInterrupt:
    print("\n停止要求を受け付けました。")
  finally:
    print_run_summary(config, stats)


if __name__ == "__main__":
  main()
