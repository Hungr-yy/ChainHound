"""On-demand fetcher: rate limiting, backoff, and cache-first lookup. Offline."""
import pytest

from chainhound.labels.base import Label
from chainhound.labels.ondemand import (
    OnDemandSource,
    RateLimited,
    TokenBucket,
    fetch_with_backoff,
)


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_token_bucket_spaces_out_calls():
    clk = _FakeClock()
    bucket = TokenBucket(capacity=1, refill_per_sec=1.0, clock=clk.time, sleeper=clk.sleep)
    bucket.acquire()        # first token is free
    assert clk.t == 0.0
    bucket.acquire()        # must wait ~1s to refill the second
    assert clk.t == pytest.approx(1.0)


def test_backoff_retries_then_succeeds():
    sleeps = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimited("429")
        return "ok"

    out = fetch_with_backoff(flaky, retries=3, base_delay=1.0, sleeper=sleeps.append)
    assert out == "ok"
    assert sleeps == [1.0, 2.0]   # exponential


def test_backoff_gives_up_after_retries():
    def always():
        raise RateLimited("429")

    with pytest.raises(RateLimited):
        fetch_with_backoff(always, retries=2, base_delay=1.0, sleeper=lambda s: None)


# --- OnDemandSource cache behaviour, against a fake connection ----------------

class _FakeCursor:
    def __init__(self, fetchone_val):
        self.calls = []
        self._fetchone = fetchone_val

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def executemany(self, sql, seq):
        self.calls.append((sql, list(seq)))

    def fetchone(self):
        return self._fetchone


class _FakeConn:
    def __init__(self, fetchone_val):
        self.cursor_obj = _FakeCursor(fetchone_val)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass


class _Src(OnDemandSource):
    source = "test"

    def __init__(self, raw, **kw):
        super().__init__(**kw)
        self._raw = raw
        self.fetched = 0

    def _fetch(self, chain, address):
        self.fetched += 1
        return self._raw

    def parse(self, raw, chain, address):
        return [Label(chain, address, f"name:{raw}", "test", self.source, "Moderate")]


def _fast_bucket():
    return TokenBucket(capacity=99, refill_per_sec=1e9)


def test_cache_hit_short_circuits_fetch():
    conn = _FakeConn(fetchone_val=("cached-raw",))   # cache returns a row
    src = _Src("fresh", bucket=_fast_bucket(), sleeper=lambda s: None)
    labels = src.check("pg://x", "bitcoin", "1AAA", connect=lambda _u: conn)
    assert src.fetched == 0                  # never hit the network
    assert labels[0].name == "name:cached-raw"


def test_cache_miss_fetches_and_persists():
    conn = _FakeConn(fetchone_val=None)      # cache miss
    src = _Src("fresh", bucket=_fast_bucket(), sleeper=lambda s: None)
    labels = src.check("pg://x", "ethereum", "0xBBB", connect=lambda _u: conn)
    assert src.fetched == 1
    assert labels[0].name == "name:fresh"
    sql_blob = " ".join(c[0] for c in conn.cursor_obj.calls)
    assert "label_cache" in sql_blob         # wrote the cache
    assert "INSERT INTO label" in sql_blob    # persisted the labels
