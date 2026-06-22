# Roadmap

The clean rule that decides what gets built now vs deferred:

> **Build what needs only engineering against public data. Defer what needs research.**

Phases 0–5 are entirely deterministic/heuristic and buildable now. Phases 6 (ML)
and 7 (privacy coins) are deferred — each is a project in its own right that
needs research first. They are numbered out of build order on purpose.

## Phase 0 — Foundations  *(done, in scaffold)*
Canonical models, Postgres schema, config, CLI, two BTC connectors.
**Deliverable:** `chainhound triage <address>`.

## Phase 1 — BTC UTXO tracing + clustering  *(done, in scaffold)*
Co-spend clustering + CoinJoin exclusion; six-heuristic change analysis with
confidence bands; peel-chain detection; follow-the-money graph.
**Deliverable:** working triage-and-trace CLI.

### Phase 1.5 — Thin trace viewer  *(optional bridge)*
A read-only web page that renders the `trace`/`peel` JSON as a graph
(Cytoscape.js). Not the full investigation UI — just eyes on the graph while the
engine matures. Pull it forward only if seeing the output speeds you up.

## Phase 2a — Label corpus  *(the remedy for "partial"; continuous infrastructure)*
Not a one-shot feature — a pipeline that never really finishes. Ingests public
attribution into the `label` table with source + confidence + refresh cadence.
This is the single biggest force-multiplier; it is the commercial vendors' moat.

Two ingestion modes (see DESIGN.md):
- **Bulk/scheduled** (no rate limit, small): OFAC SDN crypto list, GraphSense
  TagPacks, Etherscan name-tag community dumps, sanctioned/scam address repos.
- **On-demand/cached** (rate-limited, lazy): Chainabuse per-address.

Suggested start: OFAC + TagPacks (authoritative, low-noise) first; add the
noisier dumps once exposure works. Privacy-coin on/off-ramp endpoints are just
labels and land here now (the only privacy-coin work possible without Phase 7).

**Progress:**
- *OFAC SDN — done.* End-to-end slice: `chainhound/labels/` (a `LabelSource`
  interface, the `OFACSource` loader parsing Treasury `sdn.xml`, and an idempotent
  `store` that refreshes by source), wired into the CLI and into `triage`. Tagged
  `source="ofac"`, `category="sanctioned"`, `Near Certainty`, with the sanctioned
  entity name for glass-box provenance.
- *GraphSense TagPacks — done.* `TagPackSource` ingests the local TagPack corpus
  (`data/labels/tagpacks.tar.gz`, ~524k tags), inheriting header defaults per tag,
  mapping the GraphSense confidence taxonomy to bands and normalizing categories
  (`mixing_service`→`mixer`). **Privacy-coin on/off-ramps land here** via the
  corpus's XMR/ZEC exchange + mixing labels (the only privacy-coin work possible
  pre-Phase 7).
- *On-demand/cached mode — done.* `labels/ondemand.py` provides the
  DESIGN-mandated fetcher: a token-bucket limiter, exponential backoff, and a
  `label_cache` table so repeated lookups never re-hit the API. **Chainabuse**
  (`ChainabuseSource`) is the first consumer, key-gated via
  `CHAINHOUND_CHAINABUSE_KEY` and reached by `chainhound labels check`.
- *Generic dump loader — done.* `RepoSource` ingests any address-dump repo
  (scam/sanction lists, Etherscan name-tag dumps) from a small YAML manifest
  (lines or csv), so adding a vetted dump is data, not code.
- *Sources & CLI.* `labels/sources.py` registry; `labels sync --source|--all`,
  `labels check`, `labels lookup`, `labels sources`.
- *Remaining (ongoing):* select/vet specific community dump repos; run bulk syncs
  on a cadence (cron + idempotent re-pull — no daemon, per scope).

## Phase 2b — Exposure + pathfinding
Consumes the labels: counterparty (direct) and indirect (multi-hop) exposure,
summed into TRM-style rings. Glass-box provenance on every tag.

## Phase 3 — EVM / account tracing
Etherscan/BigQuery EVM (note: Etherscan free API is 5 req/s, 100k/day, key
required, and several chains are now paywalled — use Routescan/OKLink/Blockscout
where needed). Internal txns + traces, ERC-20/721 transfers, token swaps,
contract-function decoding via Sourcify + 4byte. On-demand fetch + cache + backoff.

## Phase 4 — Cross-chain matching  *(highest real-world value)*
Two deterministic tiers, both stored in `cross_chain_link` with method + confidence:
- **`api`** — read a bridge explorer for the src↔dst pair (Wormholescan,
  LayerZeroScan, deBridge, THORChain). Effectively another connector + a join.
- **`inferred`** — match outflow (chain A) to inflow (chain B) by asset
  equivalence + amount within fee tolerance + time window + known bridge
  contracts. The real novel heuristic work; automates today's manual matching.

## Phase 5 — Monitoring, hygiene, reporting + investigation UI
Watched-address detectors + alerts; graph hygiene (color, notes, hide infra,
presentation copy); dust/poisoning filter; court export (raw on-chain only).
**The interface:** an analyst-driven, incremental graph-building canvas
(Cytoscape.js/sigma.js over a FastAPI query layer) in the style of TRM Graph
Visualizer / Breadcrumbs — seed -> triage -> expand node by node -> exposure
rings + transfer table -> save/load per case. See ARCHITECTURE.md "Interface plan".

## Phase 6 — ML augmentation  *(DEFERRED — own project, needs research)*
Advisory, confidence-scored signal layered on the deterministic engine, never a
replacement. Candidates: entity-type classification of unlabeled clusters,
ML change-output prediction (extra vote into the noisy-OR), illicit-tx/anomaly
detection (Elliptic dataset + GNN), ML-assisted cross-chain linkage,
probabilistic CoinJoin de-mixing. Guardrails: tagged `source='ml'`, excluded
from court export, every prediction links back to its features.

## Phase 7 — Privacy coins  *(DEFERRED — own project, needs research)*
Monero/Zcash internals are cryptographically opaque; no tool traces them. The
only tractable work is labeling on/off-ramp endpoints (swap services, exchanges)
— and that piece lands in Phase 2a now. Full coverage research is deferred.
