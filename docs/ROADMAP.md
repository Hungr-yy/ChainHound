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
- *Persistence — live-proven (2026-06-23).* The full attribution loop was run
  end-to-end against a real Postgres 16, not just fake-connection unit tests:
  - `initdb` applies `sql/schema.sql` cleanly; the live OFAC fetch
    (`treasury.gov/ofac/downloads/sdn.xml`, ~28 MB) parses to **798**
    crypto-address labels (520 BTC / 188 ETH / 50 TRON / …); `labels sync
    --source ofac` upserts them to **791** distinct rows (7 duplicate keys
    collapsed), and a re-sync stays at 791 — idempotent (~49 s, fetch-dominated).
  - Closed loop: `labels lookup 123WBUDmSJv4GctdVEz6Qq6z8nXSKrJ4KX` →
    *OFAC SDN: HYDRA MARKET (sanctioned, ofac, Near Certainty)*, and `triage` of
    that address attaches `["OFAC SDN: HYDRA MARKET"]` (`found: true`).
  - Bulk volume: `labels sync --source tagpack` parses 524,170 tags and upserts
    them to **508,100** distinct rows in ~60 s (parse ~44 s; the 524k-row
    `executemany` upsert is the rest — fast enough, no COPY needed).
  - SQL verified on live psycopg3: the `ON CONFLICT … DO UPDATE` label upsert on
    the partial unique index, the `fetch_cache` JSONB body + `expires_at`
    freshness, `replace_address`, and `with connect()` commit/close.
  - Locked in by `tests/test_labels_integration.py` (skipped unless
    `CHAINHOUND_DATABASE_URL` is set; isolated in a dropped-on-teardown schema).
- *Design hardening (folded from the alternate branch):* per-row `ON CONFLICT`
  upsert (incremental refresh without clearing a source); a dedicated `labels`
  packaging extra (`live` is connectors-only again); and a general
  `fetch_cache(source, request_key)` JSONB cache.
- *Deferred (by design):* vet/wire specific scam/sanction & Etherscan dumps (data,
  not code — `RepoSource` is ready); Chainabuse live `_fetch` (needs a partner API
  key); OFAC program/UID metadata (court-export enhancement, needs a schema
  decision); a refresh daemon (use the cron recipe in DESIGN.md — no scheduler).
- *Safe per-source prune (deferred — the safety rule matters more than the
  feature):* the upsert refreshes/adds but never deletes, so labels delisted
  upstream linger. A prune must be **per-source** and run **only after a
  successful, non-empty fetch** — never on a fetch failure, an empty parse, or a
  partial load — and must **refuse to prune when the new set is suspiciously
  smaller than the stored set** (a threshold guard), so a bad OFAC fetch can't
  wipe the sanctions corpus. Do **not** ship the naive "delete-everything-not-in-
  the-new-set" version.
- *Point-in-time provenance (the real home for "don't lose history"):* if
  "this address was sanctioned at the time" ever needs proving, that is an
  **append-only audit** concern — a `delisted_at` / history row — not a reason to
  avoid pruning. It's a separate, and arguably more valuable, feature than prune,
  and the one place the don't-lose-history instinct should actually be satisfied.

## Phase 2b — Exposure + pathfinding
Consumes the labels: counterparty (direct) and indirect (multi-hop) exposure,
summed into TRM-style rings. Glass-box provenance on every tag.
- *On-demand source candidate:* TRM's free Sanctions API — a 2b/on-demand
  attribution source (wrap with the `OnDemandSource` fetcher), not a 2a bulk loader.

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
