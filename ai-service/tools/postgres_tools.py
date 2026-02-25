"""LangChain tools for PostgreSQL application state operations.

Tools manage workspace-scoped application state:
    - Data product CRUD
    - Business requirements persistence
    - Semantic view metadata storage
    - Audit log entries
"""

import contextvars
import json
import logging
import re
from typing import Any
from uuid import uuid4

from langchain.tools import tool

from services import postgres as pg_service

logger = logging.getLogger(__name__)

# Context variable for the real data_product_id (set from agent.py before tool execution).
# LLMs sometimes truncate UUIDs — this override ensures tools always use the correct one.
_data_product_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_data_product_id_ctx", default=None
)


def set_data_product_context(data_product_id: str | None) -> None:
    """Set the data_product_id context for the current task."""
    _data_product_id_ctx.set(data_product_id)


# Context variable for the data product NAME (used by naming.py for schema derivation).
_data_product_name_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_data_product_name_ctx", default=None
)


def set_data_product_name_context(name: str | None) -> None:
    """Set the data product name context for the current task."""
    _data_product_name_ctx.set(name)


def get_data_product_name() -> str | None:
    """Return the data product name from the current context."""
    return _data_product_name_ctx.get()


def _resolve_dp_id(llm_provided: str) -> str:
    """Return the contextvar data_product_id if available, else the LLM-provided one."""
    ctx_id = _data_product_id_ctx.get()
    if ctx_id:
        if ctx_id != llm_provided:
            logger.warning(
                "data_product_id mismatch — LLM sent %r, using context %r",
                llm_provided,
                ctx_id,
            )
        return ctx_id
    return llm_provided


async def _get_pool() -> Any:
    """Return the global PostgreSQL pool, raising if not initialized."""
    if pg_service._pool is None:
        raise RuntimeError("PostgreSQL pool not initialized. Start the application first.")
    return pg_service._pool


def _sanitize_document_query_text(value: str) -> str:
    """Normalize free-text search input used for document retrieval tools."""
    text = re.sub(r"\s+", " ", (value or "").strip())
    return text[:400]


def _phase_to_document_step(phase: str | None) -> str:
    """Map workflow phase labels to document-context step labels."""
    normalized = (phase or "").strip().lower()
    if normalized in {"prepare", "transformation", "idle", ""}:
        return "discovery"
    if normalized == "explorer":
        return "publishing"
    if normalized in {
        "discovery",
        "requirements",
        "modeling",
        "generation",
        "validation",
        "publishing",
    }:
        return normalized
    return "discovery"


async def _table_exists(pool: Any, table_name: str) -> bool:
    """Return True when a PostgreSQL table exists in public schema."""
    try:
        rows = await pg_service.query(
            pool,
            "SELECT to_regclass($1) AS rel",
            f"public.{table_name}",
        )
        if not rows:
            return False
        return bool(rows[0].get("rel"))
    except Exception:
        return False


async def _resolve_active_document_scope(
    pool: Any,
    data_product_id: str,
) -> tuple[list[str] | None, str]:
    """Resolve active document scope for current step; fallback to all docs."""
    step = "discovery"
    try:
        phase_rows = await pg_service.query(
            pool,
            "SELECT state->>'current_phase' AS current_phase FROM data_products WHERE id = $1::uuid",
            data_product_id,
        )
        current_phase = phase_rows[0].get("current_phase") if phase_rows else None
        step = _phase_to_document_step(str(current_phase or ""))
    except Exception:
        step = "discovery"

    if not await _table_exists(pool, "context_step_selections"):
        return None, step

    try:
        rows = await pg_service.query(
            pool,
            """SELECT DISTINCT cs.document_id
               FROM context_step_selections cs
               JOIN uploaded_documents ud ON ud.id = cs.document_id
               WHERE cs.data_product_id = $1::uuid
                 AND cs.step_name = $2
                 AND cs.state = 'active'
                 AND COALESCE(ud.is_deleted, false) = false""",
            data_product_id,
            step,
        )
        doc_ids = [str(row.get("document_id")) for row in rows if row.get("document_id")]
        return (doc_ids or None), step
    except Exception:
        return None, step


def _as_json_number(value: Any) -> float | None:
    """Convert DB numeric-like values to JSON-safe float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_exact_value_question(question: str) -> bool:
    """Heuristic to identify exact-value asks that require deterministic facts."""
    text = (question or "").lower()
    if not text:
        return False
    exact_markers = (
        "exact",
        "exactly",
        "precise",
        "specific",
        "invoice",
        "amount",
        "total",
        "price",
        "cost",
        "how much",
        "value",
        "number",
        "quantity",
        "qty",
    )
    return any(marker in text for marker in exact_markers)


def _serialize_event_time(value: Any) -> str | None:
    """Convert datetime-like values to string for JSON payloads."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _build_document_recovery_actions(step: str) -> list[dict[str, Any]]:
    """Standard recovery actions for missing/weak document evidence."""
    return [
        {
            "action": "activate_document_evidence",
            "description": "Activate relevant documents for this mission step and retry.",
            "metadata": {"step": step},
        },
        {
            "action": "upload_additional_documents",
            "description": "Upload a source document that contains the required business values.",
            "metadata": {"step": step},
        },
    ]


def _coerce_limit(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    """Safely coerce tool-provided limits into bounded integers."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


@tool
async def query_document_facts(
    data_product_id: str,
    question: str,
    limit: int = 12,
) -> str:
    """Retrieve deterministic document facts for exact or transaction-style questions.

    The tool searches `doc_facts` (scoped to active mission-step documents when
    available) and returns structured rows + citation payloads. It includes an
    `answer_contract_hint` so the router can apply evidence-first trust signals.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()

    query_text = _sanitize_document_query_text(question)
    if not query_text:
        return json.dumps(
            {
                "status": "error",
                "tool": "query_document_facts",
                "error": "question cannot be empty",
                "error_type": "validation",
                "retryable": False,
            }
        )

    if not await _table_exists(pool, "doc_facts"):
        return json.dumps(
            {
                "status": "error",
                "tool": "query_document_facts",
                "error": "Document semantic facts are unavailable. Apply hybrid document migrations first.",
                "error_type": "missing_schema",
                "retryable": True,
            }
        )

    active_doc_ids, step = await _resolve_active_document_scope(pool, data_product_id)
    like_pattern = f"%{query_text[:180]}%"
    effective_limit = _coerce_limit(limit, default=12, minimum=1, maximum=50)
    has_search_view = await _table_exists(pool, "v_doc_search_facts")

    rows: list[Any] = []
    try:
        if has_search_view:
            rows = await pg_service.query(
                pool,
                """SELECT
                       f.fact_id AS id,
                       f.document_id,
                       f.filename,
                       f.fact_type,
                       f.subject_key,
                       f.predicate,
                       f.object_value,
                       f.object_unit,
                       f.numeric_value,
                       f.currency,
                       f.event_time,
                       f.source_page,
                       f.confidence,
                       f.metadata
                   FROM v_doc_search_facts f
                   WHERE f.data_product_id = $1::uuid
                     AND ($2::uuid[] IS NULL OR f.document_id = ANY($2::uuid[]))
                     AND (
                         f.search_vector @@ websearch_to_tsquery('english', $3)
                         OR COALESCE(f.subject_key, '') ILIKE $4
                         OR COALESCE(f.predicate, '') ILIKE $4
                         OR COALESCE(f.object_value, '') ILIKE $4
                         OR COALESCE(f.filename, '') ILIKE $4
                     )
                   ORDER BY
                       CASE WHEN f.numeric_value IS NOT NULL THEN 0 ELSE 1 END,
                       COALESCE(f.confidence, 0) DESC,
                       f.created_at DESC
                   LIMIT $5""",
                data_product_id,
                active_doc_ids,
                query_text,
                like_pattern,
                effective_limit,
            )
        else:
            rows = await pg_service.query(
                pool,
                """SELECT
                       f.id,
                       f.document_id,
                       ud.filename,
                       f.fact_type,
                       f.subject_key,
                       f.predicate,
                       f.object_value,
                       f.object_unit,
                       f.numeric_value,
                       f.currency,
                       f.event_time,
                       f.source_page,
                       f.confidence,
                       f.metadata
                   FROM doc_facts f
                   JOIN uploaded_documents ud
                     ON ud.id = f.document_id
                   WHERE f.data_product_id = $1::uuid
                     AND COALESCE(ud.is_deleted, false) = false
                     AND ($2::uuid[] IS NULL OR f.document_id = ANY($2::uuid[]))
                     AND (
                         to_tsvector('english',
                             COALESCE(f.subject_key, '') || ' ' ||
                             COALESCE(f.predicate, '') || ' ' ||
                             COALESCE(f.object_value, '')
                         ) @@ websearch_to_tsquery('english', $3)
                         OR COALESCE(f.subject_key, '') ILIKE $4
                         OR COALESCE(f.predicate, '') ILIKE $4
                         OR COALESCE(f.object_value, '') ILIKE $4
                         OR COALESCE(ud.filename, '') ILIKE $4
                     )
                   ORDER BY
                       CASE WHEN f.numeric_value IS NOT NULL THEN 0 ELSE 1 END,
                       COALESCE(f.confidence, 0) DESC,
                       f.created_at DESC
                   LIMIT $5""",
                data_product_id,
                active_doc_ids,
                query_text,
                like_pattern,
                effective_limit,
            )
    except Exception:
        # Fallback path when full-text search is unavailable.
        rows = await pg_service.query(
            pool,
            """SELECT
                   f.id,
                   f.document_id,
                   ud.filename,
                   f.fact_type,
                   f.subject_key,
                   f.predicate,
                   f.object_value,
                   f.object_unit,
                   f.numeric_value,
                   f.currency,
                   f.event_time,
                   f.source_page,
                   f.confidence,
                   f.metadata
               FROM doc_facts f
               JOIN uploaded_documents ud
                 ON ud.id = f.document_id
               WHERE f.data_product_id = $1::uuid
                 AND COALESCE(ud.is_deleted, false) = false
                 AND ($2::uuid[] IS NULL OR f.document_id = ANY($2::uuid[]))
                 AND (
                     COALESCE(f.subject_key, '') ILIKE $3
                     OR COALESCE(f.predicate, '') ILIKE $3
                     OR COALESCE(f.object_value, '') ILIKE $3
                     OR COALESCE(ud.filename, '') ILIKE $3
                 )
               ORDER BY
                   CASE WHEN f.numeric_value IS NOT NULL THEN 0 ELSE 1 END,
                   COALESCE(f.confidence, 0) DESC,
                   f.created_at DESC
               LIMIT $4""",
            data_product_id,
            active_doc_ids,
            like_pattern,
            effective_limit,
        )

    facts: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []

    for row in rows:
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        fact_id = str(row.get("id"))
        document_id = str(row.get("document_id"))
        filename = str(row.get("filename") or "Document")
        source_page = row.get("source_page")
        confidence = _as_json_number(row.get("confidence"))
        numeric_value = _as_json_number(row.get("numeric_value"))

        facts.append(
            {
                "fact_id": fact_id,
                "document_id": document_id,
                "filename": filename,
                "fact_type": row.get("fact_type"),
                "subject_key": row.get("subject_key"),
                "predicate": row.get("predicate"),
                "object_value": row.get("object_value"),
                "object_unit": row.get("object_unit"),
                "numeric_value": numeric_value,
                "currency": row.get("currency"),
                "event_time": _serialize_event_time(row.get("event_time")),
                "source_page": source_page,
                "confidence": confidence,
                "metadata": metadata,
            }
        )

        label = filename if source_page is None else f"{filename} (page {source_page})"
        citations.append(
            {
                "citation_type": "document_fact",
                "reference_id": fact_id,
                "label": label,
                "page": int(source_page) if isinstance(source_page, int) else None,
                "score": confidence,
                "metadata": {
                    "document_id": document_id,
                    "fact_type": row.get("fact_type"),
                    "subject_key": row.get("subject_key"),
                    "predicate": row.get("predicate"),
                },
            }
        )

    exact_intent = _is_exact_value_question(query_text)
    has_numeric_fact = any(item.get("numeric_value") is not None for item in facts)
    numeric_values = sorted(
        {float(item["numeric_value"]) for item in facts if item.get("numeric_value") is not None}
    )
    conflicting_numeric_values = len(numeric_values) > 1
    conflict_notes: list[str] = []

    if exact_intent and conflicting_numeric_values:
        sample = ", ".join(str(v) for v in numeric_values[:4])
        suffix = " ..." if len(numeric_values) > 4 else ""
        conflict_notes.append(
            f"Conflicting exact values were found in source facts ({sample}{suffix})."
        )

    if exact_intent and conflicting_numeric_values:
        confidence_decision = "abstain"
        exactness_state = "insufficient_evidence"
        trust_state = "abstained_conflicting_evidence"
        evidence_summary = "Conflicting deterministic values were found. ekaiX abstained to avoid returning an incorrect exact answer."
    elif not facts:
        confidence_decision = "abstain"
        exactness_state = "insufficient_evidence" if exact_intent else "not_applicable"
        trust_state = "abstained_missing_evidence"
        evidence_summary = "No matching document facts were found for this question."
    elif exact_intent and not has_numeric_fact:
        confidence_decision = "abstain"
        exactness_state = "insufficient_evidence"
        trust_state = "abstained_missing_evidence"
        evidence_summary = (
            "Relevant document context exists but no deterministic numeric fact was found."
        )
    elif exact_intent and has_numeric_fact:
        confidence_decision = "high"
        exactness_state = "validated_exact"
        trust_state = "answer_ready"
        evidence_summary = "Deterministic document facts were found for this exact-value question."
    else:
        confidence_decision = "medium"
        exactness_state = "not_applicable"
        trust_state = "answer_ready"
        evidence_summary = "Relevant document facts were found."

    answer_contract_hint = {
        "source_mode": "document",
        "exactness_state": exactness_state,
        "confidence_decision": confidence_decision,
        "trust_state": trust_state,
        "evidence_summary": evidence_summary,
        "conflict_notes": conflict_notes,
        "citations": citations,
        "recovery_actions": (
            _build_document_recovery_actions(step) if confidence_decision == "abstain" else []
        ),
        "metadata": {
            "tool": "query_document_facts",
            "question": query_text,
            "step_scope": step,
            "document_scope": "active" if active_doc_ids else "all",
            "result_count": len(facts),
            "exact_intent": exact_intent,
            "conflicting_numeric_values": conflicting_numeric_values,
            "numeric_values": numeric_values[:8],
            "exact_value": numeric_values[0] if len(numeric_values) == 1 else None,
        },
    }

    return json.dumps(
        {
            "status": "success",
            "tool": "query_document_facts",
            "question": query_text,
            "step_scope": step,
            "document_scope": "active" if active_doc_ids else "all",
            "document_ids": active_doc_ids or [],
            "match_count": len(facts),
            "facts": facts,
            "citations": citations,
            "conflict_notes": conflict_notes,
            "answer_contract_hint": answer_contract_hint,
        }
    )


@tool
async def search_document_chunks(
    data_product_id: str,
    query_text: str,
    limit: int = 8,
) -> str:
    """Retrieve relevant document chunks for policy/context style questions.

    This tool searches `doc_chunks` and returns short snippets plus citation
    metadata for evidence-aware synthesis.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()

    cleaned_query = _sanitize_document_query_text(query_text)
    if not cleaned_query:
        return json.dumps(
            {
                "status": "error",
                "tool": "search_document_chunks",
                "error": "query_text cannot be empty",
                "error_type": "validation",
                "retryable": False,
            }
        )

    if not await _table_exists(pool, "doc_chunks"):
        return json.dumps(
            {
                "status": "error",
                "tool": "search_document_chunks",
                "error": "Document chunk index is unavailable. Apply hybrid document migrations first.",
                "error_type": "missing_schema",
                "retryable": True,
            }
        )

    active_doc_ids, step = await _resolve_active_document_scope(pool, data_product_id)
    like_pattern = f"%{cleaned_query[:180]}%"
    effective_limit = _coerce_limit(limit, default=8, minimum=1, maximum=20)
    has_search_view = await _table_exists(pool, "v_doc_search_chunks")

    rows: list[Any] = []
    try:
        if has_search_view:
            rows = await pg_service.query(
                pool,
                """SELECT
                       c.chunk_id AS id,
                       c.document_id,
                       c.filename,
                       c.page_no,
                       c.chunk_seq,
                       c.section_path,
                       c.chunk_text,
                       c.extraction_confidence,
                       ts_rank(c.search_vector, websearch_to_tsquery('english', $3)) AS rank
                   FROM v_doc_search_chunks c
                   WHERE c.data_product_id = $1::uuid
                     AND ($2::uuid[] IS NULL OR c.document_id = ANY($2::uuid[]))
                     AND (
                         c.search_vector @@ websearch_to_tsquery('english', $3)
                         OR COALESCE(c.chunk_text, '') ILIKE $4
                         OR COALESCE(c.filename, '') ILIKE $4
                     )
                   ORDER BY rank DESC NULLS LAST, COALESCE(c.extraction_confidence, 0) DESC, c.chunk_seq ASC
                   LIMIT $5""",
                data_product_id,
                active_doc_ids,
                cleaned_query,
                like_pattern,
                effective_limit,
            )
        else:
            rows = await pg_service.query(
                pool,
                """SELECT
                       c.id,
                       c.document_id,
                       ud.filename,
                       c.page_no,
                       c.chunk_seq,
                       c.section_path,
                       c.chunk_text,
                       c.extraction_confidence,
                       ts_rank(
                         to_tsvector('english', COALESCE(c.chunk_text, '')),
                         websearch_to_tsquery('english', $3)
                       ) AS rank
                   FROM doc_chunks c
                   JOIN uploaded_documents ud
                     ON ud.id = c.document_id
                   WHERE c.data_product_id = $1::uuid
                     AND COALESCE(ud.is_deleted, false) = false
                     AND ($2::uuid[] IS NULL OR c.document_id = ANY($2::uuid[]))
                     AND (
                         to_tsvector('english', COALESCE(c.chunk_text, '')) @@ websearch_to_tsquery('english', $3)
                         OR COALESCE(c.chunk_text, '') ILIKE $4
                         OR COALESCE(ud.filename, '') ILIKE $4
                     )
                   ORDER BY rank DESC NULLS LAST, COALESCE(c.extraction_confidence, 0) DESC, c.chunk_seq ASC
                   LIMIT $5""",
                data_product_id,
                active_doc_ids,
                cleaned_query,
                like_pattern,
                effective_limit,
            )
    except Exception:
        rows = await pg_service.query(
            pool,
            """SELECT
                   c.id,
                   c.document_id,
                   ud.filename,
                   c.page_no,
                   c.chunk_seq,
                   c.section_path,
                   c.chunk_text,
                   c.extraction_confidence,
                   0::float AS rank
               FROM doc_chunks c
               JOIN uploaded_documents ud
                 ON ud.id = c.document_id
               WHERE c.data_product_id = $1::uuid
                 AND COALESCE(ud.is_deleted, false) = false
                 AND ($2::uuid[] IS NULL OR c.document_id = ANY($2::uuid[]))
                 AND (
                     COALESCE(c.chunk_text, '') ILIKE $3
                     OR COALESCE(ud.filename, '') ILIKE $3
                 )
               ORDER BY COALESCE(c.extraction_confidence, 0) DESC, c.chunk_seq ASC
               LIMIT $4""",
            data_product_id,
            active_doc_ids,
            like_pattern,
            effective_limit,
        )

    chunks: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []

    for row in rows:
        chunk_id = str(row.get("id"))
        document_id = str(row.get("document_id"))
        filename = str(row.get("filename") or "Document")
        page_no = row.get("page_no")
        chunk_seq = row.get("chunk_seq")
        score = _as_json_number(row.get("rank"))
        extract_confidence = _as_json_number(row.get("extraction_confidence"))
        raw_text = str(row.get("chunk_text") or "")
        snippet = raw_text[:520].strip()

        chunks.append(
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "filename": filename,
                "page_no": page_no,
                "chunk_seq": chunk_seq,
                "section_path": row.get("section_path"),
                "snippet": snippet,
                "score": score,
                "extraction_confidence": extract_confidence,
            }
        )

        label = filename if page_no is None else f"{filename} (page {page_no})"
        citations.append(
            {
                "citation_type": "document_chunk",
                "reference_id": chunk_id,
                "label": label,
                "page": int(page_no) if isinstance(page_no, int) else None,
                "score": score if score is not None else extract_confidence,
                "metadata": {
                    "document_id": document_id,
                    "chunk_seq": chunk_seq,
                    "section_path": row.get("section_path"),
                },
            }
        )

    confidence_decision = "medium" if chunks else "abstain"
    trust_state = "answer_ready" if chunks else "abstained_missing_evidence"
    evidence_summary = (
        f"Found {len(chunks)} relevant document snippet(s)."
        if chunks
        else "No relevant document snippets were found for this question."
    )

    answer_contract_hint = {
        "source_mode": "document",
        "exactness_state": "not_applicable",
        "confidence_decision": confidence_decision,
        "trust_state": trust_state,
        "evidence_summary": evidence_summary,
        "citations": citations,
        "recovery_actions": (_build_document_recovery_actions(step) if not chunks else []),
        "metadata": {
            "tool": "search_document_chunks",
            "query_text": cleaned_query,
            "step_scope": step,
            "document_scope": "active" if active_doc_ids else "all",
            "result_count": len(chunks),
        },
    }

    return json.dumps(
        {
            "status": "success",
            "tool": "search_document_chunks",
            "query_text": cleaned_query,
            "step_scope": step,
            "document_scope": "active" if active_doc_ids else "all",
            "document_ids": active_doc_ids or [],
            "match_count": len(chunks),
            "chunks": chunks,
            "citations": citations,
            "answer_contract_hint": answer_contract_hint,
        }
    )


@tool
async def save_workspace_state(data_product_id: str, state: str) -> str:
    """Update the state JSONB column on a data product record.

    Used by agents to persist intermediate state (e.g. discovered tables,
    selected schemas, current phase) between conversation turns.

    Args:
        data_product_id: UUID of the data product.
        state: JSON string representing the new state object.
    """
    pool = await _get_pool()

    parsed_state: dict[str, Any] = json.loads(state)

    sql = """
    UPDATE data_products
    SET state = $1::jsonb,
        updated_at = NOW()
    WHERE id = $2::uuid
    """
    result = await pg_service.execute(pool, sql, json.dumps(parsed_state), data_product_id)

    return json.dumps({"status": "ok", "result": result})


@tool
async def load_workspace_state(data_product_id: str) -> str:
    """Read the current state JSONB from a data product record.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()

    sql = """
    SELECT state
    FROM data_products
    WHERE id = $1::uuid
    """
    rows = await pg_service.query(pool, sql, data_product_id)

    if not rows:
        return json.dumps({"error": f"Data product not found: {data_product_id}"})

    state = rows[0]["state"]
    return json.dumps(state) if state else json.dumps({})


@tool
async def save_data_description(
    data_product_id: str,
    description_json: str,
    created_by: str,
) -> str:
    """Persist a data description document for a data product.

    Creates a new data_descriptions row with the provided JSON content.

    Args:
        data_product_id: UUID of the data product.
        description_json: JSON string containing the structured data description.
        created_by: Username of the person who created the description.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    dd_id = str(uuid4())

    # LLM may send raw text or malformed JSON — normalize to valid JSON string
    try:
        parsed = json.loads(description_json)
    except (json.JSONDecodeError, TypeError):
        parsed = {"document": description_json}
    clean_json = json.dumps(parsed)

    sql = """
    INSERT INTO data_descriptions (id, data_product_id, description_json, created_by)
    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4)
    """
    await pg_service.execute(pool, sql, dd_id, data_product_id, clean_json, created_by)

    return json.dumps({"status": "ok", "data_description_id": dd_id})


@tool
async def get_latest_data_description(data_product_id: str) -> str:
    """Retrieve the most recent data description for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    rows = await pg_service.query(
        pool,
        "SELECT description_json, version FROM data_descriptions WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps(
            {"status": "not_found", "message": "No data description found for this data product"}
        )
    return json.dumps(
        {
            "status": "ok",
            "version": rows[0]["version"],
            "description_json": rows[0]["description_json"],
        }
    )


@tool
async def save_brd(
    data_product_id: str,
    brd_json: str,
    created_by: str,
) -> str:
    """Persist a business requirements document for a data product.

    Creates a new business_requirements row with the provided JSON content.

    Args:
        data_product_id: UUID of the data product.
        brd_json: JSON string containing the structured BRD.
        created_by: Username of the person who created the BRD.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    brd_id = str(uuid4())

    # LLM may send raw text or malformed JSON — normalize to valid JSON string
    try:
        parsed = json.loads(brd_json)
    except (json.JSONDecodeError, TypeError):
        # Wrap raw BRD text in a JSON object
        parsed = {"document": brd_json}
    clean_json = json.dumps(parsed)

    sql = """
    INSERT INTO business_requirements (id, data_product_id, brd_json, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, NOW())
    """
    await pg_service.execute(pool, sql, brd_id, data_product_id, clean_json, created_by)

    return json.dumps({"status": "ok", "brd_id": brd_id})


@tool
async def get_latest_brd(data_product_id: str) -> str:
    """Retrieve the most recent BRD for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    rows = await pg_service.query(
        pool,
        "SELECT brd_json, version FROM business_requirements WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "message": "No BRD found for this data product"})
    return json.dumps(
        {"status": "ok", "version": rows[0]["version"], "brd_json": rows[0]["brd_json"]}
    )


async def _strip_unnecessary_casts(yaml_str: str, data_product_id: str) -> str:
    """Remove TRY_CAST/CAST on columns that are already the target numeric type.

    Some LLMs (e.g. gpt-5-mini) add TRY_CAST(COL AS NUMERIC) even when the column
    is already NUMBER/FLOAT/REAL. Snowflake errors: "TRY_CAST cannot be used with
    arguments of types NUMBER(38,0) and FLOAT". This uses the Redis metadata cache
    to detect and strip these unnecessary casts.
    """
    import re
    import yaml as _yaml
    from services.redis import get_client as get_redis

    # Universal safety: TRY_CAST only works on VARCHAR input in Snowflake.
    # Always convert to CAST first (works for any type conversion).
    yaml_str = re.sub(r"\bTRY_CAST\(", "CAST(", yaml_str)

    redis = await get_redis()
    if not redis:
        return yaml_str

    # Build column->data_type map from Redis metadata cache
    col_types: dict[str, str] = {}  # "TABLE.COLUMN" -> data_type
    cache_keys = await redis.keys(f"cache:metadata:{data_product_id}:*")
    for key in cache_keys:
        cached = await redis.get(key)
        if not cached:
            continue
        try:
            import json as _json

            meta = _json.loads(cached) if isinstance(cached, str) else cached
            for col_info in meta if isinstance(meta, list) else []:
                col_name = (
                    col_info.get("COLUMN_NAME") or col_info.get("column_name") or ""
                ).upper()
                col_type = (col_info.get("DATA_TYPE") or col_info.get("data_type") or "").upper()
                if col_name and col_type:
                    col_types[col_name] = col_type
        except Exception:
            continue

    if not col_types:
        logger.info("_strip_unnecessary_casts: no column metadata found, skipping")
        return yaml_str

    _NUMERIC_TYPES = {
        "NUMBER",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "INTEGER",
        "INT",
        "BIGINT",
        "SMALLINT",
        "TINYINT",
        "DECIMAL",
        "NUMERIC",
        "FIXED",
    }

    def _is_numeric_col(col_name: str) -> bool:
        ct = col_types.get(col_name.upper(), "")
        # Handle types like "NUMBER(38,0)" -> "NUMBER"
        base_type = ct.split("(")[0].strip()
        return base_type in _NUMERIC_TYPES

    # Pattern: TRY_CAST(COL_NAME AS NUMERIC/FLOAT/DOUBLE/NUMBER)
    # Also: CAST(COL_NAME AS NUMERIC/FLOAT/DOUBLE/NUMBER)
    pattern = re.compile(
        r"(?:TRY_CAST|CAST)\(\s*([A-Z_][A-Z0-9_]*)\s+AS\s+(?:NUMERIC|FLOAT|DOUBLE|NUMBER|REAL|INTEGER)\s*\)",
        re.IGNORECASE,
    )

    def _replace_cast(match: re.Match) -> str:
        col = match.group(1)
        if _is_numeric_col(col):
            logger.info("_strip_unnecessary_casts: stripped cast on already-numeric column %s", col)
            return col
        return match.group(0)  # Keep the cast if column isn't numeric

    result = pattern.sub(_replace_cast, yaml_str)
    if result != yaml_str:
        logger.info("_strip_unnecessary_casts: cleaned unnecessary casts in YAML")
    return result


def _repair_yaml_description_scalars(yaml_text: str) -> tuple[str, bool]:
    """Quote unquoted description values that contain `:` and break YAML parsing."""
    repaired_lines: list[str] = []
    changed = False

    for line in yaml_text.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]

        if not stripped.lower().startswith("description:"):
            repaired_lines.append(line)
            continue

        _, raw_value = stripped.split(":", 1)
        value = raw_value.strip()
        if not value:
            repaired_lines.append(line)
            continue
        if value.startswith(("'", '"', "|", ">", "{", "[")):
            repaired_lines.append(line)
            continue

        # YAML plain scalars often fail when they contain `: `.
        # Quote these values to keep save_semantic_view resilient.
        if ": " in value:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            repaired_lines.append(f'{indent}description: "{escaped}"')
            changed = True
            continue

        repaired_lines.append(line)

    return "\n".join(repaired_lines), changed


@tool
async def save_semantic_view(
    data_product_id: str,
    yaml_content: str,
    created_by: str,
) -> str:
    """Persist a semantic view YAML for a data product.

    Creates a new semantic_views row with the YAML content.
    If the content is JSON (structured output from the generation agent),
    it is automatically assembled into Snowflake-compatible YAML.

    Production hardening:
    - Dedup guard: skips save if an assembled version already exists within 2 minutes
    - JSON input always routed through template assembler (deterministic YAML)
    - Raw YAML input validated for required structure before saving
    - All YAML validated with yaml.safe_load before persistence

    Args:
        data_product_id: UUID of the data product.
        yaml_content: The semantic view YAML string (or JSON structure).
        created_by: Username of the person who created the semantic view.
    """
    import yaml as _yaml

    data_product_id = _resolve_dp_id(data_product_id)
    content = yaml_content.strip()
    is_json = content.startswith("{")
    logger.info(
        "save_semantic_view: received %d chars, is_json=%s, dp_id=%s, first_100=%s",
        len(content),
        is_json,
        data_product_id,
        repr(content[:100]),
    )

    pool = await _get_pool()

    # ── Dedup guard: if a version was saved very recently (within 2 min),
    # skip this save. Prevents LLM calling save twice in one generation cycle
    # (once with JSON → assembler, once with raw YAML → buggy). ──
    recent_rows = await pg_service.query(
        pool,
        "SELECT id, LENGTH(yaml_content) as len FROM semantic_views "
        "WHERE data_product_id = $1::uuid AND created_at > NOW() - INTERVAL '2 minutes' "
        "ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if recent_rows and not is_json:
        # A version was already saved recently (likely from the assembler path).
        # Raw YAML saves are lower quality — skip to keep the assembled version.
        logger.info(
            "save_semantic_view: DEDUP GUARD — skipping raw YAML save, "
            "assembled version already exists (id=%s, %d chars, saved <2min ago)",
            recent_rows[0]["id"],
            recent_rows[0]["len"],
        )
        return json.dumps(
            {
                "status": "ok",
                "semantic_view_id": str(recent_rows[0]["id"]),
                "note": "Using previously saved assembled version (dedup guard)",
            }
        )

    if is_json:
        try:
            from agents.generation import (
                extract_json_from_text,
                assemble_semantic_view_yaml,
                build_table_metadata,
                _build_fqn_sample_values,
                build_working_layer_map,
            )

            logger.info("save_semantic_view: extracting JSON from text...")
            structure = extract_json_from_text(content)
            if structure and "tables" in structure:
                logger.info("save_semantic_view: building table metadata...")
                meta = await build_table_metadata(data_product_id, structure)
                logger.info("save_semantic_view: building sample values map...")
                sv_map = await _build_fqn_sample_values(data_product_id)
                wl_map = await build_working_layer_map(data_product_id)
                logger.info(
                    "save_semantic_view: assembling YAML (meta=%d tables, sv_map=%d, wl=%d)...",
                    len(meta),
                    len(sv_map),
                    len(wl_map),
                )
                assembled = assemble_semantic_view_yaml(
                    structure,
                    table_metadata=meta,
                    sample_values_map=sv_map,
                    working_layer_map=wl_map,
                )
                if assembled and len(assembled) > 50:
                    logger.info(
                        "save_semantic_view: auto-assembled JSON to YAML (%d chars, meta=%d tables)",
                        len(assembled),
                        len(meta),
                    )
                    content = assembled
                else:
                    logger.warning(
                        "save_semantic_view: assembly returned empty/short result (%s chars)",
                        len(assembled) if assembled else 0,
                    )
            else:
                logger.warning(
                    "save_semantic_view: extract_json_from_text returned no tables structure=%s",
                    bool(structure),
                )
        except Exception as e:
            logger.warning("save_semantic_view: failed to auto-assemble JSON to YAML: %s", e)
    else:
        # Content is raw YAML — apply column quoting and expression cleanup
        try:
            from agents.generation import quote_columns_in_yaml_str

            logger.info("save_semantic_view: applying YAML column quoting...")
            content = await quote_columns_in_yaml_str(content, data_product_id)
            logger.info("save_semantic_view: YAML column quoting done (%d chars)", len(content))
        except Exception as e:
            logger.warning("save_semantic_view: failed to apply YAML column quoting: %s", e)

        # Strip unnecessary TRY_CAST on columns that are already numeric
        try:
            content = await _strip_unnecessary_casts(content, data_product_id)
        except Exception as e:
            logger.warning("save_semantic_view: failed to strip unnecessary casts: %s", e)

    # ── YAML structure validation before saving ──
    try:
        parsed_obj = _yaml.safe_load(content)
    except _yaml.YAMLError as e:
        repaired_content, repaired = _repair_yaml_description_scalars(content)
        if not repaired:
            logger.error("save_semantic_view: YAML parse error: %s", e)
            return json.dumps({"status": "error", "error": f"Invalid YAML syntax: {e}"})
        try:
            parsed_obj = _yaml.safe_load(repaired_content)
            content = repaired_content
            logger.info("save_semantic_view: auto-repaired YAML description quoting issue")
        except _yaml.YAMLError as repaired_err:
            logger.error(
                "save_semantic_view: YAML parse error after auto-repair attempt: %s",
                repaired_err,
            )
            return json.dumps({"status": "error", "error": f"Invalid YAML syntax: {repaired_err}"})

    if not isinstance(parsed_obj, dict):
        return json.dumps({"status": "error", "error": "YAML content is not a valid mapping"})
    if "name" not in parsed_obj:
        return json.dumps({"status": "error", "error": "YAML missing required 'name' field"})
    if "tables" not in parsed_obj or not parsed_obj["tables"]:
        return json.dumps({"status": "error", "error": "YAML missing required 'tables' list"})
    for i, tbl in enumerate(parsed_obj["tables"]):
        if not tbl.get("base_table"):
            return json.dumps({"status": "error", "error": f"Table #{i} missing 'base_table'"})
        bt = tbl["base_table"]
        for key in ("database", "schema", "table"):
            if not bt.get(key):
                return json.dumps(
                    {"status": "error", "error": f"Table #{i} base_table missing '{key}'"}
                )

    # Data isolation guard: semantic model can only use selected source tables
    # plus internal EKAIX-managed curated/marts objects.
    try:
        from tools.snowflake_tools import _allowed_tables

        allowed_tables = {t.upper() for t in (_allowed_tables.get() or [])}
        if allowed_tables:
            for i, tbl in enumerate(parsed_obj["tables"]):
                bt = tbl.get("base_table", {})
                db = str(bt.get("database", "")).strip('"').upper()
                schema = str(bt.get("schema", "")).strip('"').upper()
                table = str(bt.get("table", "")).strip('"').upper()
                base_fqn = f"{db}.{schema}.{table}"
                if db != "EKAIX" and base_fqn not in allowed_tables:
                    return json.dumps(
                        {
                            "status": "error",
                            "error": (
                                f"Table #{i} base_table '{base_fqn}' is outside the selected "
                                "data product scope."
                            ),
                        }
                    )
    except Exception as scope_err:
        logger.warning(
            "save_semantic_view: scope validation skipped due to internal error: %s", scope_err
        )

    logger.info(
        "save_semantic_view: YAML structure validation passed (%d tables)",
        len(parsed_obj["tables"]),
    )

    sv_id = str(uuid4())

    sql = """
    INSERT INTO semantic_views (id, data_product_id, yaml_content, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3, $4, NOW())
    """
    await pg_service.execute(pool, sql, sv_id, data_product_id, content, created_by)

    return json.dumps({"status": "ok", "semantic_view_id": sv_id})


@tool
async def get_latest_semantic_view(data_product_id: str) -> str:
    """Retrieve the most recent semantic view YAML for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    rows = await pg_service.query(
        pool,
        "SELECT yaml_content, version, validation_status FROM semantic_views WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps(
            {"status": "not_found", "message": "No semantic view found for this data product"}
        )
    return json.dumps(
        {
            "status": "ok",
            "version": rows[0]["version"],
            "yaml_content": rows[0]["yaml_content"],
            "validation_status": rows[0].get("validation_status"),
        }
    )


@tool
async def update_validation_status(
    data_product_id: str,
    status: str,
    errors: str = "",
) -> str:
    """Update the validation status of the latest semantic view.

    Args:
        data_product_id: UUID of the data product.
        status: New validation status (valid, invalid, pending).
        errors: JSON string of validation errors (empty if valid).
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()

    # Parse errors to ensure valid JSON
    try:
        parsed_errors = json.loads(errors) if errors else []
    except (json.JSONDecodeError, TypeError):
        parsed_errors = [{"message": errors}] if errors else []

    sql = """
    UPDATE semantic_views
    SET validation_status = $1,
        validation_errors = $2::jsonb,
        validated_at = NOW()
    WHERE data_product_id = $3::uuid
    AND version = (
        SELECT MAX(version) FROM semantic_views WHERE data_product_id = $3::uuid
    )
    """
    await pg_service.execute(pool, sql, status, json.dumps(parsed_errors), data_product_id)

    return json.dumps({"status": "ok", "validation_status": status})


def _extract_agent_fqn(details: Any) -> str | None:
    """Extract DATABASE.SCHEMA.OBJECT from action details when available."""
    if isinstance(details, dict):
        for key in ("agent_fqn", "published_agent_fqn", "ai_agent_fqn", "cortex_agent_fqn"):
            value = details.get(key)
            if isinstance(value, str) and value.count(".") == 2:
                return value.strip('"')
        # Fallback: search the serialized payload
        haystack = json.dumps(details)
    elif isinstance(details, str):
        haystack = details
    else:
        haystack = str(details)

    matches = re.findall(r"\b([A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+)\b", haystack)
    if not matches:
        return None
    for candidate in matches:
        obj_name = candidate.split(".")[-1].upper()
        if "AGENT" in obj_name:
            return candidate
    return matches[0]


def _is_successful_publish_payload(details: Any) -> bool:
    """Return True only when publish details indicate a successful deployment."""
    if not isinstance(details, dict):
        return False

    # Explicit error always means publish did not complete.
    if details.get("error") or details.get("exception"):
        return False

    raw_status = str(details.get("status") or "").strip().lower()
    if raw_status in {"failed", "error", "blocked", "denied", "aborted"}:
        return False

    success_markers = (
        bool(details.get("published") is True),
        raw_status in {"success", "ok", "completed", "published"},
        str(details.get("result") or "").strip().lower() in {"success", "ok", "published"},
    )
    return any(success_markers)


@tool
async def save_quality_report(
    data_product_id: str,
    overall_score: int,
    check_results: str,
    issues: str,
) -> str:
    """Persist a data quality report for a data product.

    Creates a row in the data_quality_checks table. This is REQUIRED after
    running quality checks during discovery.

    Args:
        data_product_id: UUID of the data product.
        overall_score: Health score between 0 and 100.
        check_results: JSON string of detailed per-check results.
        issues: JSON string array of issues found.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    report_id = str(uuid4())

    sql = """
    INSERT INTO data_quality_checks (id, data_product_id, overall_score, check_results, issues)
    VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb)
    """
    await pg_service.execute(
        pool, sql, report_id, data_product_id, overall_score, check_results, issues
    )

    return json.dumps({"status": "ok", "report_id": report_id, "overall_score": overall_score})


@tool
async def log_agent_action(
    data_product_id: str,
    action_type: str,
    details: str,
    user_name: str,
) -> str:
    """Write an entry to the audit_logs table.

    Records agent actions for compliance and debugging purposes.

    Args:
        data_product_id: UUID of the data product (workspace_id is resolved automatically).
        action_type: Category of action (e.g. 'discovery', 'generation', 'publish').
        details: JSON string with action details.
        user_name: Username of the acting user.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    try:
        pool = await _get_pool()
        log_id = str(uuid4())

        # Normalize details into valid JSONB payload
        try:
            parsed_details = json.loads(details) if isinstance(details, str) else details
        except (json.JSONDecodeError, TypeError):
            parsed_details = {"message": str(details)}
        details_json = json.dumps(parsed_details if parsed_details is not None else {})

        # Resolve workspace_id from data_product_id
        ws_rows = await pg_service.query(
            pool,
            "SELECT workspace_id FROM data_products WHERE id = $1::uuid",
            data_product_id,
        )
        workspace_id = str(ws_rows[0]["workspace_id"]) if ws_rows else None

        if not workspace_id:
            logger.warning(
                "log_agent_action: no workspace found for data_product_id %s", data_product_id
            )
            return json.dumps(
                {
                    "status": "ok",
                    "log_id": log_id,
                    "note": "audit log skipped — workspace not found",
                }
            )

        sql = """
        INSERT INTO audit_logs (id, workspace_id, data_product_id, action_type, action_details, user_name, created_at)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::jsonb, $6, NOW())
        """
        await pg_service.execute(
            pool, sql, log_id, workspace_id, data_product_id, action_type, details_json, user_name
        )

        # Publishing side-effects: persist canonical published state only when the
        # publish action is explicitly successful and a concrete agent FQN exists.
        if action_type.strip().lower() == "publish":
            publish_succeeded = _is_successful_publish_payload(parsed_details)
            agent_fqn = _extract_agent_fqn(parsed_details)
            if publish_succeeded and agent_fqn:
                try:
                    await pg_service.execute(
                        pool,
                        """UPDATE data_products
                           SET status = 'published'::data_product_status,
                               published_at = NOW(),
                               published_agent_fqn = $1,
                               state = jsonb_set(
                                   jsonb_set(COALESCE(state, '{}'::jsonb), '{current_phase}', '"explorer"'::jsonb),
                                   '{published}',
                                   'true'::jsonb
                               ),
                               updated_at = NOW()
                           WHERE id = $2::uuid""",
                        agent_fqn,
                        data_product_id,
                    )
                except Exception as publish_err:
                    logger.warning(
                        "log_agent_action: failed to persist publish metadata: %s", publish_err
                    )
            else:
                logger.info(
                    "log_agent_action: skipping publish-state update (success=%s, agent_fqn=%s)",
                    publish_succeeded,
                    bool(agent_fqn),
                )

        return json.dumps({"status": "ok", "log_id": log_id})
    except Exception as e:
        logger.error("log_agent_action failed: %s", e)
        return json.dumps(
            {
                "status": "ok",
                "log_id": str(uuid4()),
                "note": "audit log skipped due to internal error",
            }
        )


@tool
async def verify_brd_completeness(data_product_id: str) -> str:
    """Verify that the latest BRD is complete and well-formed.

    Checks:
    - All 7 sections present
    - No placeholder text ([TBD], [TODO], etc.)
    - Table references match discovered tables from Redis metadata cache

    Args:
        data_product_id: UUID of the data product.

    Returns:
        JSON: {"status": "pass"|"fail", "issues": [...], "section_count": N}
    """
    data_product_id = _resolve_dp_id(data_product_id)
    issues: list[str] = []

    try:
        pool = await _get_pool()

        # Load latest BRD
        rows = await pg_service.query(
            pool,
            "SELECT brd_json FROM business_requirements WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
            data_product_id,
        )
        if not rows:
            return json.dumps({"status": "fail", "issues": ["No BRD found"], "section_count": 0})

        brd_json = rows[0].get("brd_json")
        brd_text = ""
        if isinstance(brd_json, dict):
            brd_text = brd_json.get("document", str(brd_json))
        elif isinstance(brd_json, str):
            try:
                parsed = json.loads(brd_json)
                brd_text = parsed.get("document", brd_json)
            except (json.JSONDecodeError, TypeError):
                brd_text = brd_json

        # Check sections
        section_markers = {
            "SECTION 1:": "Executive Summary",
            "SECTION 2:": "Metrics and Calculations",
            "SECTION 3:": "Dimensions and Filters",
            "SECTION 4:": "Table Relationships",
            "SECTION 5:": "Data Requirements",
            "SECTION 6:": "Data Quality Rules",
            "SECTION 7:": "Sample Questions",
        }
        section_count = 0
        for marker, name in section_markers.items():
            if marker in brd_text:
                section_count += 1
            else:
                issues.append(f"Missing {name} ({marker})")

        # Check for placeholder text
        placeholders = ["[TBD]", "[TODO]", "[PLACEHOLDER]", "[FILL IN]", "[INSERT"]
        for p in placeholders:
            count = brd_text.upper().count(p.upper())
            if count > 0:
                issues.append(f"Found {count} instance(s) of placeholder '{p}'")

        # Check table references against Redis metadata cache
        try:
            from services.redis import get_client as get_redis

            redis = await get_redis()
            if redis:
                cache_keys = await redis.keys(f"cache:metadata:{data_product_id}:*")
                discovered_tables = set()
                for key in cache_keys:
                    # Key format: cache:metadata:{dp_id}:{DB}.{SCHEMA}.{TABLE}
                    parts = key.split(":")
                    if len(parts) >= 4:
                        fqn = parts[3] if isinstance(parts[3], str) else parts[3].decode()
                        table_name = fqn.split(".")[-1]
                        discovered_tables.add(table_name.upper())

                if discovered_tables:
                    # Extract table names mentioned in SECTION 5
                    import re

                    section5_match = re.search(
                        r"SECTION 5.*?(?=SECTION 6|---END|$)", brd_text, re.DOTALL
                    )
                    if section5_match:
                        section5 = section5_match.group(0)
                        # Look for table name patterns (ALL_CAPS words)
                        brd_tables = set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", section5))
                        # Filter to likely table names (not generic words)
                        generic = {
                            "SECTION",
                            "TABLE",
                            "PURPOSE",
                            "FIELDS",
                            "DATA",
                            "PRODUCT",
                            "QUALITY",
                            "RULES",
                            "SAMPLE",
                            "QUESTIONS",
                            "METRICS",
                            "DIMENSIONS",
                            "FILTERS",
                            "REQUIREMENTS",
                            "EXECUTIVE",
                            "SUMMARY",
                            "RELATIONSHIPS",
                            "THE",
                            "FOR",
                            "AND",
                            "NOT",
                            "ALL",
                            "BRD",
                            "BEGIN",
                            "END",
                            "NUMBER",
                            "VARCHAR",
                            "DATE",
                            "BOOLEAN",
                            "TIMESTAMP",
                        }
                        brd_tables -= generic
                        for t in brd_tables:
                            if t not in discovered_tables and not any(
                                t in dt for dt in discovered_tables
                            ):
                                issues.append(
                                    f"BRD references table '{t}' not found in discovered tables"
                                )
        except Exception as e:
            logger.debug("verify_brd: Redis check failed: %s", e)

        status = "pass" if not issues else "fail"
        return json.dumps({"status": status, "issues": issues, "section_count": section_count})

    except Exception as e:
        logger.error("verify_brd_completeness failed: %s", e)
        return json.dumps({"status": "error", "issues": [str(e)], "section_count": 0})
