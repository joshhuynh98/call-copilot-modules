"""Where transcripts and recommendations come from.

Our modules only depend on these two Protocols. Today they are backed by fixture files; when the
transcript / recommendation modules exist, add an HttpSource (or an in-process adapter) that
implements the same method and nothing else changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from callcopilot.shared.contracts import RecommendationSet, Transcript


class TranscriptSource(Protocol):
    def get_transcript(self, case_id: str) -> Transcript: ...


class RecommendationSource(Protocol):
    def get_recommendations(self, case_id: str) -> RecommendationSet: ...


class FileTranscriptSource:
    """Reads a transcript JSON file (ignores case_id; handy for ad-hoc payloads)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get_transcript(self, case_id: str = "") -> Transcript:
        return Transcript.model_validate_json(self.path.read_text(encoding="utf-8"))


class FileRecommendationSource:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get_recommendations(self, case_id: str = "") -> RecommendationSet:
        return RecommendationSet.model_validate_json(self.path.read_text(encoding="utf-8"))
