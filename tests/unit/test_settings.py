"""Smoke tests for settings parsing."""

from __future__ import annotations

import pytest

from app.settings import Settings


def test_defaults_load() -> None:
    s = Settings()
    assert s.cache_similarity_threshold == 0.92
    assert s.pii_mode == "reversible"
    assert s.embedding_model.endswith("all-MiniLM-L6-v2")


def test_cors_origins_csv_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIL_CORS_ORIGINS", "https://a.example, https://b.example")
    s = Settings()
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_threshold_out_of_range_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIL_CACHE_SIMILARITY_THRESHOLD", "1.5")
    with pytest.raises(ValueError, match="between 0 and 1"):
        Settings()
