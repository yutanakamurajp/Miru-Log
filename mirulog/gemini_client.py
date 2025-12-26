from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any, List

import google.generativeai as genai
from PIL import Image

from .config import GeminiSettings
from .models import AnalysisResult, CaptureRecord

PROMPT = """
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
"""


class GeminiAnalyzer:
    def __init__(self, settings: GeminiSettings, log):
        self._settings = settings
        self._logger = log
        genai.configure(api_key=settings.api_key)
        self._model = genai.GenerativeModel(settings.model)

    def analyze(self, record: CaptureRecord) -> AnalysisResult:
        if not record.image_path.exists():
            raise FileNotFoundError(record.image_path)

        prompt = f"{PROMPT}\nTimestamp: {record.captured_at.isoformat()}\nWindow: {record.window_title}\nApplication: {record.active_application}\n"
        generation_config = {
            "max_output_tokens": self._settings.max_tokens,
            "temperature": self._settings.temperature,
        }

        # Optional spacing to avoid bursting above per-minute quotas.
        if self._settings.request_spacing_seconds > 0:
            time.sleep(self._settings.request_spacing_seconds)

        response = self._generate_with_retry(
            prompt=prompt,
            image_path=record.image_path,
            generation_config=generation_config,
            max_retries=self._settings.max_retries,
            retry_buffer_seconds=self._settings.retry_buffer_seconds,
        )
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

    def _generate_with_retry(
        self,
        *,
        prompt: str,
        image_path: Path,
        generation_config: dict[str, Any],
        max_retries: int,
        retry_buffer_seconds: float,
    ):
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                with Image.open(image_path) as image:
                    return self._model.generate_content([prompt, image], generation_config=generation_config)
            except Exception as exc:
                last_exc = exc
                if not self._is_rate_limited(exc):
                    raise

                if attempt >= max_retries:
                    raise

                wait_seconds = self._compute_retry_wait_seconds(exc, attempt)
                wait_seconds = max(0.0, wait_seconds + max(0.0, retry_buffer_seconds))
                self._logger.warning(
                    "Gemini rate limit hit (attempt %s/%s). Waiting %.1fs then retrying...",
                    attempt + 1,
                    max_retries + 1,
                    wait_seconds,
                )
                time.sleep(wait_seconds)

        # Should be unreachable, but keeps type-checkers happy.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Gemini generate_content failed unexpectedly")

    def _is_rate_limited(self, exc: Exception) -> bool:
        # google.api_core.exceptions.ResourceExhausted maps to 429 in this context.
        try:
            from google.api_core.exceptions import ResourceExhausted

            if isinstance(exc, ResourceExhausted):
                return True
        except Exception:
            pass

        message = str(exc)
        return "429" in message or "Quota exceeded" in message or "rate limit" in message.lower()

    def _compute_retry_wait_seconds(self, exc: Exception, attempt: int) -> float:
        # Prefer server-suggested delay if present.
        message = str(exc)
        match = re.search(r"Please retry in\s+([0-9]+(?:\.[0-9]+)?)s", message)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass

        # Fallback: exponential backoff with jitter, capped.
        base = min(60.0, (2.0 ** attempt))
        jitter = random.uniform(0.0, 1.0)
        return base + jitter

    def _parse_payload(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            if "```" in cleaned:
                cleaned = cleaned.split("```", 1)[0]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            self._logger.warning("Failed to parse Gemini JSON. Keeping raw text.")
            return {}
