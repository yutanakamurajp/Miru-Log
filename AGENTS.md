# AGENTS.md (Miru-Log Project)

## 0. プロジェクト概要

**Miru-Log** は、WindowsユーザーのPC操作画面を1分おきにキャプチャし、Gemini APIを用いて活動内容を分析・要約、最終的に図解付きの日報を生成・通知する自動セルフ・トラッキング・システムである。

## 1. エージェントのミッション

あなたは「Miru-Log」のリードエンジニア兼プロダクトマネージャーとして、以下の目標を達成するためのコード生成、デバッグ、アーキテクチャ設計を行う。

* 低負荷かつプライバシーに配慮したキャプチャの実装。
* Gemini APIを最大限に活用した高精度な行動分析。
* ユーザーの手間をゼロにする完全自動化フローの構築。

## 2. 技術スタック (Core Stack)

* **Language:** Python 3.10+
* **OS:** Windows 10/11
* **Capture:** `pyautogui`, `Pillow`
* **Activity Detection:** `pynput` (マウス/キーボード監視)
* **LLM API:** Google Gemini API (Python SDK)
* **Visualization:** Nanobanana Pro (via Image Generation API)
* **Automation:** Windows Task Scheduler / Python `time.sleep`
* **Notification:** `smtplib` (Email)

## 3. 主要モジュール構成

1. **`observer.py`**: 操作中のみ1分間隔でSSを撮影。アイドル状態（5分以上無操作）時は停止。
2. **`analyzer.py`**: Gemini APIを呼び出し、画像から「何をしていたか」を言語化。
3. **`summarizer.py`**: 1日のログを統合し、日報テキストを生成し、markdown形式でまとめたファイルを出力する。
4. **`notifier.py`**: 図解画像と日報をメールで送信。

## 4. 開発・実装の原則

* **Privacy First**: 撮影した画像は解析後、速やかに削除またはアーカイブする設定にする。`.gitignore`に画像フォルダを必ず含める。
* **Efficiency**: 画面ロック中やスリープ中はリソースを消費しないよう、Windowsのセッション状態を考慮する。
* **Scalability**: 将来的にチーム共有機能（Slack/Notion連携など）を拡張しやすい構造にする。

## 5. 指示ガイドライン

コードを生成する際は、以下の点に注意せよ：

* Windows固有のパス（`C:\Users\...`）や実行権限に配慮したコードを書くこと。
* APIキーなどの機密情報は `.env` ファイルから読み込む形式を徹底すること。
* 実行ログを標準出力および `logs/` ディレクトリに詳細に残すこと。