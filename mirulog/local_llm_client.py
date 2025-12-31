from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import LocalLLMSettings
from .models import AnalysisResult, CaptureRecord

SYSTEM_PROMPT = """
You are Miru-Log, a meticulous self-tracking assistant. You receive desktop screenshots and contextual metadata.
Analyze what the user was doing. Respond strictly as compact JSON with keys:
  - description: 1 sentence summary of the activity.
  - primary_task: concise task label (<=6 words).
  - tags: array of activity tags/keywords.
  - confidence: float between 0 and 1 reflecting your certainty.
    - observed_files: array of file paths/names you can read from the screenshot (if any).
    - observed_repositories: array of repository/workspace names you can read from the screenshot (if any).
    - observed_urls: array of http(s) URLs you can read from the screenshot (if any).
All values must be written in Japanese. The JSON keys must remain in English as listed above.
Focus on observable actions only.
If you cannot confidently read items, return empty arrays for those keys.
""".strip()


@dataclass(frozen=True)
class _OpenAIChatResponse:
    text: str


def _rdp_hint(window_title: str | None, process_name: str | None) -> str:
    title = (window_title or "").lower()
    proc = (process_name or "").lower()
    is_rdp = any(k in title for k in ["リモート デスクトップ", "remote desktop", "rdp", "mstsc", "msrdc"]) or proc in {
        "mstsc.exe",
        "msrdc.exe",
        "remotedesktop.exe",
    }
    if not is_rdp:
        return ""
    return (
        "\n"
        "IMPORTANT (RDP): If this screenshot is from Remote Desktop, do NOT summarize as just 'using remote desktop'. "
        "Describe what is happening inside the remote session (apps, code, browser, docs, errors) based on what you see. "
        "Only mention RDP as a note if you cannot infer the actual work.\n"
    )


class LocalLLMAnalyzer:
    """Analyzer using an OpenAI-compatible HTTP API (e.g., LM Studio).

    Expected base URL: http://localhost:1234/v1
    Endpoint used:     POST {base_url}/chat/completions
    """

    def __init__(self, settings: LocalLLMSettings, log):
        self._settings = settings
        self._logger = log
        self._model = self._resolve_model(settings)

    def analyze(self, record: CaptureRecord) -> AnalysisResult:
        if not record.image_path.exists():
            raise FileNotFoundError(record.image_path)

        user_text = (
            f"Timestamp: {record.captured_at.isoformat()}\n"
            f"Window: {record.window_title}\n"
            f"Application: {record.active_application}\n"
        )

        response = self._chat_with_image(system=SYSTEM_PROMPT, user_text=user_text, image_path=record.image_path)
        text = response.text or "{}"
        payload = self._parse_payload(text)

        description = payload.get("description") or text.strip()
        primary_task = payload.get("primary_task") or "Unclassified"
        tags = payload.get("tags") or []
        confidence = float(payload.get("confidence", 0.6))

        return AnalysisResult(
            capture_id=record.id or -1,
            description=description,
            primary_task=primary_task,
            confidence=confidence,
            tags=[str(tag) for tag in tags],
            raw_response=text,
        )

    def _chat_with_image(self, *, system: str, user_text: str, image_path: Path) -> _OpenAIChatResponse:
        url = f"{self._settings.base_url}/chat/completions"

        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"

        image_data_url = self._image_as_data_url(image_path)

        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": self._settings.temperature,
            "max_tokens": self._settings.max_tokens,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
        }

        # Ask for strict JSON if the server supports OpenAI-compatible response_format.
        # Some servers reject unknown fields; we will fallback gracefully.
        payload_with_format = dict(payload)
        payload_with_format["response_format"] = {"type": "json_object"}

        res = self._post_with_fallback(url=url, headers=headers, primary=payload_with_format, fallback=payload)

        if res.status_code >= 400:
            raise RuntimeError(f"Local LLM HTTP {res.status_code}: {res.text}")

        data = res.json()
        text = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")

        if isinstance(text, list):
            # Some servers may return structured content; join text chunks.
            parts = []
            for item in text:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
            text = "\n".join(p for p in parts if p)

        if not isinstance(text, str):
            text = "{}"

        return _OpenAIChatResponse(text=text)

    def _resolve_model(self, settings: LocalLLMSettings) -> str:
        # If explicitly configured, respect it.
        configured = (settings.model or "").strip()
        if configured and configured.lower() not in {"local-model", "auto"}:
            return configured

        # Auto-detect via OpenAI-compatible models endpoint.
        try:
            url = f"{settings.base_url.rstrip('/')}/models"
            res = requests.get(url, timeout=min(10.0, settings.timeout_seconds))
            if res.status_code >= 400:
                self._logger.warning("Local LLM models discovery failed (HTTP %s)", res.status_code)
                return configured or "local-model"

            data = res.json()
            models = data.get("data")
            if isinstance(models, list) and models:
                first = models[0]
                if isinstance(first, dict) and first.get("id"):
                    model_id = str(first["id"])
                    self._logger.info("Auto-selected LOCAL_LLM_MODEL=%s", model_id)
                    return model_id
        except Exception as exc:
            self._logger.warning("Local LLM models discovery failed: %s", exc)

        return configured or "local-model"

    def _post_with_fallback(
        self,
        *,
        url: str,
        headers: dict[str, str],
        primary: dict[str, Any],
        fallback: dict[str, Any],
    ) -> requests.Response:
        try:
            res = requests.post(url, headers=headers, json=primary, timeout=self._settings.timeout_seconds)
        except requests.RequestException as exc:
            raise RuntimeError(f"Local LLM request failed: {exc}") from exc

        if res.status_code in {400, 422}:
            # Likely unsupported field(s) (e.g., response_format). Retry once without extras.
            try:
                res2 = requests.post(url, headers=headers, json=fallback, timeout=self._settings.timeout_seconds)
                return res2
            except requests.RequestException as exc:
                raise RuntimeError(f"Local LLM request failed: {exc}") from exc

        return res

    def _image_as_data_url(self, image_path: Path) -> str:
        raw = image_path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        # LM Studio typically accepts data URLs for image_url.
        return f"data:image/png;base64,{encoded}"

    def _parse_payload(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            if "```" in cleaned:
                cleaned = cleaned.split("```", 1)[0]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to salvage the first JSON object contained in the output.
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                candidate = match.group(0)
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

            self._logger.warning("Failed to parse Local LLM JSON. Keeping raw text.")
            return {}

    def _build_prompt(self, record):  # 既存のプロンプト組み立て関数名に合わせてください
        window_title = getattr(record, "window_title", None)
        process_name = getattr(record, "process_name", None) or getattr(record, "process", None)

        prompt = (
            SYSTEM_PROMPT
            + _rdp_hint(window_title, process_name)
        )
        return prompt
