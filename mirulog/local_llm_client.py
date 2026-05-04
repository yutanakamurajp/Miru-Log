from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from .analysis_utils import ANALYSIS_PROMPT, normalize_analysis_payload, parse_analysis_json, payload_to_json_text
from .config import LocalLLMSettings
from .models import AnalysisResult, CaptureRecord


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

        system_prompt = self._build_prompt(record)
        response = self._chat_with_image(system=system_prompt, user_text=user_text, image_path=record.image_path)
        text = response.text or "{}"
        payload = normalize_analysis_payload(parse_analysis_json(text), fallback_text=text)

        return AnalysisResult(
            capture_id=record.id or -1,
            description=payload["description"],
            primary_task=payload["primary_task"],
            confidence=payload["confidence"],
            tags=[str(tag) for tag in payload["tags"]],
            raw_response=payload_to_json_text(payload),
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
        """Read image, resize if necessary, and return as a base64 data URL."""
        max_size = 1024  # Max width or height
        
        with Image.open(image_path) as img:
            # Convert to RGB if it's RGBA or something else (some models dislike Alpha)
            if img.mode != "RGB":
                img = img.convert("RGB")
            
            # Resize if too large
            if max(img.width, img.height) > max_size:
                ratio = max_size / max(img.width, img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            # Save to buffer
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            raw = buffer.getvalue()

        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _parse_payload(self, text: str) -> dict[str, Any]:
        return parse_analysis_json(text)

    def _build_prompt(self, record):  # 既存のプロンプト組み立て関数名に合わせてください
        window_title = getattr(record, "window_title", None)
        process_name = getattr(record, "process_name", None) or getattr(record, "process", None)

        prompt = (
            ANALYSIS_PROMPT
            + _rdp_hint(window_title, process_name)
        )
        return prompt
