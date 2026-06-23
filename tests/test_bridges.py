"""THORChain Midgard api-tier bridge connector. Offline: a captured swap action."""
import json
from pathlib import Path

from chainhound.bridges import ThorchainMidgard

_FIX = json.loads((Path(__file__).parent / "fixtures" / "midgard_swap.json").read_text())
SRC_TXID = "CE396D8FBC19B962D03DC09E693909A5C1E36B30747AD036AA061ED44029AB3C"


def _transport(_params):
    return _FIX


def test_midgard_maps_cross_chain_swap_pair():
    link = ThorchainMidgard(transport=_transport).lookup("bitcoin", SRC_TXID.lower())
    assert link is not None
    assert link.method == "api" and link.bridge == "thorchain"
    assert link.src_chain == "bitcoin"
    assert link.dst_chain == "ethereum"
    assert link.dst_txid == "0x8c7af3e6e1b1742b8b9420550a2dbfa4b93505efb153861ac0a33bc2529d58bc"
    assert link.confidence == "Near Certainty"      # status == success
    assert link.matched_on["out"]["asset"].startswith("ETH.USDC")


def test_midgard_skips_rune_intermediate_and_matches_uppercase_txid():
    # the THOR.RUNE empty-txID leg is skipped; uppercase query still matches
    link = ThorchainMidgard(transport=_transport).lookup("bitcoin", SRC_TXID)
    assert link is not None and link.dst_chain == "ethereum"


def test_midgard_no_match_returns_none():
    link = ThorchainMidgard(transport=lambda p: {"actions": []}).lookup("bitcoin", "deadbeef")
    assert link is None
