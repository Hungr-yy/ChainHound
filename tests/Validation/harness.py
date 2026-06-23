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
    results = [_safe("A1", lambda: case_a1(btc))]
    return results


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
