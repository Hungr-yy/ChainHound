"""Triage label attachment. Offline: a fake provider and a fake label lookup."""
from chainhound.models import AddressSummary
from chainhound.analysis.triage import triage_address


class _FakeProvider:
    chain = "bitcoin"

    def __init__(self, summary):
        self._summary = summary

    def get_address_summary(self, address):
        return self._summary


def _summary():
    return AddressSummary(address="1AAA", chain="bitcoin", tx_count=1)


def test_labels_attached_when_lookup_provided():
    calls = []

    def lookup(chain, address):
        calls.append((chain, address))
        return ["OFAC SDN: LAZARUS GROUP"]

    report = triage_address(_FakeProvider(_summary()), "1AAA", label_lookup=lookup)
    assert report["labels"] == ["OFAC SDN: LAZARUS GROUP"]
    assert calls == [("bitcoin", "1AAA")]


def test_labels_empty_without_lookup():
    report = triage_address(_FakeProvider(_summary()), "1AAA")
    assert report["labels"] == []
