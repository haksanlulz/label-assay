"""Runtime configuration, read from the environment (and .env in dev).

The API key is read here and passed explicitly to the extractor, never hardcoded
and never sent to the browser. In the deployed container the key comes from a
real environment variable (the host's secret store); .env is a dev convenience.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str | None = None
    daily_budget_usd: float = 5.0
    haiku_model: str = "claude-haiku-4-5-20251001"


def get_settings() -> Settings:
    return Settings()
