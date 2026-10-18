"""Secrets loaded from environment / .env — never from settings.yaml."""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # India
    zerodha_api_key: SecretStr | None = None
    zerodha_api_secret: SecretStr | None = None
    dhan_client_id: SecretStr | None = None
    dhan_access_token: SecretStr | None = None

    # US
    alpaca_api_key: SecretStr | None = None
    alpaca_secret_key: SecretStr | None = None
    alpaca_paper: bool = True

    # Email
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

    # LLM
    telegram_bot_token: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # SEC EDGAR requires a descriptive User-Agent with a contact email,
    # e.g. "TradeMare you@example.com". US fundamentals fall back to yfinance without it.
    sec_user_agent: str | None = None

    # Dashboard
    dashboard_password: SecretStr | None = None
