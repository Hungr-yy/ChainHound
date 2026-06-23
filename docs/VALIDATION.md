# ChainHound validation run

_Dated 2026-06-23. Live measurement against the TRM course ground truth (`tests/Validation/CASES.md`). Measurement, not a gate — verdicts are graded per tier; no thresholds were tuned to pass._

**Providers / endpoints used:**
- BTC: `https://blockstream.info/api` (Blockstream, keyless)
- EVM: `https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api` (keyless Routescan)
- 4byte: `https://api.4byte.sourcify.dev/signature-database/v1/lookup`

## Results

### A1 — PASS
_triage / activity picture_
- expected: recv==sent==520033909, tx_count==141, p2sh, likely_service
- actual:   recv=520033909 sent=520033909 tx_count=141 type=AddressType.P2SH likely_service=True

### A2 — PASS
_change identification (the core heuristic)_
- expected: predicted change starts bc1q32 & ends nl80, band >= High
- actual:   predicted=bc1q32ugkkd6kgf0d249je0uddzxyferyx20wgnl80 band=High score=0.730
- pinned txid=9686e2ac304fe052e65de241885751baf5d8e4a0680b17bb80b0ae10405e1906
- signals: address_type@0.55->out1; round_payment@0.4->out1

### A3 — FAIL
_peel-chain detection_
- expected: peel reaches bc1qr5kgpg5ddn8tac254s3f0xjtj4749xayq6ua3y with 53.5 BTC +/- 1
- actual:   is_peel_chain=True hops=5 terminal=bc1qta5646q9ystlvhl8quqfe9m7wwdz6j5ed2k34w (53.50 BTC) target_hit=no
- start txid=9686e2ac304fe052e65de241885751baf5d8e4a0680b17bb80b0ae10405e1906

### A4 — PASS
_co-spend abstains (must NOT merge the peel)_
- expected: peel hops left in separate co-spend clusters (abstention)
- actual:   peel_addrs=5 clusters=5 biggest_cluster=1 terminal_in_origin_cluster=False all_peel_merged=False
- origin cluster size=1

### B1 — COVERAGE-MISS
_exchange exposure from the hack origin_
- expected: course: 'over a third of the stolen BTC went to an exchange'
- actual:   unconfirmable — exchange deposit address not in CASES.md and not in the label corpus
- Tier B is coverage-dependent; this records what the corpus would need (the exchange's labelled deposit address) to validate the claim.
- Engine arithmetic is checked separately by the B1-math DIAGNOSTIC below (seeded proxy — explicitly NOT a course verdict).

### B1-math — DIAGNOSTIC
_exposure ring-math sanity check (NOT a course verdict)_
- expected: ring.direct_value == summed outbound edges to the seeded counterparty
- actual:   seeded bc1q32ugkkd6kgf0d249je0uddzxyferyx20wgnl80 = 96% of 1-hop outflow; ring.direct_value=8000457900; math=OK
- Seeds an arbitrary label only to check compute_exposure's arithmetic; this does NOT confirm the course's exchange-exposure finding.

### C1 — SKIP
_cross-chain matching_
- actual:   Phase 4 (cross-chain) not built
- BTC->ETH RenBTC bridge: bc1qr5kg... -> 0xd3f04ce2d37b182432e2f804f9913a02071cea54

### C2 — SKIP
_cross-chain matching_
- actual:   Phase 4 (cross-chain) not built
- ETH RENBTC -> WBTC/DAI/USDD hops + BitTorrent ETH->TRON bridge

### C3 — SKIP
_cross-chain matching_
- actual:   Phase 4 (cross-chain) not built
- TRON USDD landing: TMhCFSbdwX8cTC5bg4Q3iAKchH7YWpj9nz

### D — SKIP
_TRM-proprietary / ML signatures + private attribution_
- actual:   excluded from heuristic grading by design (CASES.md Tier D)

### E1 — PASS
_EVM triage / get_transaction smoke_
- expected: coherent summary (EOA/contract, non-zero activity) + tx resolves with from/to/value
- actual:   type=AddressType.EOA tx_count=18 | tx from=0x65172d46bd1543a51d904d1bde6c9b037b55ac59 to=0xdac17f958d2ee523a2206206994597c13d831ec7 value=0

### E2 — PASS
_contract-function decoding (4byte)_
- expected: decode_input returns a non-None signature for the calldata
- actual:   selector=0xa9059cbb -> 'transfer(address,uint256)'

### E3 — PASS
_EVM label-normalization regression_
- expected: checksummed ETH lowercases; BTC base58 unchanged
- actual:   eth->0xabc0000000000000000000000000000000000123 ; btc_unchanged=True
- DB round-trip covered by tests/test_labels_integration.py::test_e3_evm_checksummed_label_matches_lowercased_lookup

## Summary (graded verdicts)
- PASS: 6
- FAIL: 1
- COVERAGE-MISS: 1
- SKIP: 4
- ERROR: 0
