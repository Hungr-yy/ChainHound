"""Tests for the OFAC SDN label loader. Pure parsing, no network.

The fixture doubles as a detection-signature regression: it MUST trigger labels
for the digital-currency addresses and MUST NOT for the passport-only near-miss.
"""
from pathlib import Path

from chainhound.labels.ofac import OFACSource

FIXTURE = Path(__file__).parent / "fixtures" / "sample_sdn.xml"


def _parse():
    return OFACSource().parse(FIXTURE.read_text())


def test_parses_both_digital_currency_addresses():
    labels = _parse()
    # Two crypto addresses on the Lazarus entry; nothing from the near-misses.
    assert len(labels) == 2
    by_addr = {l.address: l for l in labels}

    btc = by_addr["1Q9UMz5aGanLxgqQ2j6t9JNQVSiCwGCi9b"]
    assert btc.chain == "bitcoin"
    eth = by_addr["0x8576acc5c05d6ce88f4e49bf65bdf0c62f91353c"]
    assert eth.chain == "ethereum"


def test_label_carries_glassbox_provenance():
    btc = next(
        l for l in _parse() if l.address == "1Q9UMz5aGanLxgqQ2j6t9JNQVSiCwGCi9b"
    )
    assert btc.source == "ofac"
    assert btc.category == "sanctioned"
    assert btc.confidence == "Near Certainty"
    assert "LAZARUS GROUP" in btc.name


def test_near_miss_passport_and_empty_entries_yield_no_labels():
    addrs = {l.address for l in _parse()}
    # The passport idNumber and the id-less entity must never become labels.
    assert "X1234567" not in addrs
    assert all(a for a in addrs)
    assert len(addrs) == 2
