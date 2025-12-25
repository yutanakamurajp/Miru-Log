from __future__ import annotations

import json
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
Focus on observable actions only.
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
        image = Image.open(record.image_path)

        generation_config = {
            "max_output_tokens": self._settings.max_tokens,
            "temperature": self._settings.temperature,
        }

        response = self._model.generate_content([prompt, image], generation_config=generation_config)
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