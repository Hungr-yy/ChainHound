-- ChainHound canonical schema (Postgres).
-- One transfer-centric model unifies UTXO and account chains. Tables beyond
-- Phase 1 (clusters, labels, bridge links, cases) are defined now so the
-- correlation engine has a stable backbone to grow into.

CREATE TABLE IF NOT EXISTS address (
    chain          TEXT    NOT NULL,
    address        TEXT    NOT NULL,
    address_type   TEXT,
    is_multisig    BOOLEAN DEFAULT FALSE,
    multisig_type  TEXT,
    first_seen     TIMESTAMPTZ,
    last_seen      TIMESTAMPTZ,
    n_tx_seen      INTEGER DEFAULT 0,
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS transaction (
    chain      TEXT NOT NULL,
    txid       TEXT NOT NULL,
    block_time TIMESTAMPTZ,
    fee        BIGINT DEFAULT 0,
    is_coinbase BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (chain, txid)
);

-- The atomic unit. A BTC tx fans out to many rows; an EVM/token transfer is one.
CREATE TABLE IF NOT EXISTS transfer (
    id            BIGSERIAL PRIMARY KEY,
    chain         TEXT NOT NULL,
    txid          TEXT NOT NULL,
    from_address  TEXT,
    to_address    TEXT,
    asset         TEXT NOT NULL DEFAULT 'BTC',
    amount        NUMERIC(40,0) NOT NULL,     -- smallest unit
    usd_at_time   NUMERIC(20,2),
    vout          INTEGER,                    -- output index (UTXO)
    block_time    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_transfer_from ON transfer (chain, from_address);
CREATE INDEX IF NOT EXISTS idx_transfer_to   ON transfer (chain, to_address);
CREATE INDEX IF NOT EXISTS idx_transfer_txid ON transfer (chain, txid);

-- Co-spend / heuristic clusters. Confidence is a band, never a boolean.
CREATE TABLE IF NOT EXISTS cluster (
    cluster_id  BIGSERIAL PRIMARY KEY,
    chain       TEXT NOT NULL,
    heuristic   TEXT NOT NULL,               -- 'co_spend', 'deposit', ...
    confidence  TEXT NOT NULL DEFAULT 'High',
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cluster_member (
    cluster_id  BIGINT REFERENCES cluster(cluster_id) ON DELETE CASCADE,
    chain       TEXT NOT NULL,
    address     TEXT NOT NULL,
    PRIMARY KEY (cluster_id, chain, address)
);

-- Change-analysis verdicts, with the contributing heuristics for glass-box review.
CREATE TABLE IF NOT EXISTS change_verdict (
    chain         TEXT NOT NULL,
    txid          TEXT NOT NULL,
    change_vout   INTEGER,
    score         REAL,
    confidence    TEXT,
    signals       JSONB,                      -- list of {heuristic, weight, rationale}
    PRIMARY KEY (chain, txid)
);

-- Attribution tags from any source, with provenance + confidence (TagPack style).
CREATE TABLE IF NOT EXISTS label (
    id          BIGSERIAL PRIMARY KEY,
    chain       TEXT NOT NULL,
    address     TEXT,                          -- or a cluster via cluster_id
    cluster_id  BIGINT REFERENCES cluster(cluster_id) ON DELETE SET NULL,
    name        TEXT NOT NULL,                 -- 'Binance hot wallet', 'OFAC SDN', ...
    category    TEXT,                          -- 'exchange','mixer','sanctioned',...
    source      TEXT NOT NULL,                 -- 'ofac','chainabuse','etherscan',...
    confidence  TEXT DEFAULT 'High',
    added_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_label_addr ON label (chain, address);
-- Natural key for idempotent upserts of address-scoped labels (Phase 2a
-- ingestion). Partial: cluster-only labels (address IS NULL) are exempt.
CREATE UNIQUE INDEX IF NOT EXISTS uq_label_addr_source_name
    ON label (chain, address, source, name) WHERE address IS NOT NULL;
-- Refreshed by re-sync so a label's last-seen-from-source is auditable.
ALTER TABLE label ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- On-demand fetch cache (Chainabuse etc.): repeat lookups for case addresses
-- are served from here so the rate-limited APIs are never re-hit needlessly.
CREATE TABLE IF NOT EXISTS fetch_cache (
    source      TEXT NOT NULL,
    request_key TEXT NOT NULL,              -- e.g. the queried address
    body        JSONB,                      -- parsed response payload
    status      INTEGER,                    -- last HTTP status
    fetched_at  TIMESTAMPTZ DEFAULT now(),
    expires_at  TIMESTAMPTZ,                -- NULL = never expires
    PRIMARY KEY (source, request_key)
);

-- Cross-chain links: deterministic (bridge API) or inferred (value/time match).
CREATE TABLE IF NOT EXISTS cross_chain_link (
    id             BIGSERIAL PRIMARY KEY,
    src_chain      TEXT NOT NULL,
    src_txid       TEXT NOT NULL,
    dst_chain      TEXT NOT NULL,
    dst_txid       TEXT NOT NULL,
    bridge         TEXT,
    method         TEXT NOT NULL,             -- 'api' | 'inferred'
    confidence     TEXT NOT NULL,
    matched_on     JSONB,                      -- {asset, amount, time_delta_s}
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- Investigation workspace: cases, notes, and graph hygiene state.
CREATE TABLE IF NOT EXISTS investigation (
    case_id     BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS case_note (
    id          BIGSERIAL PRIMARY KEY,
    case_id     BIGINT REFERENCES investigation(case_id) ON DELETE CASCADE,
    chain       TEXT,
    ref         TEXT,                          -- address or txid the note pins to
    body        TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS graph_element (
    id          BIGSERIAL PRIMARY KEY,
    case_id     BIGINT REFERENCES investigation(case_id) ON DELETE CASCADE,
    element_id  TEXT NOT NULL,                 -- node/edge id
    color       TEXT,
    hidden      BOOLEAN DEFAULT FALSE,
    note        TEXT
);

-- Monitoring: addresses under watch and fired detector alerts (Phase 5).
CREATE TABLE IF NOT EXISTS watch (
    id          BIGSERIAL PRIMARY KEY,
    chain       TEXT NOT NULL,
    address     TEXT NOT NULL,
    case_id     BIGINT REFERENCES investigation(case_id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert (
    id          BIGSERIAL PRIMARY KEY,
    watch_id    BIGINT REFERENCES watch(id) ON DELETE CASCADE,
    detector    TEXT NOT NULL,
    detail      JSONB,
    fired_at    TIMESTAMPTZ DEFAULT now()
);
