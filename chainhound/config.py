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
    evm_provider_url: str | None = os.getenv("CHAINHOUND_EVM_PROVIDER_URL")
    etherscan_key: str | None = os.getenv("CHAINHOUND_ETHERSCAN_KEY")
    evm_chain_id: int = int(os.getenv("CHAINHOUND_EVM_CHAIN_ID", "1"))

    def provider_kwargs(self) -> dict:
        if self.provider in ("bigquery", "bq"):
            return {"project": self.gcp_project}
        if self.provider in ("ethereum", "eth", "evm"):
            kw: dict = {"chain_id": self.evm_chain_id, "api_key": self.etherscan_key}
            if self.evm_provider_url:
                kw["base_url"] = self.evm_provider_url
            return kw
        return {}


def load() -> Config:
    return Config()
