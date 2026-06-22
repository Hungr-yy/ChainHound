# ChainHound

A deterministic-first, open-source on-chain forensics toolkit. It automates the
cross-source aggregation an investigator otherwise does by hand: connectors pull
public OSINT into one canonical store, and a correlation engine runs clustering,
change analysis, peel-chain detection, and (later) cross-chain matching as code.

This is the Phase 0 + Phase 1 scaffold (BTC triage + UTXO tracing). See
`docs/ROADMAP.md` for the full plan through cross-chain correlation and an
advisory ML layer.

## Quick start

```bash
pip install -e ".[live,dev]"          # blockstream connector + tests
python -m pytest -q                   # run the heuristic tests
chainhound triage 3P91G6V8CurGLRtJgQmdNvkZ49s7GNMEcT
chainhound trace <txid> --hops 2
chainhound peel  <txid> --max-hops 30
```

The default connector (Blockstream/Esplora) needs no credentials but does need
network access to `blockstream.info`. For bulk/historical work:

```bash
export CHAINHOUND_PROVIDER=bigquery CHAINHOUND_GCP_PROJECT=your-project
# requires google-cloud-bigquery + GCP credentials
```

Optional persistence:

```bash
export CHAINHOUND_DATABASE_URL=postgresql://localhost/chainhound
chainhound initdb                      # creates sql/schema.sql
```

## What works today (Phase 0–1)

- Address triage with service-likeness flags.
- Co-spend clustering with union-find and CoinJoin exclusion.
- Change analysis: six heuristics (self-change, optimal/nominal spend, address
  type, multisig, round payment, reuse) combined with noisy-OR into a confidence
  band.
- Peel-chain detection along the change trail.
- Follow-the-money trace graph (JSON), edges tagged change/payment + confidence.

## Layout

```
chainhound/
  models.py            canonical chain-agnostic types
  config.py            env-based config
  connectors/          Provider interface + Blockstream + BigQuery
  heuristics/          clustering · coinjoin · change_analysis · peel_chain
  analysis/            triage · trace
  db.py                Postgres persistence helper
  cli.py               triage / trace / peel / initdb
sql/schema.sql         canonical schema (supports the full roadmap)
docs/                  ARCHITECTURE · DESIGN · DETECTION_SIGNATURES · ROADMAP
tests/                 heuristic unit tests
```

## Design stance

Deterministic, explainable, court-ready first; ML strictly advisory and added
last (Phase 6). Every probabilistic conclusion carries a confidence band and its
contributing signals. See `docs/DESIGN.md`.

## Credits & prior art

ChainHound stands on established open research and public data sources:

- **[GraphSense](https://github.com/graphsense)** — open cryptoasset analytics
  platform; informs the clustering and address-graph model.
- **[blockchain-etl](https://github.com/blockchain-etl)** — open ETL pipelines
  behind the BigQuery public datasets used by the bulk connector.
- **Meiklejohn et al.** — the change-analysis heuristics derive from "A Fistful
  of Bitcoins" (IMC 2013).
- **[OFAC SDN](https://sanctionssearch.ofac.treas.gov/)** and
  **[Chainabuse](https://www.chainabuse.com/)** — public sanctions and
  scam/abuse reports for the label corpus (Phase 2).

## Scope honesty

Privacy coins (Monero), Lightning/off-chain channels, and off-chain OTC
settlement are blind spots for every tool, this one included — see
`docs/DETECTION_SIGNATURES.md`. The high-value investment areas are the label
corpus (Phase 2) and cross-chain matching (Phase 4).
