from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any


TASK_CHOICES = [
    "開発(コード)",
    "デバッグ/不具合対応",
    "テスト/ビルド",
    "レビュー/品質確認",
    "調査/検討",
    "ドキュメント/記録",
    "連絡/調整",
    "ミーティング",
    "計画/タスク管理",
    "環境/運用",
    "閲覧/学習",
    "事務/資料",
    "デザイン/図解",
    "休憩/雑務",
    "その他",
]

ANALYSIS_PROMPT = """
You are Miru-Log, a meticulous self-tracking assistant.
You receive a desktop screenshot and metadata about the active window.

Return exactly one JSON object. Do not use markdown, code fences, commentary, or explanations.

Required schema:
{
  "description": "日本語の短い1文。画面で確認できる作業内容だけを書く",
  "primary_task": "one of the allowed labels",
  "tags": ["日本語タグ1", "日本語タグ2"],
  "confidence": 0.0,
  "observed_files": ["見えているファイル名や相対パス"],
  "observed_repositories": ["見えているリポジトリ名やワークスペース名"],
  "observed_urls": ["見えているhttp(s) URL"]
}

Allowed primary_task values:
[
  "開発(コード)", "デバッグ/不具合対応", "テスト/ビルド", "レビュー/品質確認", "調査/検討",
  "ドキュメント/記録", "連絡/調整", "ミーティング", "計画/タスク管理", "環境/運用",
  "閲覧/学習", "事務/資料", "デザイン/図解", "休憩/雑務", "その他"
]

Rules:
- Values must be in Japanese, except literal file names, repository names, and URLs.
- description must be one short sentence, ideally <= 80 characters and at most 120 characters.
- Do not dump OCR text, logs, terminal output, JSON, or repeated fragments into description.
- Prefer concrete observable actions such as editing, reviewing, building, debugging, or reading.
- If VS Code, terminal, browser tabs, or window titles reveal a repository/workspace or file name, record them in observed_repositories and observed_files.
- observed_files / observed_repositories / observed_urls should contain only concrete items you can actually read.
- If an item is unclear, omit it. Use an empty array instead of guessing.
- Limit each observed_* array to at most 5 items.
- confidence must be a number between 0 and 1.
""".strip()


_FILE_TOKEN_RE = re.compile(
    r"\b[\w./\\-]+\.(?:py|md|txt|json|ya?ml|toml|ini|cfg|csv|ts|js|jsx|tsx|html|css|ps1|bat|cmd|sh|ipynb|env|exe|db|sql|log)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def parse_analysis_json(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fences(text)
    if not cleaned:
        return {}

    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass

    candidate = _extract_first_json_object(cleaned)
    if not candidate:
        return {}

    try:
        payload = json.loads(candidate)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def normalize_analysis_payload(payload: dict[str, Any], *, fallback_text: str = "") -> dict[str, Any]:
    files = _sanitize_list(payload.get("observed_files"), kind="file")
    repos = _sanitize_list(payload.get("observed_repositories"), kind="repo")
    urls = _sanitize_list(payload.get("observed_urls"), kind="url")
    tags = _sanitize_list(payload.get("tags"), kind="tag", max_items=8)

    description = _sanitize_description(
        payload.get("description"),
        fallback_text=fallback_text,
        observed_files=files,
        observed_repositories=repos,
    )

    primary_task = str(payload.get("primary_task") or "").strip()
    if primary_task not in TASK_CHOICES:
        primary_task = "その他"

    confidence = _clamp_confidence(payload.get("confidence", 0.6))

    return {
        "description": description,
        "primary_task": primary_task,
        "tags": tags,
        "confidence": confidence,
        "observed_files": files,
        "observed_repositories": repos,
        "observed_urls": urls,
    }


def payload_to_json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def extract_file_like_tokens(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(0) for m in _FILE_TOKEN_RE.finditer(text)]


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    return _URL_RE.findall(text)


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
    return cleaned.strip()


def _extract_first_json_object(text: str) -> str | None:
    start = -1
    depth = 0
    in_string = False
    escape = False

    for index, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:index + 1]
    return None


def _sanitize_description(
    value: Any,
    *,
    fallback_text: str,
    observed_files: list[str],
    observed_repositories: list[str],
) -> str:
    description = _normalize_text(_unwrap_nested_description(value))
    if not description or _looks_like_noise(description):
        description = _build_fallback_description(observed_files, observed_repositories, fallback_text)

    if len(description) > 120:
        description = description[:117].rstrip(" 　,、。.;:-") + "..."

    if not description.endswith(("。", ".")):
        description = description + "。"
    return description


def _unwrap_nested_description(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("description") or "")

    text = str(value or "").strip()
    if text.startswith("{") and '"description"' in text:
        nested = parse_analysis_json(text)
        if nested:
            return str(nested.get("description") or "")
    return text


def _build_fallback_description(observed_files: list[str], observed_repositories: list[str], fallback_text: str) -> str:
    if observed_repositories and observed_files:
        return f"{observed_repositories[0]} で {observed_files[0]} などを確認している"
    if observed_files:
        return f"{observed_files[0]} などを確認している"
    if observed_repositories:
        return f"{observed_repositories[0]} で作業内容を確認している"

    fallback = _normalize_text(fallback_text)
    if fallback and not _looks_like_noise(fallback):
        return fallback[:120]
    return "画面上の作業内容を確認している"


def _sanitize_list(value: Any, *, kind: str, max_items: int = 5) -> list[str]:
    if value is None:
        raw_items: list[Any] = []
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]

    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = _normalize_text(raw)
        if not text:
            continue

        if kind == "file":
            file_candidates = [text] if _FILE_TOKEN_RE.search(text) else extract_file_like_tokens(text)
            for candidate in file_candidates:
                candidate = _normalize_text(candidate)
                if _accept_item(candidate, kind=kind) and candidate not in seen:
                    seen.add(candidate)
                    items.append(candidate)
        elif kind == "url":
            url_candidates = [text] if text.startswith(("http://", "https://")) else extract_urls(text)
            for candidate in url_candidates:
                candidate = candidate.rstrip(').,]')
                if _accept_item(candidate, kind=kind) and candidate not in seen:
                    seen.add(candidate)
                    items.append(candidate)
        else:
            if _accept_item(text, kind=kind) and text not in seen:
                seen.add(text)
                items.append(text)

        if len(items) >= max_items:
            break

    return items[:max_items]


def _accept_item(text: str, *, kind: str) -> bool:
    if not text:
        return False
    if len(text) > 160:
        return False
    if text.count("{") or text.count("}"):
        return False
    if _looks_like_noise(text):
        return False
    if kind == "repo":
        return len(text) <= 80
    if kind == "url":
        return text.startswith(("http://", "https://"))
    return True


def _looks_like_noise(text: str) -> bool:
    lowered = text.lower()
    if len(text) > 220:
        return True
    if lowered.count("scanning") >= 3:
        return True
    if "based-based-based" in lowered:
        return True

    tokens = re.findall(r"[A-Za-z0-9_./:-]+|[一-龥ぁ-んァ-ン]+", text)
    if len(tokens) >= 12:
        counts = Counter(tokens)
        _, top_count = counts.most_common(1)[0]
        if top_count >= 6:
            return True
    return False


def _normalize_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("<br>", " ")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip(" \t\r\n\"'")


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.6
    if confidence < 0:
        return 0.0
    if confidence > 1:
        return 1.0
    return confidence