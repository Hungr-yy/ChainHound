"""Request models for endpoints that take a body (query params cover the rest)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class CrossChainRequest(BaseModel):
    """Cross-chain link request — mirrors ``chainhound crosschain``.

    ``api`` mode reads a bridge explorer for the src tx. ``inferred`` mode matches
    the src outflow against the dst address's inflows by asset/amount/time, and
    therefore requires the dst fields.
    """

    mode: Literal["api", "inferred"] = "inferred"
    src_chain: str = Field(..., min_length=1)
    src_txid: str = Field(..., min_length=1)

    # inferred-mode fields
    src_asset: Optional[str] = None
    src_amount: Optional[float] = Field(
        default=None, description="human-decimal amount"
    )
    src_time: Optional[int] = Field(
        default=None, description="unix seconds; fetched if omitted"
    )
    dst_chain: Optional[str] = None
    dst_address: Optional[str] = None
