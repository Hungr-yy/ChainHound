"""Tests for the GraphSense TagPack loader. Pure parsing, no network."""
from pathlib import Path

from chainhound.labels.tagpacks import TagPackSource

FIXTURE = Path(__file__).parent / "fixtures" / "sample_tagpack.yaml"


def _parse():
    return TagPackSource().parse(FIXTURE.read_text())


def test_header_defaults_are_inherited_by_tags():
    labels = _parse()
    by_addr = {l.address: l for l in labels}
    btc = by_addr["1ExchangeAddrAAAAAAAAAAAAAAAAAAAAA"]
    assert btc.chain == "bitcoin"
    assert btc.name == "TestExchange"
    assert btc.category == "exchange"
    assert btc.source == "tagpack"
    assert btc.confidence == "High"  # service_data -> High


def test_per_tag_overrides_win():
    mixer = next(
        l for l in _parse() if l.address.startswith("0xMixerAddr")
    )
    assert mixer.chain == "ethereum"          # currency override
    assert mixer.name == "TestMixer"          # label override
    assert mixer.category == "mixer"          # mixing_service alias
    assert mixer.confidence == "Moderate"     # forensic -> Moderate


def test_tag_without_address_is_skipped():
    addrs = {l.address for l in _parse()}
    assert len(addrs) == 3
    assert all(addrs)


def test_empty_pack_yields_no_labels():
    assert TagPackSource().parse("title: empty\ntags: []\n") == []
    assert TagPackSource().parse("title: no tags key\n") == []
