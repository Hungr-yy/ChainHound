"""Live, opt-in validation harness — grade ChainHound's heuristics against the TRM
course ground truth in CASES.md.

This is a MEASUREMENT + findings report, not a gate. Each case is graded only
against its tier (see CASES.md "How to grade honestly"), so a label-corpus gap
reads as COVERAGE-MISS, never a heuristic FAIL. We never tune thresholds to pass.

Run it (opt-in, makes live calls):

    CHAINHOUND_VALIDATION=1 python -m tests.Validation.harness

It prints a per-case report and writes docs/VALIDATION.md.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from chainhound.connectors import get_provider
from chainhound.connectors.blockstream_btc import API as BTC_API, BlockstreamBTC
from chainhound.analysis.triage import triage_address
from chainhound.heuristics.change_analysis import analyze_change
from chainhound.heuristics.peel_chain import trace_peel_chain
from chainhound.heuristics.clustering import cluster_addresses

# Verdicts
PASS = "PASS"
FAIL = "FAIL"
COVERAGE_MISS = "COVERAGE-MISS"
SKIP = "SKIP"
ERROR = "ERROR"
DIAGNOSTIC = "DIAGNOSTIC"  # not a course verdict; excluded from the pass/fail tally

_GRADED = {PASS, FAIL, COVERAGE_MISS, SKIP, ERROR}

BTC_REPORT = 100_000_000  # sats per BTC

# --- ground-truth constants (from CASES.md) ---------------------------------
A1_ADDR = "3P91G6V8CurGLRtJgQmdNvkZ49s7GNMEcT"
ORIGIN = "bc1qjnsx0sdxksh4w2azwu5ngr8sax46vcu52ljfcx"
A2_PREFIX, A2_SUFFIX = "bc1q32", "nl80"
A3_TERMINAL = "bc1qr5kgpg5ddn8tac254s3f0xjtj4749xayq6ua3y"
A3_TERMINAL_SATS = 5_350_000_000          # 53.5 BTC
A3_TOLERANCE_SATS = 100_000_000           # ± 1 BTC
DUST_SATS = 100_000                       # "significant" outbound spend floor


@dataclass
class CaseResult:
    case: str
    verdict: str
    summary: str
    expected: str = ""
    actual: str = ""
    detail: list[str] = field(default_factory=list)


def _safe(case_id: str, fn: Callable[[], CaseResult]) -> CaseResult:
    """Run a case; a network/harness failure is ERROR, never a heuristic FAIL."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - measurement harness, surface anything
        return CaseResult(case_id, ERROR, f"{type(exc).__name__}: {exc}")


# --- Tier A ------------------------------------------------------------------

def case_a1(btc) -> CaseResult:
    s = triage_address(btc, A1_ADDR)
    ok = (
        s.get("total_received") == 520_033_909
        and s.get("total_sent") == 520_033_909
        and s.get("tx_count") == 141
        and s.get("address_type") == "p2sh"
        and s.get("likely_service") is True
    )
    return CaseResult(
        "A1", PASS if ok else FAIL, "triage / activity picture",
        expected="recv==sent==520033909, tx_count==141, p2sh, likely_service",
        actual=(
            f"recv={s.get('total_received')} sent={s.get('total_sent')} "
            f"tx_count={s.get('tx_count')} type={s.get('address_type')} "
            f"likely_service={s.get('likely_service')}"
        ),
    )


def _memo(fn: Callable):
    cache: dict = {}

    def g(txid):
        if txid not in cache:
            cache[txid] = fn(txid)
        return cache[txid]

    return g


def first_significant_spend(btc):
    """Pin the origin's first significant outbound spend — the tx the course plots.

    Outbound = origin appears as an input; significant = above the dust floor;
    first = earliest by timestamp. The caller prints the chosen txid so it can be
    verified (a wrong pin makes an A2/A3 verdict meaningless).
    """
    txs = btc.get_address_transactions(ORIGIN, limit=50)
    spends = [
        t for t in txs if ORIGIN in t.input_addresses and t.total_out >= DUST_SATS
    ]
    if not spends:
        return None
    spends.sort(key=lambda t: t.timestamp)
    return spends[0]


def case_a2(btc) -> CaseResult:
    tx = first_significant_spend(btc)
    if tx is None:
        return CaseResult("A2", ERROR, "no significant outbound spend from origin")
    v = analyze_change(tx)
    if v.output_index is None:
        return CaseResult(
            "A2", FAIL, "change identification (the core heuristic)",
            expected=f"predicted change starts {A2_PREFIX} & ends {A2_SUFFIX}, band >= High",
            actual="no change output predicted",
            detail=[f"pinned txid={tx.txid}"],
        )
    addr = tx.outputs[v.output_index].address or ""
    ok = (
        addr.startswith(A2_PREFIX)
        and addr.endswith(A2_SUFFIX)
        and v.band in ("High", "Near Certainty")
    )
    sigs = "; ".join(
        f"{s.heuristic}@{s.weight}->out{s.output_index}" for s in v.signals
    ) or "none"
    return CaseResult(
        "A2", PASS if ok else FAIL, "change identification (the core heuristic)",
        expected=f"predicted change starts {A2_PREFIX} & ends {A2_SUFFIX}, band >= High",
        actual=f"predicted={addr} band={v.band} score={v.score:.3f}",
        detail=[f"pinned txid={tx.txid}", f"signals: {sigs}"],
    )


def case_a3(btc) -> CaseResult:
    tx = first_significant_spend(btc)
    if tx is None:
        return CaseResult("A3", ERROR, "no significant outbound spend from origin")
    get_tx = _memo(btc.get_transaction)
    chain = trace_peel_chain(tx.txid, get_tx, btc.get_spending_tx, max_hops=60)
    hop_addrs: list[tuple] = []
    hit = None
    for h in chain.hops:
        htx = get_tx(h.txid)
        caddr = (
            htx.outputs[h.change_index].address
            if htx and h.change_index < len(htx.outputs)
            else None
        )
        hop_addrs.append((caddr, h.change_value))
        if caddr == A3_TERMINAL:
            hit = h
    term_addr, term_val = hop_addrs[-1] if hop_addrs else (None, 0)
    within = hit is not None and abs(hit.change_value - A3_TERMINAL_SATS) <= A3_TOLERANCE_SATS
    ok = chain.is_peel_chain and within
    return CaseResult(
        "A3", PASS if ok else FAIL, "peel-chain detection",
        expected=f"peel reaches {A3_TERMINAL} with 53.5 BTC +/- 1",
        actual=(
            f"is_peel_chain={chain.is_peel_chain} hops={chain.length} "
            f"terminal={term_addr} ({term_val / BTC_REPORT:.2f} BTC) "
            f"target_hit={'%.2f BTC' % (hit.change_value / BTC_REPORT) if hit else 'no'}"
        ),
        detail=[f"start txid={tx.txid}"],
    )


def case_a4(btc) -> CaseResult:
    tx = first_significant_spend(btc)
    if tx is None:
        return CaseResult("A4", ERROR, "no significant outbound spend from origin")
    get_tx = _memo(btc.get_transaction)
    chain = trace_peel_chain(tx.txid, get_tx, btc.get_spending_tx, max_hops=60)
    hop_txs = [t for t in (get_tx(h.txid) for h in chain.hops) if t]
    peel_addrs = []
    for h in chain.hops:
        htx = get_tx(h.txid)
        if htx and h.change_index < len(htx.outputs) and htx.outputs[h.change_index].address:
            peel_addrs.append(htx.outputs[h.change_index].address)
    if len(peel_addrs) < 2:
        return CaseResult(
            "A4", ERROR, "too few peel addresses to grade clustering",
            detail=[f"peel_addrs={len(peel_addrs)}"],
        )
    res = cluster_addresses(hop_txs)
    origin_cluster = res.cluster_of(ORIGIN)
    terminal = peel_addrs[-1]
    # STRICT: affirmatively confirm co-spend abstained — not merely that it ran.
    merged_all = any(set(peel_addrs).issubset(c) for c in res.clusters.values())
    terminal_with_origin = terminal in origin_cluster
    ok = (not merged_all) and (not terminal_with_origin)
    biggest = max((len(c) for c in res.clusters.values()), default=1)
    return CaseResult(
        "A4", PASS if ok else FAIL, "co-spend abstains (must NOT merge the peel)",
        expected="peel hops left in separate co-spend clusters (abstention)",
        actual=(
            f"peel_addrs={len(peel_addrs)} clusters={len(res.clusters)} "
            f"biggest_cluster={biggest} terminal_in_origin_cluster={terminal_with_origin} "
            f"all_peel_merged={merged_all}"
        ),
        detail=[f"origin cluster size={len(origin_cluster)}"],
    )


# --- report ------------------------------------------------------------------

def _endpoints() -> dict:
    eps = {"btc": BTC_API}
    try:
        eps["evm"] = get_provider("ethereum").base_url
    except Exception:  # pragma: no cover - provider import/config issue
        eps["evm"] = "(unavailable)"
    try:
        from chainhound.analysis.decode import FOURBYTE_URL
        eps["fourbyte"] = FOURBYTE_URL
    except Exception:  # pragma: no cover
        eps["fourbyte"] = "(unavailable)"
    return eps


def run_all() -> list[CaseResult]:
    btc = BlockstreamBTC()
    return [
        _safe("A1", lambda: case_a1(btc)),
        _safe("A2", lambda: case_a2(btc)),
        _safe("A3", lambda: case_a3(btc)),
        _safe("A4", lambda: case_a4(btc)),
    ]


def render_report(results: list[CaseResult]) -> str:
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    eps = _endpoints()
    lines = [
        "# ChainHound validation run",
        "",
        f"_Dated {today}. Live measurement against the TRM course ground truth "
        "(`tests/Validation/CASES.md`). Measurement, not a gate — verdicts are "
        "graded per tier; no thresholds were tuned to pass._",
        "",
        "**Providers / endpoints used:**",
        f"- BTC: `{eps['btc']}` (Blockstream, keyless)",
        f"- EVM: `{eps['evm']}` (keyless Routescan)",
        f"- 4byte: `{eps['fourbyte']}`",
        "",
        "## Results",
        "",
    ]
    tally: dict[str, int] = {}
    for r in results:
        if r.verdict in _GRADED:
            tally[r.verdict] = tally.get(r.verdict, 0) + 1
        lines.append(f"### {r.case} — {r.verdict}")
        lines.append(f"_{r.summary}_")
        if r.expected:
            lines.append(f"- expected: {r.expected}")
        if r.actual:
            lines.append(f"- actual:   {r.actual}")
        for d in r.detail:
            lines.append(f"- {d}")
        lines.append("")
    lines.append("## Summary (graded verdicts)")
    for v in (PASS, FAIL, COVERAGE_MISS, SKIP, ERROR):
        lines.append(f"- {v}: {tally.get(v, 0)}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    results = run_all()
    report = render_report(results)
    print(report)
    out = Path(__file__).resolve().parents[2] / "docs" / "VALIDATION.md"
    out.write_text(report)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
