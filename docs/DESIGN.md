# Design

## Principles

1. **Deterministic first.** Every capability in Phases 0–5 is rule/heuristic
   code. It is explainable, reproducible, and admissible — a judge can follow
   the reasoning. ML (Phase 6) is layered on top as *advisory* signal, never as
   a replacement for the deterministic evidence.

2. **Deterministic is not the same as certain.** Many heuristics are
   deterministic code that produces a *probabilistic* conclusion (optimal-change
   can false-positive; inferred cross-chain matches are educated guesses). So
   every conclusion carries a confidence band, and the contributing signals are
   stored for glass-box review.

3. **Glass-box provenance.** Every label, cluster, and verdict records its
   source and reasoning. For court export, third-party attribution can be
   stripped to leave raw on-chain data (mirroring the TRM "admissibility" note).

4. **Aggregate automatically.** The investigator should never hand-reconcile
   sources. Connectors normalize; the engine correlates.

## Confidence model

Bands: `Near Certainty (>=0.85) · High (>=0.65) · Moderate (>=0.40) · Low`.
Heuristic votes combine per output with **noisy-OR**:
`score = 1 - prod(1 - weight_i)`. Independent heuristics agreeing on the same
output drive confidence up, which is exactly the TRM guidance to apply several
heuristics rather than trusting one.

## Heuristic specifications

### Co-spend clustering (`heuristics/clustering.py`)
All input addresses of a non-CoinJoin transaction share an owner. Applied
transitively via union-find, a seed address expands into the wallet. Strongest
heuristic — `Near Certainty` for ordinary co-spends.

### CoinJoin exclusion (`heuristics/coinjoin.py`)
Detect equal-output mixers (>=3 equal-value outputs backed by >=3 distinct
inputs) and exclude them from clustering, so unrelated participants are never
merged. Conservative by design.

### Change analysis (`heuristics/change_analysis.py`)
Six independent heuristics vote for the change output:

| Heuristic | Signal | Weight |
|---|---|---|
| `self_change` | an input address reappears as an output | 0.95 |
| `multisig` | output multisig type matches the inputs | 0.60 |
| `optimal_change` | only output smaller than the smallest input | 0.65 |
| `address_type` | output address type matches the inputs | 0.55 |
| `address_reuse` | fresh output (others reused -> payment) | 0.45 |
| `round_payment` | the non-round output (round one is payment) | 0.40 |

`analyze_change` runs all six, combines with noisy-OR, returns the best output
plus the band and the signal list.

### Peel chain (`heuristics/peel_chain.py`)
Walk the change output forward hop by hop while the peel shape holds (2–3
outputs, change dominant >=60% of spent value). >=3 hops = a peel chain.

## Cross-chain matching (Phase 4 spec)

Two tiers, both stored in `cross_chain_link` with method + confidence:

- **Deterministic (`method='api'`):** pull the src↔dst pair directly from a
  bridge explorer (Wormholescan, LayerZeroScan, deBridge, THORChain). Highest
  confidence.
- **Inferred (`method='inferred'`):** when no bridge API exists, match an
  outflow on chain A to an inflow on chain B by asset equivalence + amount within
  a fee tolerance + a time window + known bridge contract addresses. Confidence
  scales with how tight the match is. This automates the manual value/time
  matching done by hand today.

## Correlation engine modules (target state)

- **Clustering** — co-spend (UTXO), deposit/funding heuristics (EVM).
- **Change analysis** — as above.
- **Cross-chain matching** — two tiers above.
- **Exposure** — counterparty (direct labeled neighbours) and indirect
  (multi-hop pathfinding) exposure, summed into TRM-style rings.
- **Signatures** — rule-based first (peel, fan-in, fan-out, rapid hops), ML
  later.
- **Attribution merge** — join all label sources with provenance + confidence.

## Label ingestion modes & rate limits

Sources split by *how they are fetched*, which determines time/space cost. The
local label corpus is small (a few hundred MB, indexed on `(chain, address)`);
the terabytes of chain data are never bulk-ingested — only the addresses an
investigation touches are fetched.

**Bulk/scheduled — no rate limit, refresh on a cadence:**
- OFAC SDN crypto list (Treasury file + community GitHub mirror)
- GraphSense TagPacks (git)
- Etherscan name-tag labels (NOT in the API — web-only; use a community dump)
- sanctioned/scam address repos (git)

Loaded by a batch job with idempotent upserts; re-pulls dedup.

**On-demand/cached — rate-limited, queried lazily for case addresses only:**
- Etherscan API for txns/traces (5 req/s, 100k/day, key required; some chains
  paywalled — fall back to Routescan/OKLink/Blockscout)
- Chainabuse per-address; bridge explorers per-tx

Wrapped by a shared fetcher with a token-bucket limiter, exponential backoff on
429, and a local cache table so repeated lookups never re-hit the API.

Implication: "ingest all sources" is safe — the heavy sources are downloads and
the API sources are lazy, so nothing gets hammered.
