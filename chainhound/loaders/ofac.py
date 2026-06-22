"""OFAC SDN sanctioned digital-currency addresses (bulk loader).

The U.S. Treasury publishes the Specially Designated Nationals list as
`sdn_advanced.xml`. Crypto addresses appear as party *features* whose feature
type is named ``Digital Currency Address - <ASSET>`` (XBT, ETH, XMR, ...), with
the address string in the feature's ``VersionDetail``.

Parsing is a pure function of the XML bytes (`parse`), so it is unit-tested
offline against a small synthetic document. OFAC is authoritative, so every
address is tagged at the top confidence band.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import LabelRecord
from .base import BulkLoader

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

# Stable Treasury export. Overridable via the constructor for mirrors/tests.
DEFAULT_URL = "https://www.treasury.gov/ofac/downloads/sdn_advanced.xml"

FEATURE_PREFIX = "Digital Currency Address - "

# OFAC asset code -> canonical chain name. Unknown codes fall back to the
# lowercased code so a newly-sanctioned asset still ingests (just un-mapped).
ASSET_CHAIN = {
    "XBT": "bitcoin",
    "BCH": "bitcoin-cash",
    "BSV": "bitcoin-sv",
    "BTG": "bitcoin-gold",
    "ETH": "ethereum",
    "ETC": "ethereum-classic",
    "USDT": "ethereum",        # most sanctioned USDT entries are ERC-20
    "LTC": "litecoin",
    "XMR": "monero",
    "ZEC": "zcash",
    "DASH": "dash",
    "XVG": "verge",
    "XRP": "ripple",
    "TRX": "tron",
    "ARB": "arbitrum",
    "BASE": "base",
}


def _local(tag: str) -> str:
    """Strip the XML namespace, leaving the local element name."""
    return tag.rsplit("}", 1)[-1]


class OFACLoader(BulkLoader):
    source = "ofac"

    def __init__(self, url: str = DEFAULT_URL, timeout: int = 60,
                 cache_dir: Path | None = None) -> None:
        self.url = url
        self.timeout = timeout
        # Downloads land under data/ (git-ignored) by default.
        self.cache_dir = cache_dir or Path("data/labels")

    def fetch_raw(self) -> bytes:
        if requests is None:
            raise RuntimeError("install 'requests' to fetch the OFAC SDN list")
        resp = requests.get(self.url, timeout=self.timeout)
        resp.raise_for_status()
        raw = resp.content
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "sdn_advanced.xml").write_bytes(raw)
        return raw

    def parse(self, raw: bytes) -> list[LabelRecord]:
        root = ET.fromstring(raw)

        # 1) Map each digital-currency FeatureType id -> its asset code.
        feature_assets: dict[str, str] = {}
        for el in root.iter():
            if _local(el.tag) != "FeatureType":
                continue
            name = (el.text or "").strip()
            if name.startswith(FEATURE_PREFIX):
                fid = el.get("ID")
                if fid:
                    feature_assets[fid] = name[len(FEATURE_PREFIX):].strip()

        # 2) Walk party features; emit one label per address VersionDetail.
        records: list[LabelRecord] = []
        for feat in root.iter():
            if _local(feat.tag) != "Feature":
                continue
            asset = feature_assets.get(feat.get("FeatureTypeID", ""))
            if asset is None:
                continue
            chain = ASSET_CHAIN.get(asset.upper(), asset.lower())
            for vd in feat.iter():
                if _local(vd.tag) == "VersionDetail" and (vd.text or "").strip():
                    records.append(LabelRecord(
                        chain=chain,
                        address=vd.text.strip(),
                        name="OFAC SDN",
                        source=self.source,
                        category="sanctioned",
                        confidence="Near Certainty",
                    ))
        return records
