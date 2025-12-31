# Miru-Log

Miru-Log は Windows 上の操作画面を自動キャプチャし、Gemini で行動を解析、Markdown 形式の日報と（任意で）Nanobanana Pro の図解を生成するセルフトラッキングシステムです。本リポジトリには `AGENTS.md` で定義された 4 つのエージェント実装が含まれます。

## リポジトリ構成

```
mirulog/            # 共有パッケージ（設定・ロギング・DB・外部 API との連携など）
observer.py         # キャプチャエージェント（pyautogui + pynput）
analyzer.py         # Gemini Vision 解析 + 画像ライフサイクル管理
summarizer.py       # Markdown / JSON の日報生成
notifier.py         # 日報のエクスポート + 図解生成（メール送信は不要）
requirements.txt    # Python 依存関係
.env.example        # 設定テンプレート
data/               # ランタイムデータ（Git 管理外）
logs/               # 各エージェントのローテーションログ
output/             # summarizer の生成物（Git 管理外）
reports/            # 最終的な `YYYYMMDD_log.md` の配置先（Git 管理外）
```

スクリーンショットのメタデータと Gemini の解析結果は `data/archive/mirulog.db`（SQLite）に保存され、画像ファイルは解析後に削除または日付別ディレクトリへ移動します。

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

> すべてのログは `logs/` に出力されます。詳細解析が必要な場合は `.env` の `LOG_LEVEL` を `DEBUG` に切り替えてください。

## 実行フロー

1. `python observer.py`
   - pynput でグローバル入力を監視し、PC がアクティブかつロック解除状態のときのみ `CAPTURE_INTERVAL_SECONDS` ごとにスクリーンショットを保存します。
   - SQLite にウィンドウタイトル、前面プロセス、ハッシュなどのメタデータを記録します。

   **キャプチャ保存先を変更したい場合**

   - `.env` の `CAPTURE_ROOT` / `ARCHIVE_ROOT` を変更
       - `.env` 内のパスは環境変数展開に対応しています（例: `data/archive/%COMPUTERNAME%`）
   - または起動引数で上書き（例）:
     - `python observer.py --capture-root D:/MiruLog/captures --archive-root D:/MiruLog/archive`

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
   - `output/` 以下に `daily-report-YYYY-MM-DD.md` と `daily-report-YYYY-MM-DD.json` を生成します。
   - 解析の生レスポンス（画面に写っていた情報）から、作業中のファイル名・リポジトリ名・URL などを推定して日報に含めます（読めない場合は空になります）。

4. `python notifier.py --date 2025-12-25`
   - 上記 Markdown を `REPORT_EXPORT_DIR` にコピーし、`YYYYMMDD_log.md` というファイル名で保存します。
   - `.env` で `ENABLE_VISUALIZATION=true` にしている場合のみ、Nanobanana Pro で図解 PNG（`YYYYMMDD_log.png`）を出力します。

Windows タスク スケジューラを使えば、observer をログオン時に常駐させ、analyzer を定期実行、summarizer/notifier を深夜に実行するなどの完全自動化が可能です。

## トレイ常駐コントローラ

`tray.py` を起動すると、トレイから各エージェントの Start/Stop、ステータス確認、ログ/出力フォルダを開く操作ができます。

また「解析バックエンド（Gemini / Local）」をトレイ上で切り替えできます。切り替えは `.env` を書き換えず、トレイから `analyzer.py` を起動する時にだけ反映されます（次回起動から有効）。

トレイから `analyzer.py` を起動した場合は、未解析が空になるまで解析を回し切るモード（`--until-empty`）で動作し、状況（処理済み/残り/直近タスク）がステータス表示に反映されます。

> 注意: 現状の `tray.py` は Python スクリプト（`observer.py` など）を起動/停止する仕組みです。配布用の `dist/mirulog-observer.exe` をトレイから操作する用途には対応していません。

### 手動起動

```
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
- 既定で PC 名ごとに保存先を分けるため、`%COMPUTERNAME%` を使っています。

例:

- `CAPTURE_ROOT=data/captures/%COMPUTERNAME%`
- `ARCHIVE_ROOT=data/archive/%COMPUTERNAME%`

これにより、複数 PC で同じ共有フォルダ配下へ保存しても、衝突しにくくなります（SQLite の DB を 1つに共有して同時書き込みするのは避けてください）。

### 複数PC運用（キャプチャのみ）

複数 PC で行うのが **キャプチャ（observer）だけ** の場合は、以下の運用が安全です。

- 各PC: `dist/mirulog-observer.exe` を常駐させ、`CAPTURE_ROOT` / `ARCHIVE_ROOT` を `%COMPUTERNAME%` 付きで PC 別に分離
   - 例: `ARCHIVE_ROOT=\\server\share\mirulog\archive\%COMPUTERNAME%`
   - これにより、DB は `.../<PC名>/mirulog.db` となり、PC 間で SQLite を共有しません
- 解析/日報生成: 1台のPCでまとめて実行（同じDBに複数端末から同時に書き込まない）

**解析をまとめて行う方法（例）**

- 最も簡単: `ARCHIVE_ROOT` を切り替えて PC ごとに `analyzer.py` を実行
   - PowerShell 例:
      - `$env:ARCHIVE_ROOT='\\server\share\mirulog\archive\DESKTOP-AAAAAAA' ; python analyzer.py --until-empty`
      - `$env:ARCHIVE_ROOT='\\server\share\mirulog\archive\DESKTOP-BBBBBBB' ; python analyzer.py --until-empty`
- PC名フォルダを自動で走査して順番に回す: `scripts/run_analyzer_all_pcs.ps1`
   - 例:
      - 解析実行: `powershell -ExecutionPolicy Bypass -File scripts/run_analyzer_all_pcs.ps1 -ArchiveRootParent "\\server\share\mirulog\archive" -Mode analyze -Limit 50 -UntilEmpty true`
      - 未解析件数の一覧表示のみ（解析は実行しない）: `powershell -ExecutionPolicy Bypass -File scripts/run_analyzer_all_pcs.ps1 -ArchiveRootParent "\\server\share\mirulog\archive" -Mode list`

> 重要: 集約PCで別PCのスクリーンショット画像を解析するには、集約PCから画像ファイルのパスにアクセスできる必要があります。
> そのため、複数PC運用では `CAPTURE_ROOT` / `ARCHIVE_ROOT` を共有ストレージ上（UNCパスなど）に置くのが安全です。
>
> `run_analyzer_all_pcs.ps1` で画像パスも合わせて切り替える場合は `-CaptureRootParent` を使えます（`<captures>/<PC名>` を想定）。
> 例: `powershell -ExecutionPolicy Bypass -File scripts/run_analyzer_all_pcs.ps1 -ArchiveRootParent "\\server\share\mirulog\archive" -CaptureRootParent "\\server\share\mirulog\captures" -Mode analyze -Limit 50 -UntilEmpty true`
- `.env` を PC ごとに分ける場合は `MIRULOG_DOTENV` で切り替え可能
   - 例: `$env:MIRULOG_DOTENV='D:\MiruLog\envs\desktop-a.env' ; python analyzer.py --until-empty`

> 注意: 同一 `mirulog.db` を複数PCで同時に更新する運用（DB共有）は避けてください。キャプチャのみであっても、DB が共有される設定だとロック/破損の原因になります。

**日報（md）を全PCぶん1本にまとめる（デフォルト）**

集約PCでは `.env`（または実行時の環境変数）で `ARCHIVE_ROOT` を「PC名フォルダの親」に向けて `summarizer.py` / `notifier.py` を実行します。

- 例:
   - `$env:ARCHIVE_ROOT='\\server\share\mirulog\archive' ; python summarizer.py --date 2025-12-31`
   - `$env:ARCHIVE_ROOT='\\server\share\mirulog\archive' ; python notifier.py --date 2025-12-31`

このとき `ARCHIVE_ROOT` 直下の `*/mirulog.db` を自動検出し、全PCぶんの解析結果を時系列に統合した **1本の md** を出力します。

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
- ログは `logs/observer.log` などファイルごとに分かれているため、トラブル発生時の追跡が容易です。

## トラブルシューティング

- Gemini / Nanobanana の呼び出し失敗時は `logs/analyzer.log` や `logs/notifier.log` を確認してください。
- 過去日のレポートを再生成する際は、該当日の `output/daily-report-*` を削除し、`summarizer.py` と `notifier.py` を再実行します。
- 指定日のレポートが「解析結果なし」になる場合、その日のキャプチャが未解析の可能性があります。まず `analyzer.py` を実行して解析を完了させてください（例: `python analyzer.py --until-empty`）。
- SQLite のスキーマは `mirulog/storage.py` に記載されています。`data/archive/mirulog.db` を SQLite ビューアで直接確認することも可能です。

## 今後の拡張アイデア

- Slack / Teams などへの Webhook 通知を追加し、日報を自動共有する。
- 週次・月次レポート向けに分類タグやチーム別フィルターをスキーマへ拡張する。
- Nanobanana 用プロンプトをタスク種別ごとにチューニングし、常に一定品質の図解を得られるようにする。
