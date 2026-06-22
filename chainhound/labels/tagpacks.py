"""GraphSense TagPack loader — community attribution tags.

A TagPack is a YAML document with header-level defaults (``label``, ``category``,
``currency``, ``confidence``, ``source``) that each entry under ``tags`` inherits
unless it overrides them; an entry needs only an ``address``. The corpus ships as
a tarball or a checked-out repo of many packs, so ``parse`` handles one document
(pure, unit-testable) and ``load`` walks the corpus and aggregates.
"""
from __future__ import annotations

import logging
import tarfile
from pathlib import Path
from typing import Iterator, Optional

from .base import Label, LabelSource
from .chains import currency_to_chain

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

logger = logging.getLogger(__name__)

# Repo-root default: <repo>/data/labels/tagpacks.tar.gz (git-ignored bulk data).
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "labels" / "tagpacks.tar.gz"

# GraphSense confidence taxonomy -> ChainHound confidence band.
CONFIDENCE_MAP = {
    "ledger_immanent": "Near Certainty",
    "ownership": "Near Certainty",
    "authority_data": "High",
    "service_data": "High",
    "proprietary_data": "High",
    "forensic": "Moderate",
    "web_crawl": "Moderate",
    "heuristic": "Moderate",
    "untrusted_transaction": "Low",
}

# Normalize a few TagPack categories to ChainHound's vocabulary; pass the rest
# through unchanged for glass-box fidelity.
CATEGORY_ALIASES = {
    "mixing_service": "mixer",
    "coinjoin": "mixer",
}

_HEADER_KEYS = ("label", "category", "currency", "confidence", "source")


class TagPackSource(LabelSource):
    source = "tagpack"

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH

    def fetch(self) -> str:  # pragma: no cover - corpora use load(), not fetch()
        raise NotImplementedError("TagPackSource walks a corpus via load()")

    def parse(self, text: str) -> list[Label]:
        if yaml is None:
            raise RuntimeError("install 'pyyaml' to parse TagPacks")
        doc = yaml.safe_load(text) or {}
        if not isinstance(doc, dict):
            return []
        defaults = {k: doc.get(k) for k in _HEADER_KEYS}
        labels: list[Label] = []
        for tag in doc.get("tags") or []:
            if not isinstance(tag, dict):
                continue
            address = tag.get("address")
            if not address:
                continue  # near-miss: dangling entry with no address
            name = tag.get("label", defaults["label"])
            if not name:
                continue
            currency = tag.get("currency", defaults["currency"]) or ""
            category = tag.get("category", defaults["category"]) or "unknown"
            confidence = tag.get("confidence", defaults["confidence"])
            labels.append(
                Label(
                    chain=currency_to_chain(currency),
                    address=str(address).strip(),
                    name=str(name),
                    category=CATEGORY_ALIASES.get(category, category),
                    source=self.source,
                    confidence=CONFIDENCE_MAP.get(confidence, "Moderate"),
                )
            )
        return labels

    def load(self) -> list[Label]:
        labels: list[Label] = []
        for text in self._iter_pack_texts():
            labels.extend(self.parse(text))
        return labels

    def _iter_pack_texts(self) -> Iterator[str]:
        """Yield the YAML text of every pack in the configured corpus."""
        path = self.path
        if not path.exists():
            raise FileNotFoundError(f"TagPack corpus not found: {path}")
        if path.suffix in (".gz", ".tgz") or path.name.endswith(".tar.gz"):
            with tarfile.open(path, "r:gz") as tar:
                for member in tar.getmembers():
                    if _is_pack(member.name):
                        fh = tar.extractfile(member)
                        if fh is not None:
                            yield fh.read().decode("utf-8")
        elif path.is_dir():
            for pack in sorted(path.rglob("*.yaml")):
                if _is_pack(pack.as_posix()):
                    yield pack.read_text(encoding="utf-8")
        else:  # a single pack file
            yield path.read_text(encoding="utf-8")


def _is_pack(name: str) -> bool:
    """A pack lives under packs/ and is not an actorpack."""
    return "/packs/" in name and name.endswith(".yaml") and ".actorpack." not in name
