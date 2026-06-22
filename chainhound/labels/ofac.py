"""OFAC SDN loader — sanctioned digital-currency addresses.

Parses the U.S. Treasury Specially Designated Nationals list (legacy ``sdn.xml``
format). Each ``<sdnEntry>`` may carry one or more ``<id>`` elements of type
``"Digital Currency Address - <CCY>"``; we turn each into a ``Label`` tagged with
the sanctioned entity's name for glass-box provenance. Authoritative and
low-noise, so labels carry ``Near Certainty`` confidence.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Optional

from .base import Label, LabelSource

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)

URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"

_ID_TYPE_PREFIX = "Digital Currency Address - "

# Map the SDN currency code to a ChainHound chain name. Unknown codes fall back
# to the lowercased code so a newly sanctioned asset never crashes a sync.
CURRENCY_TO_CHAIN = {
    "XBT": "bitcoin",
    "ETH": "ethereum",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "XMR": "monero",
    "ZEC": "zcash",
    "DASH": "dash",
    "BTG": "bitcoin-gold",
    "ETC": "ethereum-classic",
    "BSV": "bitcoin-sv",
    "XVG": "verge",
    "XRP": "ripple",
    "TRX": "tron",
    "ARB": "arbitrum",
    "USDT": "ethereum",
    "USDC": "ethereum",
}


class OFACSource(LabelSource):
    source = "ofac"

    def __init__(self, url: str = URL, timeout: int = 30) -> None:
        self.url = url
        self.timeout = timeout

    def fetch(self) -> str:
        if requests is None:
            raise RuntimeError("install 'requests' to fetch the OFAC SDN list")
        resp = requests.get(self.url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def parse(self, text: str) -> list[Label]:
        root = ET.fromstring(text)
        ns_uri = root.tag[1 : root.tag.index("}")] if root.tag.startswith("{") else ""

        def q(tag: str) -> str:
            return f"{{{ns_uri}}}{tag}" if ns_uri else tag

        labels: list[Label] = []
        for entry in root.findall(q("sdnEntry")):
            entity = self._entity_name(entry, q)
            for id_el in entry.iterfind(f"{q('idList')}/{q('id')}"):
                id_type = (id_el.findtext(q("idType")) or "").strip()
                if not id_type.startswith(_ID_TYPE_PREFIX):
                    continue  # near-miss: passports, tax IDs, etc.
                address = (id_el.findtext(q("idNumber")) or "").strip()
                if not address:
                    continue
                currency = id_type[len(_ID_TYPE_PREFIX) :].strip()
                chain = CURRENCY_TO_CHAIN.get(currency, currency.lower())
                labels.append(
                    Label(
                        chain=chain,
                        address=address,
                        name=f"OFAC SDN: {entity}" if entity else "OFAC SDN",
                        category="sanctioned",
                        source=self.source,
                        confidence="Near Certainty",
                    )
                )
        return labels

    @staticmethod
    def _entity_name(entry: ET.Element, q) -> Optional[str]:
        parts = [entry.findtext(q("firstName")), entry.findtext(q("lastName"))]
        name = " ".join(p.strip() for p in parts if p and p.strip())
        return name or None
