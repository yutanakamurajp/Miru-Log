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
   - Google Gemini の API キー（必須）
   - Nanobanana Pro の API キー（`ENABLE_VISUALIZATION=true` の場合のみ必須）
   - キャプチャ間隔、アイドル閾値、タイムゾーン、ログ保存先
   - `REPORT_EXPORT_DIR`（最終的な `YYYYMMDD_log.md` を配置したいフォルダ）
4. 端末にスクリーンショット権限とキーボード/マウス監視権限が付与されているアカウントで実行します。

> すべてのログは `logs/` に出力されます。詳細解析が必要な場合は `.env` の `LOG_LEVEL` を `DEBUG` に切り替えてください。

## 実行フロー

1. `python observer.py`
   - pynput でグローバル入力を監視し、PC がアクティブかつロック解除状態のときのみ `CAPTURE_INTERVAL_SECONDS` ごとにスクリーンショットを保存します。
   - SQLite にウィンドウタイトル、前面プロセス、ハッシュなどのメタデータを記録します。

2. `python analyzer.py --limit 30`
   - 未解析のキャプチャを取得し、Gemini Vision に画像と文脈（ウィンドウ情報）を送信します。
   - 解析結果を DB に保存し、`DELETE_CAPTURE_AFTER_ANALYSIS` に応じて画像を削除または `data/archive/<date>/` へ移動します。

3. `python summarizer.py --date 2025-12-25`
   - 指定日の解析結果を集計し、タスク単位のセグメント化やブロッカー/フォローアップ抽出を行います。
   - `output/` 以下に `daily-report-YYYY-MM-DD.md` と `daily-report-YYYY-MM-DD.json` を生成します。

4. `python notifier.py --date 2025-12-25`
   - 上記 Markdown を `REPORT_EXPORT_DIR` にコピーし、`YYYYMMDD_log.md` というファイル名で保存します。
   - `.env` で `ENABLE_VISUALIZATION=true` にしている場合のみ、Nanobanana Pro で図解 PNG（`YYYYMMDD_log.png`）を出力します。

Windows タスク スケジューラを使えば、observer をログオン時に常駐させ、analyzer を定期実行、summarizer/notifier を深夜に実行するなどの完全自動化が可能です。

## プライバシーと運用上の注意

- `data/captures/` と `output/`, `reports/` は Git から除外済みです。解析後に画像を即削除するか、短期アーカイブするかは `.env` で切り替えられます。
- セッションロック検知とアイドル閾値により、ユーザーが不在の間はキャプチャが停止し CPU / ストレージ消費を抑制します。
- API キーはすべて `.env` から読み込み、リポジトリには含めません。
- ログは `logs/observer.log` などファイルごとに分かれているため、トラブル発生時の追跡が容易です。

## トラブルシューティング

- Gemini / Nanobanana の呼び出し失敗時は `logs/analyzer.log` や `logs/notifier.log` を確認してください。
- 過去日のレポートを再生成する際は、該当日の `output/daily-report-*` を削除し、`summarizer.py` と `notifier.py` を再実行します。
- SQLite のスキーマは `mirulog/storage.py` に記載されています。`data/archive/mirulog.db` を SQLite ビューアで直接確認することも可能です。

## 今後の拡張アイデア

- Slack / Teams などへの Webhook 通知を追加し、日報を自動共有する。
- 週次・月次レポート向けに分類タグやチーム別フィルターをスキーマへ拡張する。
- Nanobanana 用プロンプトをタスク種別ごとにチューニングし、常に一定品質の図解を得られるようにする。