"""Bitcoin connector backed by the Google BigQuery public dataset
``bigquery-public-data.crypto_bitcoin``.

Use this for bulk/historical analytics and for populating the local store. The
dataset carries nested input/output arrays, so a single row query reconstructs a
full transaction. Requires a GCP project with billing and the
``google-cloud-bigquery`` library; queries are billed by bytes scanned, so
filter on partitions/addresses.
"""
from __future__ import annotations

from typing import Optional

from ..models import AddressSummary, Transaction, TxIO, classify_btc_address
from .base import Provider

try:
    from google.cloud import bigquery
except ImportError:  # pragma: no cover
    bigquery = None

DATASET = "bigquery-public-data.crypto_bitcoin"


class BigQueryBTC(Provider):
    chain = "bitcoin"

    def __init__(self, project: Optional[str] = None) -> None:
        if bigquery is None:
            raise RuntimeError(
                "install 'google-cloud-bigquery' and set GOOGLE_APPLICATION_CREDENTIALS"
            )
        self.client = bigquery.Client(project=project)

    def _query(self, sql: str, params: list) -> list[dict]:
        job = self.client.query(
            sql,
            job_config=bigquery.QueryJobConfig(query_parameters=params),
        )
        return [dict(row) for row in job.result()]

    def get_transaction(self, txid: str) -> Optional[Transaction]:
        sql = f"""
        SELECT `hash` AS txid, block_timestamp, fee, is_coinbase, inputs, outputs
        FROM `{DATASET}.transactions`
        WHERE `hash` = @txid
        LIMIT 1
        """
        rows = self._query(sql, [bigquery.ScalarQueryParameter("txid", "STRING", txid)])
        if not rows:
            return None
        r = rows[0]
        inputs = [
            TxIO(address=(i["addresses"][0] if i.get("addresses") else None),
                 value=int(i.get("value") or 0),
                 address_type=classify_btc_address(i["addresses"][0]) if i.get("addresses") else classify_btc_address(""))
            for i in (r.get("inputs") or [])
        ]
        outputs = [
            TxIO(address=(o["addresses"][0] if o.get("addresses") else None),
                 value=int(o.get("value") or 0),
                 address_type=classify_btc_address(o["addresses"][0]) if o.get("addresses") else classify_btc_address(""))
            for o in (r.get("outputs") or [])
        ]
        return Transaction(
            txid=r["txid"],
            chain=self.chain,
            timestamp=int(r["block_timestamp"].timestamp()) if r.get("block_timestamp") else 0,
            inputs=inputs,
            outputs=outputs,
            fee=int(r.get("fee") or 0),
            is_coinbase=bool(r.get("is_coinbase")),
        )

    def get_address_summary(self, address: str) -> Optional[AddressSummary]:
        sql = f"""
        WITH io AS (
          SELECT o.value AS value, t.block_timestamp AS ts, 'recv' AS dir
          FROM `{DATASET}.transactions` t, UNNEST(outputs) o
          WHERE @addr IN UNNEST(o.addresses)
          UNION ALL
          SELECT i.value AS value, t.block_timestamp AS ts, 'sent' AS dir
          FROM `{DATASET}.transactions` t, UNNEST(inputs) i
          WHERE @addr IN UNNEST(i.addresses)
        )
        SELECT
          SUM(IF(dir='recv', value, 0)) AS received,
          SUM(IF(dir='sent', value, 0)) AS sent,
          COUNTIF(dir='recv') AS recv_count,
          COUNTIF(dir='sent') AS sent_count,
          MIN(ts) AS first_seen, MAX(ts) AS last_seen
        FROM io
        """
        rows = self._query(sql, [bigquery.ScalarQueryParameter("addr", "STRING", address)])
        if not rows:
            return None
        r = rows[0]
        received = int(r.get("received") or 0)
        sent = int(r.get("sent") or 0)
        return AddressSummary(
            address=address,
            chain=self.chain,
            address_type=classify_btc_address(address),
            balance=received - sent,
            total_received=received,
            total_sent=sent,
            tx_count=int((r.get("recv_count") or 0) + (r.get("sent_count") or 0)),
            received_count=int(r.get("recv_count") or 0),
            sent_count=int(r.get("sent_count") or 0),
            first_seen=int(r["first_seen"].timestamp()) if r.get("first_seen") else None,
            last_seen=int(r["last_seen"].timestamp()) if r.get("last_seen") else None,
        )

    def get_address_transactions(self, address: str, limit: int = 50) -> list[Transaction]:
        sql = f"""
        SELECT DISTINCT `hash` AS txid
        FROM `{DATASET}.transactions` t
        WHERE @addr IN (
          SELECT addr FROM UNNEST(outputs) o, UNNEST(o.addresses) addr
          UNION ALL
          SELECT addr FROM UNNEST(inputs) i, UNNEST(i.addresses) addr
        )
        ORDER BY txid
        LIMIT @lim
        """
        rows = self._query(sql, [
            bigquery.ScalarQueryParameter("addr", "STRING", address),
            bigquery.ScalarQueryParameter("lim", "INT64", limit),
        ])
        return [self.get_transaction(r["txid"]) for r in rows]

    def get_spending_tx(self, txid: str, vout: int) -> Optional[tuple[str, int]]:
        sql = f"""
        SELECT t.`hash` AS spend_txid, i.index AS vin
        FROM `{DATASET}.transactions` t, UNNEST(inputs) i
        WHERE i.spent_transaction_hash = @txid AND i.spent_output_index = @vout
        LIMIT 1
        """
        rows = self._query(sql, [
            bigquery.ScalarQueryParameter("txid", "STRING", txid),
            bigquery.ScalarQueryParameter("vout", "INT64", vout),
        ])
        if rows:
            return rows[0]["spend_txid"], int(rows[0].get("vin") or 0)
        return None
