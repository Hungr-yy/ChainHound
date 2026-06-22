"""Generic manifest-driven loader for community address dumps.

Sanctioned/scam address repos and Etherscan name-tag dumps come in many shapes
but share a structure: a directory of files listing addresses, all sharing one
source/category/confidence. Rather than hardcode a loader per repo, a small YAML
manifest describes the layout and this source ingests it. Choosing and *vetting*
specific upstream repos is a data decision left to the operator.

Manifest fields:
    source        provenance tag stored on every label (required)
    category      label category (e.g. scam, exchange)
    confidence    confidence band (e.g. Moderate)
    currency      currency code -> chain (e.g. ETH); default applied to all rows
    name          default label name (lines format / rows lacking a name)
    files         glob, relative to the manifest, of files to ingest
    format        "lines" (one address per line, # comments) or "csv"
    address_column / name_column   CSV header names (csv format)
"""
from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Iterator, Optional

from .base import Label, LabelSource
from .chains import currency_to_chain

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

logger = logging.getLogger(__name__)


class RepoSource(LabelSource):
    def __init__(self, manifest: dict, base_dir: Optional[Path] = None) -> None:
        if not manifest.get("source"):
            raise ValueError("manifest must set 'source'")
        self.manifest = manifest
        self.source = manifest["source"]
        self.base_dir = base_dir

    @classmethod
    def from_manifest(cls, manifest_path) -> "RepoSource":
        if yaml is None:
            raise RuntimeError("install 'pyyaml' to read repo manifests")
        path = Path(manifest_path)
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(manifest, base_dir=path.parent)

    def fetch(self) -> str:  # pragma: no cover - corpora use load()
        raise NotImplementedError("RepoSource walks a directory via load()")

    def parse(self, text: str) -> list[Label]:
        fmt = self.manifest.get("format", "lines")
        if fmt == "lines":
            addresses = list(self._iter_lines(text))
            rows = ((addr, None) for addr in addresses)
        elif fmt == "csv":
            rows = self._iter_csv(text)
        else:
            raise ValueError(f"unknown manifest format: {fmt!r}")

        chain = currency_to_chain(self.manifest.get("currency", ""))
        category = self.manifest.get("category", "unknown")
        confidence = self.manifest.get("confidence", "Moderate")
        default_name = self.manifest.get("name") or self.source
        return [
            Label(
                chain=chain,
                address=addr,
                name=name or default_name,
                category=category,
                source=self.source,
                confidence=confidence,
            )
            for addr, name in rows
            if addr
        ]

    def load(self) -> list[Label]:
        if self.base_dir is None:
            raise ValueError("RepoSource needs a base_dir to walk; use from_manifest")
        labels: list[Label] = []
        for path in sorted(self.base_dir.glob(self.manifest.get("files", "*"))):
            if path.is_file():
                labels.extend(self.parse(path.read_text(encoding="utf-8")))
        return labels

    @staticmethod
    def _iter_lines(text: str) -> Iterator[str]:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                yield line

    def _iter_csv(self, text: str):
        addr_col = self.manifest.get("address_column", "address")
        name_col = self.manifest.get("name_column")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            addr = (row.get(addr_col) or "").strip()
            name = (row.get(name_col) or "").strip() if name_col else None
            yield addr, name
