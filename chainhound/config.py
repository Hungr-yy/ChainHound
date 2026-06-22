"""Runtime configuration via environment variables (see .env.example)."""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass
class Config:
    provider: str = os.getenv("CHAINHOUND_PROVIDER", "blockstream")
    gcp_project: str | None = os.getenv("CHAINHOUND_GCP_PROJECT")
    database_url: str | None = os.getenv("CHAINHOUND_DATABASE_URL")
    ofac_url: str | None = os.getenv("CHAINHOUND_OFAC_URL")
    tagpacks_path: str | None = os.getenv("CHAINHOUND_TAGPACKS_PATH")
    chainabuse_key: str | None = os.getenv("CHAINHOUND_CHAINABUSE_KEY")

    def provider_kwargs(self) -> dict:
        if self.provider in ("bigquery", "bq"):
            return {"project": self.gcp_project}
        return {}


def load() -> Config:
    return Config()
