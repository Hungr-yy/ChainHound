"""Opt-in live validation. Skipped (no network) unless CHAINHOUND_VALIDATION=1.

This is a measurement, not a gate: it asserts only that every case produced a
verdict and that nothing errored at the harness/network level — never that the
heuristic cases PASS. Divergences are the deliverable; read docs/VALIDATION.md.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("CHAINHOUND_VALIDATION"),
    reason="set CHAINHOUND_VALIDATION=1 to run the live validation harness",
)


def test_validation_harness_runs_and_grades_every_case():
    from tests.Validation.harness import ERROR, run_all, render_report

    results = run_all()
    print("\n" + render_report(results))
    assert results, "harness produced no results"
    assert all(r.verdict for r in results), "every case must produce a verdict"
    errored = [r.case for r in results if r.verdict == ERROR]
    assert not errored, f"harness/network errors (not heuristic results): {errored}"
