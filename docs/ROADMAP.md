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

**Progress:**
- *Exposure module — done.* `analysis/exposure.py::compute_exposure` runs a
  bounded, **bidirectional counterparty BFS** over the `Provider` primitives — its
  own traversal, since `trace_from_tx` is tx-seeded change-trail-forward, not
  address-seeded bidirectional. Emits glass-box findings (path + value + label
  source/confidence) and TRM-style rings split by category and inbound/outbound.
  CLI: `chainhound exposure <addr> [--hops N] [--direction in|out|both]`.
  - **Confidence degrades with distance:** `BAND_SCORE × decay**(distance-1)`
    (decay 0.6), re-banded via `confidence_band` — direct preserves the band,
    floors to `Low` within ~3 hops, never inflates.
  - **Value:** exact at distance 1; bottleneck (min-edge) upper bound beyond
    (not precise taint — FIFO/haircut/poison deferred); full path retained.
  - **Hard bounds:** `hops`, `max_nodes`, `max_fanout` (a hub is not expanded
    through → `truncated=True`). 8 offline tests (direct / indirect / near-miss
    past the bound / high fan-out / ring math / degradation) with a fake provider
    + fake lookup.
  - *Live-proven (2026-06-23):* `exposure` on a low-activity neighbor of a
    sanctioned HYDRA MARKET address surfaced an inbound `sanctioned` ring —
    counterparty `3ES6pqCueDPCnC4hCqhhYuey6gyiRJZw6E`, **0.321 BTC**, Near
    Certainty (direct) — over live Blockstream data + the synced OFAC corpus.
- *On-demand source candidate:* TRM's free Sanctions API — a 2b/on-demand
  attribution source (wrap with the `OnDemandSource` fetcher), not a 2a bulk
  loader. Needs its **own "sanctions EXPOSURE" confidence seam**, not conflated
  with a direct OFAC listing.
- *Deferred:* precise multi-hop taint (FIFO/haircut/poison); EVM/account-model
  exposure (Phase 3 — the code is provider-agnostic, so it lands when an EVM
  provider exists); exposure persistence + the investigation UI (Phase 5).

## Phase 3 — EVM / account tracing
Etherscan/BigQuery EVM (note: Etherscan free API is 5 req/s, 100k/day, key
required, and several chains are now paywalled — use Routescan/OKLink/Blockscout
where needed). Internal txns + traces, ERC-20/721 transfers, token swaps,
contract-function decoding via Sourcify + 4byte. On-demand fetch + cache + backoff.

Runs in two stages: Stage 1 (connector availability research + canonical-model
fit) is approved; Stage 2 builds incrementally. **Default backend: keyless
Routescan** (Etherscan V2 / Blockscout via base-url + key); BigQuery deferred as
the opt-in bulk backend (its data is free but queries are billed per-TiB-scanned
and per-address point lookups are the wrong pattern for a warehouse).

**Progress:**
- *Slice 1 — minimal EVM Provider (native value) — done.* `connectors/evm.py`
  `EvmProvider` implements the `Provider` ABC over the Etherscan-compatible REST
  shape, defaulting to keyless Routescan; native value transfers normalize to
  single-transfer `Transaction`s (failed/zero-value dropped), EOA-vs-contract
  handles EIP-7702 delegation, and rate-limit/backoff reuse `labels/ondemand`
  (no new throttle code). Wired into `get_provider` (`ethereum`/`eth`/`evm`) +
  config (`CHAINHOUND_EVM_PROVIDER_URL`/`ETHERSCAN_KEY`/`EVM_CHAIN_ID`).
  - **Exposure on EVM — live-proven (2026-06-23).** All three legs confirmed:
    (1) offline `test_exposure_works_on_evm_with_zero_engine_changes` (ring forms
    on EVM-shaped data, **zero** edits to `exposure.py`); (2) the *same*
    provider-agnostic code produced a real `sanctioned` ring on BTC (HYDRA); and
    (3) **live EVM + label intersect** — `exposure` on `0x9369…` over live
    Routescan + the OFAC corpus surfaced an outbound `sanctioned` ring:
    `0x1da5821544e25c636c1417ba96ade4cf6d2f9b5a` = *OFAC SDN: SECONDEYE SOLUTION*,
    0.1188 ETH, distance 1, Near Certainty (and a second TagPack tag on the same
    address — multi-source, glass-box). Closed via the seeded recipe below.
  - **BTC↔EVM sanctions asymmetry (matters for Phase 4 + demos).** An OFAC *ETH*
    ring is structurally harder to stage than an OFAC *BTC* one: the ETH set is
    heavy with **Tornado Cash contracts** (verified present in the current SDN —
    *not* delisted, correcting an earlier assumption) that are extremely
    high-activity, colliding with *both* the 2b high-fan-out bound and the
    explorer's recent-tx window — so a real touch can fall out of view. The live
    proof therefore landed on a discrete sanctioned entity (SECONDEYE), not a TC
    pool. BTC's sanctioned set is discrete deposit addresses; EVM's skews to mixers
    with millions of interactions.
  - **Seeded-proof recipe (what closed it):** don't random-sample — take a labeled
    ETH address, find one address with a *direct, in-window* transfer to it
    (verify it sits within the explorer's recent window), and run `exposure` on
    that. Deterministic in a couple of targeted lookups.
- *Known limitations:* `get_address_transactions` reads the recent tx window
  (explorer pagination), and `tx_count`/`first_seen` are over that window — same
  shape as the BTC connector. `trace_from_tx` is UTXO-specific; on an account-model
  provider it now **raises `NotImplementedError`** (fails loud rather than
  returning a quietly-wrong change-trail graph). The full account-model forward
  trace is deferred. (`peel` is UTXO-only too but fails safe — `get_spending_tx`
  returns `None` on EVM, so it finds no chain rather than misleading.)
- *Slice 2 — ERC-20/721 — done.* `TxIO.asset` (additive; BTC paths unchanged)
  carries the token; `get_address_transactions` emits one transfer-grained
  `Transaction` per `tokentx`/`tokennfttx` (NFTs count one unit), and exposure
  produces **per-asset rings** (`(category, direction, asset)`) so ETH and token
  exposure never mix units — no traversal changes.
- *Slice 3 — internal txns/traces — done.* `txlistinternal` value-bearing internal
  calls normalize like native (failed/zero dropped), surfacing contract-mediated
  value flow the top-level txlist misses.
- *Slice 4 — contract decoding — done.* `analysis/decode.py::decode_input` over
  Sourcify's keyless 4byte service (selector→signature, prefers verified-contract
  matches over spam); `Transaction.method` is populated free from the explorer's
  `functionName`. Full ABI argument decoding (needs a keccak dep) is deferred.
- *Address-casing fix (2026-06-23).* EVM labels were attributed only when stored
  lowercase: the connector lowercases addresses but TagPack/dump labels are stored
  as-published (often checksummed), so an exact `WHERE address` lookup silently
  missed them (OFAC ETH is lowercase, so the SECONDEYE proof was unaffected — but
  checksummed EVM labels were dropped). Fixed with a chain-aware
  `models.normalize_address` applied on label **write and lookup** (and the cache
  key): EVM hex → lowercase, base58/bech32 (BTC, **Tron**) untouched. **EVM
  attribution is only clean as of this change.** Tier-E3 round-trip on Postgres.
- *Phase 3 status:* slices 1–4 complete and tested; the keyless Routescan default
  + exposure-on-EVM are live-proven. Remaining for the phase: the account-model
  forward-trace guard's full implementation, ABI arg decoding, and the opt-in
  BigQuery EVM bulk backend — all deferred.

## Phase 4 — Cross-chain matching  *(highest real-world value)*
Two deterministic tiers, both stored in `cross_chain_link` with method + confidence:
- **`api`** — read a bridge explorer for the src↔dst pair (Wormholescan,
  LayerZeroScan, deBridge, THORChain). Effectively another connector + a join.
- **`inferred`** — match outflow (chain A) to inflow (chain B) by asset
  equivalence + amount within fee tolerance + time window + known bridge
  contracts. The real novel heuristic work; automates today's manual matching.

**Progress:**
- *Inferred tier — done.* `analysis/crosschain.py`: `score_match`/`infer_links`
  over `Transfer`s — gate on asset peg-equivalence, require amount within a fee
  tolerance (human-decimal) and dst within a time window after src, lift on a
  known-bridge touch. Amount+time alone cap at **Moderate** (never certain); a
  known bridge → High/Near Certainty. Results carry glass-box `matched_on` and
  persist to `cross_chain_link` (`save_cross_chain_link`). Live-proven on the Pando
  **C1** hop: BTC 53.5 → RENBTC 53.39 → inferred link, `rel_delta` 0.2%, **Moderate**
  (honest — RenBridge is defunct, so it isn't in the bridge registry; would be High
  with the bridge registered).
- *Api tier — one connector done.* `bridges.py`: `BridgeExplorer` ABC +
  `ThorchainMidgard` (keyless `/v2/actions?txid=`). **Live-proven**: a real swap
  resolved `bitcoin → ethereum` (USDC), `method=api`, Near Certainty. Wormholescan
  fits the same ABC (future impl).
- *CLI:* `chainhound crosschain --src-chain --src-txid {--api | inferred}`.
- *Deferred:* auto-discovery of the dst without an anchor/bridge API (needs a chain
  index/BigQuery); Wormhole/deBridge/LayerZero connectors (ABC ready); a TRON
  connector + full C2/C3 multi-swap (WBTC→DAI→USDD) asset tracing.

## Phase 5 — Monitoring, hygiene, reporting + investigation UI
Watched-address detectors + alerts; graph hygiene (color, notes, hide infra,
presentation copy); dust/poisoning filter; court export (raw on-chain only).
**The interface:** an analyst-driven, incremental graph-building canvas
(Cytoscape.js/sigma.js over a FastAPI query layer) in the style of TRM Graph
Visualizer / Breadcrumbs — seed -> triage -> expand node by node -> exposure
rings + transfer table -> save/load per case. See ARCHITECTURE.md "Interface plan".

**Progress:**
- *Slice 1 — FastAPI query-layer backbone — done.* New `chainhound_server/`
  platform package (the dependency arrow points one way: it imports `chainhound`,
  never the reverse). A stateless query API wraps the engine's read operations —
  `GET /health`, `/triage`, `/trace` (UTXO-only; account-model → 422), `/peel`,
  `/exposure` (503 without the label corpus), `/labels`, and `POST /crosschain`
  (api + inferred). Provider/label resolution is injected via FastAPI dependency
  factories so routes are offline-testable with fake providers
  (`tests/server/test_routes.py`). Boots with `python -m chainhound_server`
  (uvicorn). Packaging: `server` extra + `chainhound-server` script.
- *Slice 2 — case persistence (save/load) — done.* `chainhound_server/store.py`
  + a `/cases` router: create/list/load/delete a case (`investigation`), pin
  notes (`case_note`), and upsert per-element graph-hygiene state — color, hidden,
  note (`graph_element`). `get_case` returns the full workspace (case + notes +
  elements) — the "load". Hygiene rows upsert in place via a new
  `UNIQUE (case_id, element_id)` index. The store builds on
  `chainhound.db.connect` with an injectable `connect`; routes inject it via a
  `get_connect` dependency, so everything is offline-testable (scripted fake
  connection) with a DB-gated live round-trip
  (`tests/server/test_cases_integration.py`).
- *Slice 3 — Cytoscape investigation canvas — done.* A vanilla-JS single-page app
  (`chainhound_server/static/`, Cytoscape.js via CDN, no build step) served at `/`
  with assets under `/static` (mounted so they never shadow API routes). The
  analyst flow over the existing endpoints: search a seed txid → `/trace` plots
  the graph (tx vs address nodes; change edges colored by confidence band, payment
  edges dashed); click an address → `/triage` panel + `/exposure` rings; click a
  tx → `/peel`; double-click a tx → trace deeper. Graph hygiene (color / hide /
  note) is editable per element and persists via the Slice-2 `/cases` save/load
  (New/Save/Load + case picker). Transfer table with find-in-table. Backend
  serving is tested (`tests/server/test_static.py`); the JS is a thin client over
  tested endpoints.
- *Decisions locked / used:* UI = Cytoscape.js; frontend = vanilla JS static files
  served by FastAPI (no Node build step).
- *Remaining:* watched-address detectors + alerts (`watch`/`alert` — deferred by
  request), richer graph-hygiene UX (auto hide-infra / dust-poisoning filter), and
  court export (raw on-chain only).

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
