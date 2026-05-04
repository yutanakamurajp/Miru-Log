# Miru-Log

Miru-Log は Windows 上の操作画面を自動キャプチャし、Gemini で行動を解析、Markdown 形式の日報と（任意で）Nanobanana Pro の図解を生成するセルフトラッキングシステムです。本リポジトリには `AGENTS.md` で定義された 4 つのエージェント実装が含まれます。

## リポジトリ構成

```text
mirulog/            # 共有パッケージ（設定・ロギング・DB・外部 API との連携など）
observer.py         # キャプチャエージェント（pyautogui + pynput）
analyzer.py         # Gemini Vision 解析 + 画像ライフサイクル管理
summarizer.py       # Markdown / JSON の日報生成
notifier.py         # 日報のエクスポート + 図解生成（メール送信は不要）
requirements.txt    # Python 依存関係
.env.example        # 設定テンプレート
output/             # summarizer の生成物（Git 管理外）
reports/            # 最終的な `YYYYMMDD_Miru-Log.md` の配置先（Git 管理外）
```

> 注: notifier の出力ファイル名は `YYYYMMDD_Miru-Log.md` です。

## データ保存構造（新仕様）

各PCは独立したフォルダにデータを保存します：

```text
<実行ディレクトリ>/
├─ observer.py（または mirulog-observer.exe）
├─ .env
├─ DESKTOP-ABC123/          # PC名フォルダ（自動作成）
│  ├─ captures/YYYY-MM-DD/  # 未解析キャプチャ
│  ├─ archive/YYYY-MM-DD/   # 解析済みキャプチャ
│  ├─ logs/observer.log     # ログファイル
│  └─ mirulog.db            # SQLiteデータベース
└─ LAPTOP-XYZ789/           # 別PCのデータ
   └─ ...
```

スクリーンショットのメタデータと Gemini の解析結果は `<PC名>/mirulog.db`（SQLite）に保存され、画像ファイルは解析後に削除または日付別ディレクトリへ移動します。

## セットアップ手順

1. Windows 10/11 で Python 3.10 以上をインストールし、仮想環境を作成します。
2. `pip install -r requirements.txt`
3. `.env.example` を `.env` にコピーし、以下の値を設定します。
   - 解析バックエンド（`ANALYZER_BACKEND`）
   - Google Gemini の API キー（`ANALYZER_BACKEND=gemini` の場合のみ必須）
   - ローカル LLM（LM Studio）設定（`ANALYZER_BACKEND=local` の場合）
   - Nanobanana Pro の API キー（`ENABLE_VISUALIZATION=true` の場合のみ必須）
   - キャプチャ間隔、アイドル閾値、タイムゾーン、ログ保存先
   - `REPORT_EXPORT_DIR`（最終的な `YYYYMMDD_log.md` を配置したいフォルダ）
4. 端末にスクリーンショット権限とキーボード/マウス監視権限が付与されているアカウントで実行します。

> ログはデフォルトで `<実行ディレクトリ>/<PC名>/logs/` に出力されます。詳細解析が必要な場合は `.env` の `LOG_LEVEL` を `DEBUG` に切り替えてください。

## 実行フロー

1. `python observer.py`
   - pynput でグローバル入力を監視し、PC がアクティブかつロック解除状態のときのみ `CAPTURE_INTERVAL_SECONDS` ごとにスクリーンショットを保存します。
   - SQLite にウィンドウタイトル、前面プロセス、ハッシュなどのメタデータを記録します。

   **デフォルト動作**

   - 設定不要で `<実行ディレクトリ>/<PC名>/captures/` にキャプチャを保存
   - PC名は環境変数 `%COMPUTERNAME%` から自動取得

   **保存先を明示的に変更したい場合**

   - `.env` の `CAPTURE_ROOT` / `ARCHIVE_ROOT` / `LOG_DIR` を設定
       - 未設定の場合は上記デフォルト動作
       - 設定した場合は環境変数展開に対応（例: `D:/MiruLog/%COMPUTERNAME%/captures`）
   - または起動引数で上書き（例）:
     - `python observer.py --capture-root D:/MiruLog/captures --archive-root D:/MiruLog/archive`

   **特殊な環境変数**

   - `MIRULOG_ROOT`: アプリケーションルートを明示指定（通常は自動検出）
   - `MIRULOG_COMPUTERNAME`: PC名を明示指定（通常は自動取得）
   - `ANALYZER_DATA_ROOT`: Analyzer用データルート（複数PCの親フォルダ）

2. `python analyzer.py --limit 30`
   - 未解析のキャプチャを取得し、`ANALYZER_BACKEND` に応じて Gemini またはローカル LLM に画像と文脈（ウィンドウ情報）を送信します。
   - `--limit` は「1回のバッチで処理する件数」です（指定しない場合はバックエンドによりデフォルトが異なります）。
      - Gemini: デフォルト 20
      - Local（LM Studio）: デフォルトは実質上限なし（未解析がある限り取得）
   - `--until-empty` を付けると、未解析が空になるまでバッチ処理を繰り返します（例: `python analyzer.py --until-empty --limit 50`）。
   - 解析結果を DB に保存し、`DELETE_CAPTURE_AFTER_ANALYSIS` に応じて画像を削除または `data/archive/<date>/` へ移動します。

### 解析バックエンド切り替え（Gemini / ローカル LLM）

- **Gemini を使う（デフォルト）**
   - `.env`:
      - `ANALYZER_BACKEND=gemini`
      - `GEMINI_API_KEY=...`

- **LM Studio（ローカル LLM）を使う**
   - LM Studio を起動し、OpenAI互換サーバが `http://localhost:1234/v1` で動いている状態にします
   - `.env`:
      - `ANALYZER_BACKEND=local`
      - `LOCAL_LLM_BASE_URL=http://localhost:1234/v1`（未設定でもデフォルトでこの値）
      - `LOCAL_LLM_MODEL=auto`（未設定/auto の場合は `GET /v1/models` から自動選択）

> 画像入力に対応していないモデルだと、解析時にLM Studio側がエラーを返す場合があります。その場合は画像対応モデルに切り替えてください。

### Gemini の 429（クオータ/レート制限）対策

Gemini free tier 等で `429 Quota exceeded` が出る場合、以下を `.env` で調整できます。

- `GEMINI_MAX_RETRIES` / `GEMINI_RETRY_BUFFER_SECONDS`: サーバが提示する待ち時間に従ってリトライ
- `GEMINI_REQUEST_SPACING_SECONDS`: リクエスト間隔を固定で空けて、429自体を起こしにくくする
   - 例: 5 req/min の場合は `12` 秒程度

3. `python summarizer.py --date 2025-12-25`
   - 指定日の解析結果を集計し、タスク単位のセグメント化やブロッカー/フォローアップ抽出を行います。
   - `output/` 以下に `daily-report-YYYYMMDD.md` と `daily-report-YYYYMMDD.json` を生成します。
   - 解析の生レスポンス（画面に写っていた情報）から、作業中のファイル名・リポジトリ名・URL などを推定して日報に含めます（読めない場合は空になります）。

4. `python notifier.py --date 2025-12-25`
   - 上記 Markdown を `REPORT_EXPORT_DIR` にコピーし、`YYYYMMDD_Miru-Log.md` というファイル名で保存します。
   - `.env` で `ENABLE_VISUALIZATION=true` にしている場合のみ、Nanobanana Pro で図解 PNG（`YYYYMMDD_Miru-Log.png`）を出力します。
   - Google カレンダー連携が有効な場合、ログが存在する時間帯を「Miru-Log」イベントとしてカレンダーへ登録します（連続している時間帯は結合してイベント数を減らします）。

Windows タスク スケジューラを使えば、observer をログオン時に常駐させ、analyzer を定期実行、summarizer/notifier を深夜に実行するなどの完全自動化が可能です。

## トレイ常駐コントローラ

`tray.py` を起動すると、トレイから各エージェントの Start/Stop、ステータス確認、ログ/出力フォルダを開く操作ができます。

また「解析バックエンド（Gemini / Local）」をトレイ上で切り替えできます。切り替えは `.env` を書き換えず、トレイから `analyzer.py` を起動する時にだけ反映されます（次回起動から有効）。

トレイから `analyzer.py` を起動した場合は、未解析が空になるまで解析を回し切るモード（`--until-empty`）で動作し、状況（処理済み/残り/直近タスク）がステータス表示に反映されます。

> 注意: 現状の `tray.py` は Python スクリプト（`observer.py` など）を起動/停止する仕組みです。配布用の `dist/mirulog-observer.exe` をトレイから操作する用途には対応していません。

### 手動起動

```text
python tray.py
```

## observer を EXE 化する（PyInstaller）

`observer` 機能だけを単体EXE化できます（Windows向け）。事前に `.venv` が必要です。

1) PyInstaller をインストール

`C:\Users\...\Miru-Log\.venv\Scripts\python.exe -m pip install pyinstaller`

2) ビルド

- コンソールあり（デバッグ向き）:

`powershell -ExecutionPolicy Bypass -File scripts/build_observer.ps1`

- コンソールなし（常駐向き）:

`powershell -ExecutionPolicy Bypass -File scripts/build_observer.ps1 -NoConsole`

出力は `dist/` 配下に生成されます（例: `dist/mirulog-observer.exe`）。

### 配布するもの（observer のみ）

- `dist/mirulog-observer.exe`
- `dist/.env`（保存先/間隔などの設定）
- （任意）ショートカット `.lnk`

`scripts/build_observer.ps1` はビルド時に `dist/` を作り直しますが、`dist/.env` と `dist/*.lnk` は保持（無い場合は `scripts/observer.env` から `dist/.env` を生成）するようになっています。

### 配布用 `.env` のテンプレ

- `scripts/observer.env` は observer 配布向けの最小構成テンプレです。
- **v2.0以降のデフォルト動作**: `.env` 未設定でも自動的に `<EXE配置場所>/<PC名>/` 配下にデータを保存
- 明示的にパスを設定したい場合のみ `.env` で `CAPTURE_ROOT` / `ARCHIVE_ROOT` を指定

**複数PC運用の推奨構成**:

共有フォルダに `mirulog-observer.exe` を配置するだけで、各PCが自動的に独立したフォルダを作成：

```
\\server\share\mirulog\
├─ mirulog-observer.exe
├─ DESKTOP-ABC123/
│  ├─ captures/
│  ├─ archive/
│  └─ mirulog.db
└─ LAPTOP-XYZ789/
   └─ ...
```

これにより、SQLite の DB を共有せず、衝突を回避できます。

### 複数PC運用（キャプチャのみ）

複数 PC で行うのが **キャプチャ（observer）だけ** の場合は、以下の運用が安全です。

- 各PC: 共有フォルダに配置した `mirulog-observer.exe` を常駐させる
   - `.env` 設定不要。自動的に `<共有フォルダ>/<PC名>/` にデータ保存
   - DB は `<共有フォルダ>/<PC名>/mirulog.db` となり、PC 間で SQLite を共有しない
- 解析/日報生成: 1台のPCでまとめて実行（同じDBに複数端末から同時に書き込まない）

**解析をまとめて行う方法**

集約PCで `ANALYZER_DATA_ROOT` を共有フォルダのルートに設定すれば、全PCのデータを自動検出して解析：

```powershell
# 環境変数で設定
$env:ANALYZER_DATA_ROOT='\\server\share\mirulog'
python analyzer.py --until-empty

# または .env に記載
# ANALYZER_DATA_ROOT=\\server\share\mirulog
```

Windows のバッチで実行する場合は、[scripts/run_analyzer_aggregator.bat](scripts/run_analyzer_aggregator.bat) を使えます。

```bat
scripts\run_analyzer_aggregator.bat
scripts\run_analyzer_aggregator.bat \\server\share\mirulog --limit 50
```

第1引数に共有ルートを渡すと、その実行だけ `ANALYZER_DATA_ROOT` を上書きします。第1引数を省略した場合は `.env` の `ANALYZER_DATA_ROOT` を使い、残りの引数はそのまま `analyzer.py` に渡します。

解析から日報生成までを集約PCで回す場合は、[scripts/run_pipeline_aggregator.bat](scripts/run_pipeline_aggregator.bat) を使えます。

```bat
scripts\run_pipeline_aggregator.bat
scripts\run_pipeline_aggregator.bat \\server\share\mirulog --date 2026-05-04
scripts\run_pipeline_aggregator.bat \\server\share\mirulog --with-notify --date 2026-05-04
```

このバッチは既定で `pipeline.py --until-empty --skip-notify` を実行します。つまり、未解析キャプチャを空になるまで解析し、その日の日報生成までを行います。通知も含めたい場合だけ `--with-notify` を付けてください。

集約PC用の `.env` サンプルは [scripts/aggregator.env](scripts/aggregator.env) に置いてあります。共有ルート、LLM バックエンド、出力先を調整して `.env` として使ってください。

集約PCで日次実行をタスクスケジューラに登録する場合は、[scripts/register_aggregator_pipeline_task.ps1](scripts/register_aggregator_pipeline_task.ps1) を使えます。

```powershell
# 毎日 23:55 に解析+日報生成（通知なし）
powershell -ExecutionPolicy Bypass -File scripts/register_aggregator_pipeline_task.ps1 `
   -DataRoot "\\server\share\mirulog" `
   -DailyTime "23:55"

# 毎日 23:55 に解析+日報生成+通知
powershell -ExecutionPolicy Bypass -File scripts/register_aggregator_pipeline_task.ps1 `
   -DataRoot "\\server\share\mirulog" `
   -DailyTime "23:55" `
   -WithNotify

# 削除
powershell -ExecutionPolicy Bypass -File scripts/register_aggregator_pipeline_task.ps1 -Delete
```

`-DataRoot` を省略した場合は `.env` の `ANALYZER_DATA_ROOT` を使います。追加の引数は `-PipelineArgs @('--limit','50')` のように `pipeline.py` へ渡せます。

analyzer は `ANALYZER_DATA_ROOT` 直下の各PC名フォルダ（`<PC名>/mirulog.db` が存在するフォルダ）を自動検出し、順番に解析します。

> 重要: 集約PCで別PCのスクリーンショット画像を解析するには、集約PCから画像ファイルのパスにアクセスできる必要があります。
> そのため、複数PC運用では共有ストレージ（UNCパス）に `mirulog-observer.exe` を配置するのが安全です。

> 注意: 同一 `mirulog.db` を複数PCで同時に更新する運用（DB共有）は避けてください。キャプチャのみであっても、DB が共有される設定だとロック/破損の原因になります。

**日報（md）を全PCぶん1本にまとめる（デフォルト）**

集約PCでは `.env`（または実行時の環境変数）で `ANALYZER_DATA_ROOT` を設定して `summarizer.py` / `notifier.py` を実行します。

- 例:
   - `$env:ANALYZER_DATA_ROOT='\\server\share\mirulog' ; python summarizer.py --date 2025-12-31`
   - `$env:ANALYZER_DATA_ROOT='\\server\share\mirulog' ; python notifier.py --date 2025-12-31`

このとき `ANALYZER_DATA_ROOT` 直下の各PC名フォルダ内の `mirulog.db` を自動検出し、全PCぶんの解析結果を時系列に統合した **1本の md** を出力します。

#### キャプチャされない（"session is locked" が出る）場合

`dist/logs/observer.log` に `Skipping capture: session is locked` が出続ける場合は、Windows の環境によってロック判定が誤検知することがあります。

回避策として、配布用 `.env` に以下を追加するとロック判定を無効化できます（ロック画面でも動いてしまう点は理解した上で使用してください）。

- `MIRULOG_DISABLE_LOCK_CHECK=true`

### ショートカット（作業フォルダー=dist）を作る

`dist/.env` を確実に使うには、ショートカットの「作業フォルダー（Start in）」が `dist` になっているのが重要です。

- 生成（デフォルトはデスクトップに `MiruLog Observer.lnk`）:

`powershell -ExecutionPolicy Bypass -File scripts/create_observer_shortcut.ps1`

### EXE 実行時に保存先を変える

`dist/mirulog-observer.exe --capture-root D:/MiruLog/captures --archive-root D:/MiruLog/archive`

### タスクスケジューラでログオン時に自動起動

PowerShell から以下を実行します（`pythonw.exe` のパスは環境に合わせて変更）。

```powershell
schtasks /Create /TN "Miru-Log Tray" /SC ONLOGON /RL LIMITED /F /IT `
  /RU "$env:USERNAME" `
  /TR "\"C:\Users\nakamura\Dropbox\Repository\Miru-Log\.venv\Scripts\pythonw.exe\" \"C:\Users\nakamura\Dropbox\Repository\Miru-Log\tray.py\""
```

削除する場合:

```powershell
schtasks /Delete /TN "Miru-Log Tray" /F
```

## プライバシーと運用上の注意

- `data/captures/` と `output/`, `reports/` は Git から除外済みです。解析後に画像を即削除するか、短期アーカイブするかは `.env` で切り替えられます。
- セッションロック検知とアイドル閾値により、ユーザーが不在の間はキャプチャが停止し CPU / ストレージ消費を抑制します。
- API キーはすべて `.env` から読み込み、リポジトリには含めません。
- Google カレンダー連携の OAuth 認証情報（`credentials.json`）とトークン（`token.pickle`）は機密情報なので Git に含めません（.gitignore 済み）。
- ログは `logs/observer.log` などファイルごとに分かれているため、トラブル発生時の追跡が容易です。

## Google カレンダー連携（notifier）

`notifier.py` は、日報エクスポートに加えて Google カレンダーへ「活動があった時間帯」を「Miru-Log」イベントとして登録できます。

### 事前準備

- Google Cloud Console で Google Calendar API を有効化
- OAuth 同意画面を設定
- OAuth 2.0 クライアント ID を作成（種類は **「デスクトップアプリ」**）
- 認証情報 JSON をリポジトリ直下に配置: `credentials.json`

> テストモードのまま使う場合は、OAuth 同意画面の「テストユーザー」に自分の Google アカウントを追加してください。

### 設定（任意）

- `GOOGLE_CALENDAR_ID`
   - 未指定: `primary`
   - 共有カレンダー等に出したい場合はカレンダー ID を指定

### 実行

`python notifier.py --date 2025-12-31`

- 初回は OAuth 認証が走り、トークンが `token.pickle` に保存されます。
- 環境によって `localhost` へのリダイレクトが使えない場合は、コンソール認証（コード貼り付け）にフォールバックします。

## トラブルシューティング

- Gemini / Nanobanana の呼び出し失敗時は `logs/analyzer.log` や `logs/notifier.log` を確認してください。
- 過去日のレポートを再生成する際は、該当日の `output/daily-report-*` を削除し、`summarizer.py` と `notifier.py` を再実行します。
- 指定日のレポートが「解析結果なし」になる場合、その日のキャプチャが未解析の可能性があります。まず `analyzer.py` を実行して解析を完了させてください（例: `python analyzer.py --until-empty`）。
- SQLite のスキーマは `mirulog/storage.py` に記載されています。`<PC名>/mirulog.db` を SQLite ビューアで直接確認することも可能です。

## 今後の拡張アイデア

- Slack / Teams などへの Webhook 通知を追加し、日報を自動共有する。
- 週次・月次レポート向けに分類タグやチーム別フィルターをスキーマへ拡張する。
- Nanobanana 用プロンプトをタスク種別ごとにチューニングし、常に一定品質の図解を得られるようにする。
