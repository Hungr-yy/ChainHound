"""Contract calldata decoding. Offline: a fake 4byte fetch over captured JSON."""
import json
from pathlib import Path

from chainhound.analysis.decode import decode_input, selector

_FOURBYTE = json.loads(
    (Path(__file__).parent / "fixtures" / "evm" / "fourbyte_transfer.json").read_text()
)


def _fake_fetch(sel):
    return _FOURBYTE


def test_selector_extraction():
    assert selector("0xa9059cbb000000000000000000000000abc") == "0xa9059cbb"
    assert selector("0x") is None
    assert selector("") is None
    assert selector(None) is None


def test_decode_prefers_verified_signature_over_spam():
    # 4byte returns a spam collision + the real one; the verified one wins.
    sig = decode_input("0xa9059cbb0000000000000000000000001111", fetch=_fake_fetch)
    assert sig == "transfer(address,uint256)"


def test_plain_transfer_has_no_function():
    called = []
    decode_input("0x", fetch=lambda s: called.append(s))
    assert decode_input("0x", fetch=_fake_fetch) is None
    assert called == []   # no lookup attempted for empty calldata


def test_unknown_selector_returns_none():
    assert decode_input("0xdeadbeef00", fetch=lambda s: {"ok": True, "result": {"function": {}}}) is None
