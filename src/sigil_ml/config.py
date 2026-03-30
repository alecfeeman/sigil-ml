"""Configuration and path discovery for sigil-ml."""

from __future__ import annotations

import os
from pathlib import Path


def _data_home() -> Path:
    """Return the XDG data home, defaulting to ~/.local/share."""
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def db_path() -> Path:
    """Return the path to the sigild SQLite database."""
    return _data_home() / "sigild" / "data.db"


def models_dir() -> Path:
    """Return the directory for ML model weights, creating it if needed."""
    d = _data_home() / "sigild" / "ml-models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def weights_path(model_name: str) -> Path:
    """Return the path to a specific model's weight file."""
    return models_dir() / f"{model_name}.joblib"


def sigild_plugin_url() -> str:
    """Return the URL for the sigild plugin ingest/capabilities API."""
    return os.environ.get("SIGILD_PLUGIN_URL", "http://127.0.0.1:7775")


def operating_mode() -> str:
    """Return the operating mode: 'local' or 'cloud'.

    Reads from SIGIL_MODE environment variable.
    Defaults to 'local' if not set.
    """
    mode = os.environ.get("SIGIL_MODE", "local").lower()
    if mode not in ("local", "cloud"):
        raise ValueError(f"Invalid SIGIL_MODE: {mode!r}. Must be 'local' or 'cloud'.")
    return mode


def postgres_url() -> str | None:
    """Return the Postgres connection URL, or None if not configured.

    Set via SIGIL_POSTGRES_URL environment variable.
    Required when SIGIL_MODE=cloud.
    """
    return os.environ.get("SIGIL_POSTGRES_URL")


def tenant_id() -> str:
    """Return the tenant identifier for multi-tenant Postgres schemas.

    Set via SIGIL_TENANT environment variable.
    Defaults to 'public' if not set.
    """
    return os.environ.get("SIGIL_TENANT", "public")


def serving_mode() -> str:
    """Return the serving mode: 'local' or 'cloud'. Alias for operating_mode()."""
    return operating_mode()


def s3_bucket() -> str | None:
    """Return the S3 bucket for model storage, or None if not configured."""
    return os.environ.get("SIGIL_S3_BUCKET")


def s3_endpoint_url() -> str | None:
    """Return the S3 endpoint URL (for MinIO), or None for AWS default."""
    return os.environ.get("SIGIL_S3_ENDPOINT_URL")


def aws_region() -> str | None:
    """Return the AWS region, or None for default."""
    return os.environ.get("AWS_REGION")


def model_cache_ttl() -> float:
    """Return the model cache TTL in seconds. Default 300."""
    return float(os.environ.get("SIGIL_MODEL_CACHE_TTL", "300"))
