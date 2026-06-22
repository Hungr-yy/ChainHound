# CLAUDE.md

Guidelines for AI coding agents (and humans) working in the ChainHound repository.
Claude Code auto-loads this file at the start of every session — keep it concise and high-signal.

## Golden rules
- **Commit after every logical change.** Don't batch unrelated edits. Use conventional-commit format.
- **TDD.** Write the failing test first, then the minimal code to pass, then refactor.
- **Deterministic-first.** ChainHound infers from public data with explicit heuristics and queries. No ML in the core engine (deferred to Phase 6, advisory-only). If a task needs research rather than engineering, stop and flag it instead of guessing.
- **Confidence bands on every inference** (Near Certainty → High → Probable → Possible → Low). Never present a heuristic guess as established fact.
- **Glass-box attribution.** Every label and verdict records its source, method, and confidence. Court export contains raw on-chain data only — no third-party attribution.
- **Keep docs in sync.** After completing a phase or major feature, update `docs/ROADMAP.md` to mark it done.

## Project overview
ChainHound is a deterministic-first, open-source on-chain (blockchain) forensics toolkit in Python. It triages addresses, traces fund flows, clusters addresses, analyzes change outputs, and is building toward attribution labels and cross-chain links. Phases 0–1 (BTC UTXO triage, tracing, clustering, change analysis) are complete; **Phase 2a (label corpus) is next.**

**Stack:** Python 3.10+, pytest. Postgres is *optional* (only for persistence / labels; schema in `sql/schema.sql`). No Docker, no web framework, no background workers — this is a library + CLI.

## Layout
- `chainhound/models.py` — chain-agnostic types (`Transaction`, `TxIO`, `AddressSummary`, confidence bands); `classify_btc_address()`
- `chainhound/connectors/` — data sources behind a `Provider` ABC; `get_provider()` factory. `blockstream_btc.py` (keyless Esplora, default), `bigquery_btc.py` (bulk).
- `chainhound/heuristics/` — `clustering.py` (co-spend + UnionFind), `coinjoin.py` (CoinJoin detect/exclude), `change_analysis.py` (six heuristics combined with noisy-OR), `peel_chain.py`
- `chainhound/analysis/` — `triage.py` (build-a-picture + service flags), `trace.py` (follow-the-money graph)
- `chainhound/cli.py` — subcommands: `triage`, `trace`, `peel`, `initdb` (and `labels` in Phase 2a)
- `chainhound/config.py` — env: `CHAINHOUND_PROVIDER`, `CHAINHOUND_GCP_PROJECT`, `CHAINHOUND_DATABASE_URL`
- `sql/schema.sql` — canonical Postgres schema (address, transaction, transfer, cluster, label, cross_chain_link, investigation, …)
- `docs/` — ARCHITECTURE, DESIGN, ROADMAP, DETECTION_SIGNATURES
- `tests/` — pytest; mirror `tests/test_change_analysis.py`

## Commands
```bash
pip install -e ".[live,dev]"          # editable install + live & dev extras
python -m pytest -q                    # run tests (use this form, not bare pytest)
chainhound triage <address>            # or: python -m chainhound.cli triage <address>
chainhound trace <txid> --hops 2
chainhound peel  <txid> --max-hops 30
chainhound initdb                      # apply sql/schema.sql (needs CHAINHOUND_DATABASE_URL)
black chainhound tests --line-length 88
```
Connectors hit the live network (Blockstream is keyless). **No API keys** are needed for triage/trace or for Phase 2a's OFAC + TagPacks sources.

## Code style
- PEP 8, formatted with `black` (line length 88).
- Naming: `UPPER_SNAKE_CASE` constants, `PascalCase` classes, `snake_case` functions/vars, `_leading_underscore` for private helpers.
- Double quotes; f-strings for interpolation.
- Imports: stdlib, then third-party, then local (blank line between groups); prefer `from module import Name`.
- Module-level logger: `logger = logging.getLogger(__name__)`. Don't `print` from library code.
- Input validation: normalize, check against an explicit allowed set, raise `ValueError` with a clear, actionable message. Handle network/connector errors explicitly (return `found=False` or raise a typed error) — never let a bad address crash a trace.

## TDD & testing
- A new heuristic, loader, or connector ships with **a synthetic fixture that MUST trigger it and a near-miss that MUST NOT.** (Detection signatures double as regression fixtures — see `docs/DETECTION_SIGNATURES.md`.)
- Keep unit tests deterministic and offline: build canonical model objects directly; do not call live connectors in unit tests.
- Run `python -m pytest -q` before every commit; keep it green.

## Git commits
Format: `type(scope): description`. Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.
Examples:
- `feat(labels): add OFAC SDN loader`
- `test(change): add near-miss fixture for round-payment heuristic`
- `docs(roadmap): mark Phase 1 complete`

Commit meta-guidance (this file, `LICENSE`, `.gitignore`) as its own `chore` commit, separate from feature work.

## Architecture notes
- Connectors normalize source-specific JSON into the canonical models; downstream code stays chain/source-agnostic. **Known gap:** the Blockstream connector returns null `first_seen`/`last_seen` (BigQuery provides them).
- `change_analysis` combines six heuristics with a noisy-OR into one confidence-banded verdict; never hard-label change as certain.
- `clustering` uses co-spend with CoinJoin transactions **excluded** (the co-spend assumption breaks inside a CoinJoin).
- **Phase 2a labels:** ingest into the `label` table with source + confidence. Two modes — *bulk/scheduled* (OFAC SDN, GraphSense TagPacks; file/git; no rate limit) and *on-demand/cached* (Chainabuse; token-bucket + backoff). Build **OFAC end-to-end first** (fetch → upsert → test → `chainhound labels sync`), then add TagPacks.
- Downloaded data and `.env` are never committed (see `.gitignore`).

## Out of scope (defer — don't build unless ROADMAP says so)
- ML / anomaly models in the core engine (Phase 6, advisory-only, tagged `source="ml"`, excluded from court export).
- Privacy-coin internals (Phase 7); only on/off-ramp labels are in scope, via Phase 2a.
- Docker, a web server, or background workers.
