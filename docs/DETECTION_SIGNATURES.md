# Detection signatures

Criminal methodology evolves, so a fixed list of named typologies dates fast and
is the wrong thing to build against. The durable insight: **every typology is a
composition of a few primitives** — clustering, change analysis, exposure,
cross-chain matching, labels. Peel chain, smurfing, chain-hop, consolidation are
not bespoke features; each is a *pattern over those primitives*.

So we build **primitive-driven, not typology-driven**, and express each typology
as a small, parameterized signature (a rule/query over the graph). New methods
become new signatures, not new architecture. These also double as **regression
fixtures** — each signature ships with a synthetic transaction set that must
trigger it.

This file is a living catalogue, not a coverage contract.

## Primitives (the engine)
- **clustering** — co-spend (UTXO), deposit/funding (EVM)
- **change analysis** — six heuristics, confidence-banded
- **exposure** — counterparty (direct) + indirect (multi-hop pathfinding)
- **cross-chain match** — deterministic (bridge API) + inferred (value/time)
- **labels** — attribution with source + confidence

## Signatures (each = a query over primitives)

| Signature | Expressed as | Primitives | Params |
|---|---|---|---|
| Peel chain | dominant change output continues N hops, small payment each hop | change analysis | min_hops, change_dominance |
| Consolidation / fan-in | many inputs -> one output (VASP deposit) | graph degree | min_inputs |
| Fan-out / smurfing | one input -> many small outputs | graph degree + amount | min_outputs, max_amount |
| Structuring | repeated transfers just under a threshold | amount + velocity | threshold, window |
| Chain-hop | outflow links to inflow on another chain | cross-chain match | fee_tol, time_window |
| Layered swap | DEX swap -> bridge -> swap, often to dodge freezes | cross-chain + EVM trace | n/a |
| Mixer touch | funds enter/exit a labeled mixer pool | labels + exposure | mixer label set |
| Rapid hops | short inter-tx intervals along a trail | change analysis + timing | max_interval |
| Sanctioned exposure | path to an OFAC-labeled address | exposure + labels | max_hops |
| Privacy on/off-ramp | transfer to/from a labeled XMR swap endpoint | labels | endpoint label set |

## How to add a signature
1. Write the rule/query against the primitives (no new engine code if possible).
2. Add a synthetic fixture to `tests/` that must trigger it (and a near-miss
   that must not).
3. Register it so the monitoring layer (Phase 5) can run it as a detector.
