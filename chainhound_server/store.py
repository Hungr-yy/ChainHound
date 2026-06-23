"""Case-persistence store for the investigation workspace.

Backs the platform layer's save/load: cases (``investigation``), pinned notes
(``case_note``), and per-element graph-hygiene state (``graph_element``). This is
a platform concern — no engine analysis touches these tables — so it lives in the
server and builds on the engine's ``chainhound.db.connect`` primitive. ``connect``
is injectable for offline tests, mirroring ``chainhound.labels.store``.
"""

from __future__ import annotations

from typing import Callable, Optional

from chainhound import db


# --- cases (investigation) ----------------------------------------------------
def create_case(
    database_url: str, name: str, *, connect: Callable = db.connect
) -> dict:
    """Create a case; return ``{case_id, name, created_at}``."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO investigation (name) VALUES (%s) "
                "RETURNING case_id, name, created_at",
                (name,),
            )
            row = cur.fetchone()
        conn.commit()
    return {"case_id": row[0], "name": row[1], "created_at": row[2]}


def list_cases(database_url: str, *, connect: Callable = db.connect) -> list[dict]:
    """All cases, newest first."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT case_id, name, created_at FROM investigation "
                "ORDER BY created_at DESC, case_id DESC"
            )
            rows = cur.fetchall()
    return [{"case_id": r[0], "name": r[1], "created_at": r[2]} for r in rows]


def get_case(
    database_url: str, case_id: int, *, connect: Callable = db.connect
) -> Optional[dict]:
    """Load a full case — its row plus pinned notes and graph-hygiene state — or
    None if no such case. This is the workspace 'load'."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT case_id, name, created_at FROM investigation "
                "WHERE case_id = %s",
                (case_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            case = {"case_id": row[0], "name": row[1], "created_at": row[2]}

            cur.execute(
                "SELECT id, chain, ref, body, created_at FROM case_note "
                "WHERE case_id = %s ORDER BY created_at, id",
                (case_id,),
            )
            case["notes"] = [
                {
                    "id": r[0],
                    "chain": r[1],
                    "ref": r[2],
                    "body": r[3],
                    "created_at": r[4],
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                "SELECT element_id, color, hidden, note FROM graph_element "
                "WHERE case_id = %s ORDER BY element_id",
                (case_id,),
            )
            case["elements"] = [
                {"element_id": r[0], "color": r[1], "hidden": r[2], "note": r[3]}
                for r in cur.fetchall()
            ]
    return case


def delete_case(
    database_url: str, case_id: int, *, connect: Callable = db.connect
) -> bool:
    """Delete a case (notes/elements cascade). True if a row was removed."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM investigation WHERE case_id = %s RETURNING case_id",
                (case_id,),
            )
            removed = cur.fetchone() is not None
        conn.commit()
    return removed


# --- notes (case_note) --------------------------------------------------------
def add_note(
    database_url: str,
    case_id: int,
    *,
    chain: Optional[str] = None,
    ref: Optional[str] = None,
    body: Optional[str] = None,
    connect: Callable = db.connect,
) -> Optional[dict]:
    """Pin a note to a case. Returns the stored note, or None if the case is
    unknown (the FK would otherwise reject it)."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM investigation WHERE case_id = %s", (case_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                "INSERT INTO case_note (case_id, chain, ref, body) "
                "VALUES (%s, %s, %s, %s) RETURNING id, chain, ref, body, created_at",
                (case_id, chain, ref, body),
            )
            row = cur.fetchone()
        conn.commit()
    return {
        "id": row[0],
        "chain": row[1],
        "ref": row[2],
        "body": row[3],
        "created_at": row[4],
    }


# --- graph hygiene (graph_element) --------------------------------------------
def save_element(
    database_url: str,
    case_id: int,
    element_id: str,
    *,
    color: Optional[str] = None,
    hidden: bool = False,
    note: Optional[str] = None,
    connect: Callable = db.connect,
) -> Optional[dict]:
    """Upsert per-element hygiene state (color/hidden/note) for a case. Returns
    the stored row, or None if the case is unknown."""
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM investigation WHERE case_id = %s", (case_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                "INSERT INTO graph_element (case_id, element_id, color, hidden, note) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (case_id, element_id) DO UPDATE SET "
                "color = EXCLUDED.color, hidden = EXCLUDED.hidden, note = EXCLUDED.note "
                "RETURNING element_id, color, hidden, note",
                (case_id, element_id, color, hidden, note),
            )
            row = cur.fetchone()
        conn.commit()
    return {"element_id": row[0], "color": row[1], "hidden": row[2], "note": row[3]}
