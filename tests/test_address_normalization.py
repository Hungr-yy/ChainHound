"""Chain-aware address normalization (EVM-casing bug, Tier E3).

EVM addresses are hex and case-insensitive, so they normalize to lowercase. BTC
base58 and Tron base58 are case-SIGNIFICANT and must pass through byte-for-byte.
"""
from chainhound.models import normalize_address

# A checksummed (mixed-case) Ethereum address and its canonical lowercase form.
ETH_CHECKSUM = "0x1Da5821544E25c636c1417Ba96Ade4Cf6D2f9B5a"
ETH_LOWER = "0x1da5821544e25c636c1417ba96ade4cf6d2f9b5a"
BTC_BASE58 = "1Q9UMz5aGanLxgqQ2j6t9JNQVSiCwGCi9b"      # case-significant
TRON_BASE58 = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"     # account-model, base58


def test_evm_checksummed_address_lowercased():
    assert normalize_address("ethereum", ETH_CHECKSUM) == ETH_LOWER


def test_evm_already_lowercase_unchanged():
    assert normalize_address("ethereum", ETH_LOWER) == ETH_LOWER


def test_evm_detected_by_0x_prefix_even_off_known_chains():
    # any 0x-hex address is EVM and safe to lowercase, regardless of chain label
    assert normalize_address("evm-999", ETH_CHECKSUM) == ETH_LOWER
    assert normalize_address("unknown", ETH_CHECKSUM) == ETH_LOWER


def test_btc_base58_is_never_altered():
    # lowercasing would corrupt the base58check checksum
    assert normalize_address("bitcoin", BTC_BASE58) == BTC_BASE58


def test_tron_base58_is_never_altered_despite_being_account_model():
    # Tron is account-model but case-significant base58 — must NOT be lowercased
    assert normalize_address("tron", TRON_BASE58) == TRON_BASE58


def test_empty_address_passthrough():
    assert normalize_address("ethereum", "") == ""
    assert normalize_address("bitcoin", None) is None
