"""Small, atomic store for shared UI preferences. Does not load models."""
import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, Field


class Preferences(BaseModel):
    response_mode: Literal["automatic", "manual"] = "automatic"
    response_model: str = Field(default_factory=lambda: os.getenv("RAG_OLLAMA_MODEL", ""), max_length=300)
    k: Literal[2, 4, 6, 8] = 4
    search_only: bool = False
    multimodal: bool = False
    vision_model: str = Field(default="qwen3.5:9b-q4_K_M", max_length=300)
    max_images: int = Field(default=3, ge=1, le=4)
    max_visuals_per_document: int = Field(default=500, ge=1, le=500)


class SettingsStore:
    def __init__(self, path: Path):
        self.path, self.lock = path, RLock()

    def get(self):
        with self.lock:
            return Preferences.model_validate_json(self.path.read_text(encoding="utf-8")) if self.path.exists() else Preferences()

    def save(self, value: Preferences):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pending = self.path.with_suffix(".pending")
            pending.write_text(value.model_dump_json(indent=2), encoding="utf-8")
            pending.replace(self.path)
        return value
