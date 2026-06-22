"""Tests for the label-corpus loaders (Phase 2a). Pure logic, no network, no DB.

Parsers are fed synthetic source documents; the rate-limiter and HTTP fetcher
run against a fake clock and a fake transport, so nothing here sleeps for real or
opens a socket — same discipline as tests/test_change_analysis.py.
"""
import pytest

from chainhound.models import LabelRecord
from chainhound.loaders.ofac import OFACLoader
from chainhound.loaders.tagpacks import TagPackLoader, _map_confidence
from chainhound.loaders.chainabuse import ChainabuseLoader, parse_reports
from chainhound.loaders.ratelimit import (
    TokenBucket, HttpFetcher, RetryableStatus, backoff,
)


# --- test doubles ---------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _clock_and_sleep():
    """A fake clock plus a sleep that advances it (so token refills happen)."""
    clock = FakeClock()
    sleeps = []

    def sleep(d):
        sleeps.append(d)
        clock.advance(d)

    return clock, sleep, sleeps


class FakeResp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, timeout=None, **kw):
        self.calls.append(url)
        return self.responses.pop(0)


# --- OFAC parser ----------------------------------------------------------

OFAC_XML = b"""<?xml version="1.0"?>
<Sanctions>
  <ReferenceValueSets>
    <FeatureTypeValues>
      <FeatureType ID="344">Digital Currency Address - XBT</FeatureType>
      <FeatureType ID="345">Digital Currency Address - ETH</FeatureType>
      <FeatureType ID="99">Birthdate</FeatureType>
    </FeatureTypeValues>
  </ReferenceValueSets>
  <DistinctParties>
    <DistinctParty>
      <Profile>
        <Feature FeatureTypeID="344">
          <FeatureVersion>
            <VersionDetail>1Q9UMQjReZv8hBkz2dCgUf3M1jR4DT4zwd</VersionDetail>
          </FeatureVersion>
        </Feature>
        <Feature FeatureTypeID="345">
          <FeatureVersion>
            <VersionDetail>0x098B716B8Aaf21512996dC57EB0615e2383E2f96</VersionDetail>
          </FeatureVersion>
        </Feature>
        <Feature FeatureTypeID="99">
          <FeatureVersion><VersionDetail>1975</VersionDetail></FeatureVersion>
        </Feature>
      </Profile>
    </DistinctParty>
  </DistinctParties>
</Sanctions>"""


def test_ofac_parses_digital_currency_addresses():
    records = OFACLoader().parse(OFAC_XML)
    # The birthdate feature is ignored; only the two crypto addresses come through.
    assert len(records) == 2
    by_chain = {r.chain: r for r in records}
    assert by_chain["bitcoin"].address == "1Q9UMQjReZv8hBkz2dCgUf3M1jR4DT4zwd"
    assert by_chain["ethereum"].address.startswith("0x098B716B")
    for r in records:
        assert r.source == "ofac"
        assert r.name == "OFAC SDN"
        assert r.category == "sanctioned"
        assert r.confidence == "Near Certainty"   # authoritative -> top band


# --- TagPack parser -------------------------------------------------------

TAGPACK_YAML = """
title: Test Pack
creator: tester
category: exchange
confidence: ownership
tags:
  - address: 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2
    label: Binance
    currency: BTC
  - address: "0xabc123"
    label: Tornado Cash
    currency: ETH
    category: mixer
    confidence: heuristic
"""


def test_tagpack_parses_header_defaults_and_overrides():
    records = TagPackLoader().parse_pack(TAGPACK_YAML)
    assert len(records) == 2
    binance, tornado = records

    assert binance.chain == "bitcoin"
    assert binance.name == "Binance"
    assert binance.category == "exchange"          # inherited from the header
    assert binance.confidence == "Near Certainty"  # 'ownership' -> top band
    assert binance.source == "tagpacks"

    assert tornado.chain == "ethereum"
    assert tornado.category == "mixer"             # per-tag override
    assert tornado.confidence == "Moderate"        # 'heuristic' -> moderate


def test_tagpack_confidence_mapping():
    assert _map_confidence("ownership") == "Near Certainty"
    assert _map_confidence("weak") == "Low"
    assert _map_confidence(None) == "High"          # default when unspecified
    assert _map_confidence(90) == "Near Certainty"  # 0-100 numeric scale
    assert _map_confidence("nonsense") == "High"    # unknown name -> default


# --- TokenBucket ----------------------------------------------------------

def test_token_bucket_throttles_after_capacity():
    clock, sleep, sleeps = _clock_and_sleep()
    bucket = TokenBucket(rate=5, capacity=5, now=clock, sleep=sleep)

    for _ in range(5):           # drain the full bucket with no waiting
        bucket.acquire()
    assert sleeps == []

    bucket.acquire()             # 6th token: must wait 1/5s for one to accrue
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.2)


def test_token_bucket_rejects_oversized_request():
    bucket = TokenBucket(rate=5, capacity=5)
    with pytest.raises(ValueError):
        bucket.acquire(6)


# --- backoff / HttpFetcher ------------------------------------------------

def _fetcher(responses, retries=5):
    clock, sleep, sleeps = _clock_and_sleep()
    # Plenty of tokens so the bucket never adds sleeps; only backoff does.
    bucket = TokenBucket(rate=1000, capacity=1000, now=clock, sleep=sleep)
    transport = FakeTransport(responses)
    fetcher = HttpFetcher(bucket, transport=transport, sleep=sleep, retries=retries)
    return fetcher, transport, sleeps


def test_http_fetcher_retries_then_succeeds():
    fetcher, transport, sleeps = _fetcher(
        [FakeResp(429), FakeResp(429), FakeResp(200, {"ok": True})]
    )
    assert fetcher.get_json("http://x") == {"ok": True}
    assert len(transport.calls) == 3
    # exponential backoff: base 0.5 then 1.0
    assert sleeps == [0.5, 1.0]


def test_http_fetcher_gives_up_after_retries():
    fetcher, transport, _ = _fetcher([FakeResp(429)] * 10, retries=3)
    with pytest.raises(RetryableStatus):
        fetcher.get_json("http://x")
    assert len(transport.calls) == 4   # initial try + 3 retries


def test_backoff_returns_immediately_on_success():
    calls = []
    out = backoff(lambda: calls.append(1) or "done", sleep=lambda d: None)
    assert out == "done"
    assert calls == [1]


# --- Chainabuse (on-demand) -----------------------------------------------

def test_chainabuse_parses_reports_at_low_confidence():
    recs = parse_reports(
        "0xabc",
        {"reports": [{"category": "phishing"}, {"title": "Scammer", "category": "RANSOMWARE"}]},
        chain="ethereum",
    )
    assert len(recs) == 2
    assert recs[0].category == "phishing"
    assert recs[1].name == "Scammer"
    assert recs[1].category == "ransomware"        # normalized to lowercase
    for r in recs:
        assert r.chain == "ethereum"
        assert r.source == "chainabuse"
        assert r.confidence == "Low"               # community report -> low band


def test_chainabuse_loader_fetches_through_rate_limited_transport():
    fetcher, transport, _ = _fetcher(
        [FakeResp(200, {"reports": [{"category": "scam"}]})]
    )
    loader = ChainabuseLoader(database_url=None, fetcher=fetcher)
    recs = loader.fetch_for_address("0xabc", chain="ethereum")
    assert len(recs) == 1 and recs[0].source == "chainabuse"
    assert transport.calls == [
        "https://api.chainabuse.com/v0/reports?address=0xabc"
    ]


# --- upsert natural key ---------------------------------------------------

def test_label_record_upsert_key_matches_unique_index():
    r = LabelRecord(chain="bitcoin", address="1abc", name="OFAC SDN", source="ofac")
    assert r.upsert_key == ("bitcoin", "1abc", "ofac", "OFAC SDN")
