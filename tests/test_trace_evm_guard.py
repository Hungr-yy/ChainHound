"""trace_from_tx must refuse account-model (EVM) providers, not silently mislead.

Its change-trail logic is UTXO-specific; running it on EVM would return a
plausible-but-wrong graph. Until an account-model trace exists, it fails loud.
"""
import pytest

from chainhound.analysis.trace import trace_from_tx


class _AccountProvider:
    model = "account"
    chain = "ethereum"

    def get_transaction(self, txid):
        raise AssertionError("guard must trip before any fetch")

    def get_spending_tx(self, txid, vout):
        return None


def test_trace_refuses_account_model_provider():
    with pytest.raises(NotImplementedError):
        trace_from_tx(_AccountProvider(), "0xabc")
