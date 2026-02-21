"""Pydantic configuration models for the refresh service."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger(__name__)


class AuthConfig(BaseModel):
    """Authentication configuration for a single site."""

    type: Literal["credentials", "oauth"]
    username_env: str | None = None
    password_env: str | None = None


class SiteConfig(BaseModel):
    """Configuration for a single paywalled site."""

    domain: str
    login_url: str
    auth: AuthConfig
    refresh_interval: str = "12h"


class Config(BaseModel):
    """Top-level refresh service configuration."""

    sites: list[SiteConfig]
    cookie_dir: str = Field(default_factory=lambda: os.getenv("COOKIE_DIR", "/cookies"))
    ntfy_url: str | None = None
    healthcheck_url: str | None = None

    @field_validator("sites")
    @classmethod
    def sites_must_be_nonempty(cls, v: list[SiteConfig]) -> list[SiteConfig]:
        """Validate that at least one site is configured."""
        if not v:
            raise ValueError("Config must define at least one site")
        return v


def load_config(path: str | None = None) -> Config:
    """Load and validate configuration from a YAML file.

    Args:
        path: Path to sites.yaml. Defaults to CONFIG_PATH env, then /config/sites.yaml.

    Returns:
        Validated Config instance.
    """
    config_path = Path(path or os.getenv("CONFIG_PATH", "/config/sites.yaml"))
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open() as f:
        raw = yaml.safe_load(f)

    config = Config(**raw)
    logger.info(
        "config_loaded",
        path=str(config_path),
        sites=[s.domain for s in config.sites],
    )
    return config
