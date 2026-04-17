"""
Unified secret loading.

Resolution order for a given key name:
  1. Process env var (set by Cloud Run, docker -e, CI, etc.)
  2. Local .env file via python-dotenv (for local development)
  3. GCP Secret Manager (for cloud deployment without env var injection)

This lets the same code run locally (reads .env) and in Cloud Run
(reads Secret Manager) without any code changes.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

GCP_PROJECT = os.getenv("GCP_PROJECT", "smart-wallets-tracker")


@lru_cache(maxsize=32)
def get_secret(name: str) -> str:
    """Fetch a secret by name. Cached for the process lifetime."""
    # 1. Env var (includes vars loaded from .env by load_dotenv())
    value = os.getenv(name)
    if value:
        return value

    # 2. Fall back to GCP Secret Manager
    try:
        from google.cloud import secretmanager
    except ImportError:
        raise RuntimeError(
            f"Secret '{name}' not found in env, and google-cloud-secret-manager "
            "is not installed. Either install it or set the env var."
        )

    client = secretmanager.SecretManagerServiceClient()
    resource = f"projects/{GCP_PROJECT}/secrets/{name}/versions/latest"
    try:
        response = client.access_secret_version(name=resource)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch secret '{name}' from Secret Manager: {e}")

    return response.payload.data.decode("utf-8")
