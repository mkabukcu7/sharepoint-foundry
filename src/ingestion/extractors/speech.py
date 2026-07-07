"""Audio/video transcription via Azure AI Speech batch transcription.

Produces time-stamped, speaker-attributed segments consumed by
``chunk_transcript``. SDK/HTTP calls are lazy. This reference uses the batch
transcription REST API contract; wire endpoints/credentials per environment.
"""
from __future__ import annotations

import time
from typing import List, Optional

from ..chunking import TranscriptSegment


class SpeechExtractor:
    def __init__(self, endpoint: Optional[str] = None, region: Optional[str] = None):
        from ...agent.config import get_settings

        s = get_settings()
        self._endpoint = endpoint or s.speech_endpoint
        self._region = region or s.speech_region

    def _headers(self) -> dict:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        return {"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"}

    def transcribe(
        self,
        media_url: str,
        *,
        locale: str = "en-US",
        poll_interval: float = 5.0,
        max_wait: float = 600.0,
    ) -> List[TranscriptSegment]:
        """Submit a batch transcription job, poll to completion, and return
        diarized segments.

        Polls the job's ``self`` URL with a capped wait, then fetches the
        results file list and parses the transcription result JSON. Raises
        ``TimeoutError`` if the job does not complete within ``max_wait`` and
        ``RuntimeError`` if the job fails.
        """
        import requests

        headers = self._headers()
        create_url = f"{self._endpoint}/speechtotext/v3.2/transcriptions"
        body = {
            "contentUrls": [media_url],
            "locale": locale,
            "displayName": "workplace-ingest",
            "properties": {"diarizationEnabled": True, "wordLevelTimestampsEnabled": True},
        }
        resp = requests.post(create_url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        job_url = resp.json()["self"]

        deadline = time.monotonic() + max_wait
        while True:
            status_resp = requests.get(job_url, headers=self._headers(), timeout=30)
            status_resp.raise_for_status()
            status = status_resp.json().get("status")
            if status == "Succeeded":
                break
            if status == "Failed":
                raise RuntimeError(f"Speech transcription job failed: {job_url}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Speech transcription timed out after {max_wait}s")
            time.sleep(poll_interval)

        files_resp = requests.get(f"{job_url}/files", headers=self._headers(), timeout=30)
        files_resp.raise_for_status()
        segments: List[TranscriptSegment] = []
        for f in files_resp.json().get("values", []):
            if f.get("kind") != "Transcription":
                continue
            content_url = f.get("links", {}).get("contentUrl")
            if not content_url:
                continue
            result = requests.get(content_url, timeout=30)
            result.raise_for_status()
            segments.extend(self.parse_result_json(result.json()))
        return segments

    @staticmethod
    def parse_result_json(result: dict) -> List[TranscriptSegment]:
        """Parse a batch transcription result document into segments (pure)."""
        return SpeechExtractor._parse_phrases(result.get("recognizedPhrases", []))

    @staticmethod
    def _parse_phrases(phrases: list) -> List[TranscriptSegment]:
        segments: List[TranscriptSegment] = []
        for p in phrases:
            offset = float(p.get("offsetInSeconds", 0.0))
            duration = float(p.get("durationInSeconds", 0.0))
            best = (p.get("nBest") or [{}])[0]
            text = best.get("display") or best.get("lexical") or ""
            segments.append(
                TranscriptSegment(
                    start=offset,
                    end=offset + duration,
                    text=text,
                    speaker=str(p.get("speaker", "")),
                )
            )
        return segments
