# NewsDataDir

RSSニュースをCSVに収集し、Ollamaで分析して条件に合うニュースを結果CSVへ保存するPythonアプリです。収集と推論を同じマシンで続ける構成、外部で収集して必要な時だけ推論する構成、RSSを使った一回のテスト実行を選べます。

## セットアップ

Conda環境を作成して有効化します。

```bash
conda env create -f environment.yml
conda activate newsdatadir
```

`.env.example`を参考に`.env`を作成し、利用環境に合わせて値を設定してください。`.env`は機密情報やマシン固有の設定を含むため、Gitにはコミットしないでください。

必要な共通設定:

- `OLLAMA_HOST`: OllamaサーバーのURL
- `OLLAMA_MODEL`: 使用するモデル名
- `OLLAMA_API_KEY`: 認証が必要な場合のAPIキー（不要なら空欄）
- `STOCK_DATA_CSV`: 銘柄一覧CSV。既定値は`data_j.csv`
- `FILTERED_OUTPUT_CSV`: 分析結果の出力先

## モードの選択

`.env`の`APP_MODE`、または起動時の`--mode`で切り替えます。コマンド引数は`.env`の設定より優先されます。

### ネイティブサーバー

```dotenv
APP_MODE=native_server
```

```bash
python main.py
```

RSSを継続取得し、新着があると自動で推論します。件数到達時だけ推論する場合は、例えば次のようにします。

```dotenv
NATIVE_TRIGGER_ON_UPDATE=false
NATIVE_TRIGGER_NEWS_COUNT=10
```

`RSS_POLL_SECONDS`で取得間隔を設定できます。処理済みRSS URLは`NATIVE_SEEN_URLS_FILE`に記録されます。終了時に**Ctrl+C**を押すと、その起動中の取得・分析・登録件数を表示します。このモードでは`main.py`自身がRSS収集も行うため、別途`rss_serv.py`を起動しないでください。

### ローカルサーバー

```dotenv
APP_MODE=local_server
LOCAL_SERVER_SOURCE=ssh
LOCAL_SERVER_SSH_HOST=user@news-server
LOCAL_SERVER_REMOTE_CSV=/path/on/server/news_data.csv
LOCAL_SERVER_REMOTE_PROCESSING_CSV=/path/on/server/processing.csv
```

```bash
python main.py
```

外部の収集マシンにあるCSVをSSH/SCPで取得し、一度推論します。`LOCAL_SERVER_SSH_HOST`には`user@ホスト名`または`user@IPアドレス`を設定し、SSH鍵で接続できるようにしてください。収集側ではRSSをCSVに保存するため、収集マシン上で次を継続実行します。

```bash
python rss_serv.py
```

ローカルにあるCSVを使う場合は、次のように設定し、`LOCAL_SERVER_INPUT_CSV`を入力ファイルに合わせます。

```dotenv
LOCAL_SERVER_SOURCE=local
LOCAL_SERVER_INPUT_CSV=news_data.csv
```

推論に成功すると、処理済みCSVをクリーンアップします。失敗時は処理ファイルを残し、データを失わないようにします。

### テスト

```bash
python main.py --mode test
```

`TEST_RSS_NEWS_COUNT`で指定した件数までRSSから取得し、一度推論します。設定を固定して使う場合は`.env`に`APP_MODE=test`を設定して`python main.py`を実行します。

## 実行結果

ローカルサーバーとテストモードは処理完了時、ネイティブサーバーはCtrl+Cによる終了時に、次の件数を表示します。

- 取得したニュース: RSSから取得した件数、または入力CSVの行数
- 分析したニュース: タイトルがあり、推論を実行した件数
- 登録したニュース: 条件に合い、結果CSVに追加した件数

結果は`FILTERED_OUTPUT_CSV`で指定したファイルに追記されます。
