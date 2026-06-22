"""GraphSense TagPacks (bulk loader).

TagPacks are YAML files of attribution tags. Each pack has header-level defaults
(``creator``, ``category``, ``confidence``, ``currency``) and a ``tags`` list,
where each tag may override the header. The GraphSense repo holds many packs, so
``fetch_raw`` pulls the repo tarball and ``parse`` fans out over its ``*.yaml``
members; ``parse_pack`` (one pack -> labels) is the pure, offline-tested core.

See ARCHITECTURE.md "Tech choices": we read GraphSense for the TagPack reference
but stay lean — this consumes the data without its Spark/Cassandra stack.
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

from ..models import LabelRecord, confidence_band
from .base import BulkLoader

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Repo tarball (default branch). Overridable for mirrors/tests.
DEFAULT_URL = "https://github.com/graphsense/graphsense-tagpacks/archive/refs/heads/master.tar.gz"

# TagPack currency code -> canonical chain name.
CURRENCY_CHAIN = {
    "BTC": "bitcoin",
    "BCH": "bitcoin-cash",
    "LTC": "litecoin",
    "ZEC": "zcash",
    "ETH": "ethereum",
    "TRX": "tron",
    "XRP": "ripple",
}

# TagPack named confidence levels -> our confidence bands. Unknown -> "High".
_CONF_NAME_BAND = {
    "ownership": "Near Certainty",
    "manual": "Near Certainty",
    "service": "High",
    "trusted": "High",
    "heuristic": "Moderate",
    "web_crawl": "Moderate",
    "forensic": "Moderate",
    "weak": "Low",
}


def _map_confidence(raw) -> str:
    """Map a TagPack confidence (name or 0-100 score) to a band string."""
    if raw is None:
        return "High"
    if isinstance(raw, (int, float)):
        score = float(raw)
        return confidence_band(score / 100 if score > 1 else score)
    return _CONF_NAME_BAND.get(str(raw).strip().lower(), "High")


class TagPackLoader(BulkLoader):
    source = "tagpacks"

    def __init__(self, url: str = DEFAULT_URL, timeout: int = 60,
                 cache_dir: Path | None = None) -> None:
        self.url = url
        self.timeout = timeout
        self.cache_dir = cache_dir or Path("data/labels")

    def fetch_raw(self) -> bytes:
        if requests is None:
            raise RuntimeError("install 'requests' to fetch GraphSense TagPacks")
        resp = requests.get(self.url, timeout=self.timeout)
        resp.raise_for_status()
        raw = resp.content
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "tagpacks.tar.gz").write_bytes(raw)
        return raw

    def parse(self, raw: bytes) -> list[LabelRecord]:
        # A gzip tarball of the repo, or a single YAML pack (tests/mirrors).
        if raw[:2] == b"\x1f\x8b":
            return self._parse_archive(raw)
        return self.parse_pack(raw)

    def _parse_archive(self, raw: bytes) -> list[LabelRecord]:
        records: list[LabelRecord] = []
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                if not member.name.lower().endswith((".yaml", ".yml")):
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                try:
                    records.extend(self.parse_pack(f.read()))
                except Exception:
                    # A malformed pack must not abort the whole sync.
                    continue
        return records

    def parse_pack(self, raw) -> list[LabelRecord]:
        """Parse a single TagPack YAML document into canonical labels (pure)."""
        if yaml is None:
            raise RuntimeError("install 'PyYAML' to parse GraphSense TagPacks")
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            return []

        h_currency = (data.get("currency") or "").upper()
        h_category = data.get("category")
        h_confidence = data.get("confidence")
        h_label = data.get("label") or data.get("title")

        records: list[LabelRecord] = []
        for tag in data.get("tags", []) or []:
            if not isinstance(tag, dict):
                continue
            address = tag.get("address")
            if not address:
                continue
            currency = (tag.get("currency") or h_currency or "").upper()
            chain = CURRENCY_CHAIN.get(currency, currency.lower() or "unknown")
            records.append(LabelRecord(
                chain=chain,
                address=str(address).strip(),
                name=tag.get("label") or h_label or "tagpack",
                source=self.source,
                category=tag.get("category") or h_category,
                confidence=_map_confidence(tag.get("confidence") or h_confidence),
            ))
        return records
