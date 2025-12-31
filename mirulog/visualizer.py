from __future__ import annotations

import base64
from pathlib import Path
from typing import List

import requests

from .config import VisualizationSettings
from .models import DailySummary


class NanobananaClient:
    def __init__(self, settings: VisualizationSettings, log):
        self._settings = settings
        self._logger = log

    def render_summary(self, summary: DailySummary, output_path: Path) -> Path:
        if not self._settings.api_key:
            raise RuntimeError("Nanobanana API key is required to render a summary")

        payload = {
            "model": self._settings.model,
            "prompt": self._build_prompt(summary),
        }
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(self._settings.endpoint, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if "image" in (response.headers.get("Content-Type") or ""):
            output_path.write_bytes(response.content)
        else:
            data = response.json()
            b64_payload = data.get("image_base64")
            if not b64_payload:
                raise ValueError("Nanobanana response missing 'image_base64'")
            output_path.write_bytes(base64.b64decode(b64_payload))
        return output_path

    def _build_prompt(self, summary: DailySummary) -> str:
        lines: List[str] = [f"Date: {summary.date}"]
        lines.append(f"Total active minutes: {summary.total_active_minutes:.1f}")
        lines.append("Segments:")
        for segment in summary.segments:
            lines.append(
                f"- {segment.period_label}: {segment.dominant_task} ({segment.duration_minutes:.0f}m) -> {', '.join(segment.highlights)}"
            )
        if summary.blocking_issues:
            lines.append("Blockers: " + "; ".join(summary.blocking_issues))
        if summary.follow_ups:
            lines.append("Follow-ups: " + "; ".join(summary.follow_ups))
        lines.append("Generate a clean infographic summarizing the day.")
        return "\n".join(lines)