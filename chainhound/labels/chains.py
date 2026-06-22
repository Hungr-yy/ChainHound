"""Shared currency-code → ChainHound chain-name mapping.

Different sources name the same chain differently (OFAC's ``XBT`` vs TagPacks'
``BTC``), so loaders normalize through one map. Unknown or token-only codes fall
back to the lowercased code so a newly listed asset never crashes a sync.
"""
from __future__ import annotations

# Keys are upper-cased currency codes; values are ChainHound chain names.
_CURRENCY_TO_CHAIN = {
    "XBT": "bitcoin",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "ETC": "ethereum-classic",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "BSV": "bitcoin-sv",
    "BTG": "bitcoin-gold",
    "DASH": "dash",
    "DOGE": "dogecoin",
    "DGB": "digibyte",
    "XVG": "verge",
    "XMR": "monero",
    "ZEC": "zcash",
    "XRP": "ripple",
    "RIPPLE": "ripple",
    "TRX": "tron",
    "SOL": "solana",
    "ADA": "cardano",
    "DOT": "polkadot",
    "AVAX": "avalanche",
    "MATIC": "polygon",
    "ARB": "arbitrum",
    "BSC": "bsc",
    "BEP": "bsc",
    # Tokens (chain is ambiguous); default the common stablecoins to Ethereum.
    "USDT": "ethereum",
    "USDC": "ethereum",
}


def currency_to_chain(code: str) -> str:
    """Normalize a source currency code to a ChainHound chain name."""
    if not code:
        return "unknown"
    return _CURRENCY_TO_CHAIN.get(code.strip().upper(), code.strip().lower())
