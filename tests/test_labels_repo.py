"""Tests for the generic manifest-driven dump/repo loader. Offline."""
from pathlib import Path

from chainhound.labels.repo import RepoSource

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "repo_dump"

LINES_MANIFEST = {
    "source": "testscamrepo",
    "category": "scam",
    "confidence": "Moderate",
    "currency": "ETH",
    "name": "TestScamRepo",
    "format": "lines",
    "files": "*.txt",
}
CSV_MANIFEST = {
    "source": "namedump",
    "category": "exchange",
    "confidence": "High",
    "currency": "BTC",
    "format": "csv",
    "address_column": "address",
    "name_column": "label",
    "files": "*.csv",
}


def test_lines_format_skips_comments_and_blanks():
    text = "# header\n0xAAA\n\n0xBBB\n"
    labels = RepoSource(LINES_MANIFEST).parse(text)
    assert [l.address for l in labels] == ["0xAAA", "0xBBB"]
    l = labels[0]
    assert l.chain == "ethereum"
    assert l.category == "scam"
    assert l.source == "testscamrepo"
    assert l.confidence == "Moderate"
    assert l.name == "TestScamRepo"


def test_csv_format_uses_named_columns():
    text = "address,label\n1AAA,Evil Inc\n1BBB,Bad Co\n"
    labels = RepoSource(CSV_MANIFEST).parse(text)
    by_addr = {l.address: l for l in labels}
    assert set(by_addr) == {"1AAA", "1BBB"}
    assert by_addr["1AAA"].name == "Evil Inc"
    assert by_addr["1AAA"].chain == "bitcoin"


def test_csv_row_without_address_is_skipped():
    text = "address,label\n,Nameless\n1CCC,Real\n"
    labels = RepoSource(CSV_MANIFEST).parse(text)
    assert [l.address for l in labels] == ["1CCC"]


def test_load_walks_manifest_directory():
    src = RepoSource.from_manifest(FIXTURE_DIR / "manifest.yaml")
    addrs = {l.address for l in src.load()}
    assert addrs == {
        "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    }
