"""Chainabuse parser tests. Pure parsing of a synthetic response, no network.

The live response envelope is undocumented, so parse() is tolerant; this fixture
captures the documented report fields (scamCategory/category, trusted, checked).
"""
import json

import pytest

from chainhound.labels.chainabuse import ChainabuseSource

# Mirrors the documented report fields under a likely "reports" envelope.
SAMPLE = json.dumps(
    {
        "reports": [
            {"scamCategory": "PHISHING", "trusted": True, "createdAt": "2024-01-01"},
            {"scamCategory": "PHISHING", "trusted": False},
            {"category": "RUG_PULL", "checked": False},
        ]
    }
)


def _parse(raw):
    return ChainabuseSource().parse(raw, "ethereum", "0xBAD")


def test_parses_one_label_per_category_with_provenance():
    labels = _parse(SAMPLE)
    by_name = {l.name: l for l in labels}
    assert set(by_name) == {"Chainabuse: PHISHING", "Chainabuse: RUG_PULL"}
    for l in labels:
        assert l.source == "chainabuse"
        assert l.category == "scam"
        assert l.chain == "ethereum"
        assert l.address == "0xBAD"


def test_trusted_report_lifts_confidence_but_never_to_high():
    # Community reports are the noisiest source: anonymous reports floor at Low,
    # a vetted (trusted/checked) reporter lifts to Moderate -- never to High,
    # which would put a scam report on par with an OFAC listing.
    by_name = {l.name: l for l in _parse(SAMPLE)}
    assert by_name["Chainabuse: PHISHING"].confidence == "Moderate"  # a trusted report
    assert by_name["Chainabuse: RUG_PULL"].confidence == "Low"       # none trusted


def test_no_reports_yields_no_labels():
    assert _parse(json.dumps({"reports": []})) == []
    assert _parse(json.dumps([])) == []


def test_fetch_without_key_raises():
    src = ChainabuseSource(api_key=None)
    with pytest.raises(RuntimeError):
        src._fetch("ethereum", "0xBAD")
