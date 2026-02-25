"""Document extraction and graph normalization endpoints."""

import base64
import io
import json
import logging
import re
import uuid
import zipfile
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from services.llm import get_chat_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])

MAX_FALLBACK_BYTES = 12 * 1024 * 1024
MAX_EXTRACTED_CHARS = 500_000
MAX_LLM_TEXT_BLOCK_CHARS = 120_000
MAX_ARCHIVE_MEMBERS = 48
MAX_ARCHIVE_ENTRY_BYTES = 1_500_000

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".sql",
    ".ddl",
    ".dbml",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".m",
    ".dax",
}

PBIX_LAYOUT_PATH = "report/layout"
PBIX_MODEL_HINTS = ("datamodelschema", "diagramlayout", "datamashup")


class DocumentExtractRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    base64_data: str = Field(min_length=1)


class DocumentExtractResponse(BaseModel):
    status: Literal["completed", "pending", "failed"]
    method: str
    extracted_text: str | None = None
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _decode_base64_bytes(base64_data: str) -> bytes | None:
    try:
        return base64.b64decode(base64_data, validate=True)
    except Exception:
        return None


def _is_mostly_readable(text: str) -> bool:
    sample = text[:20_000]
    if not sample:
        return False
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\r\t")
    ratio = printable / max(len(sample), 1)
    return ratio >= 0.85


def _decode_text_bytes(raw: bytes) -> str | None:
    if not raw:
        return None

    for encoding in ("utf-8", "utf-8-sig", "utf-16-le", "utf-16", "latin-1"):
        try:
            decoded = raw.decode(encoding)
            normalized = decoded.strip()
            if not normalized:
                continue
            if _is_mostly_readable(normalized):
                return normalized[:MAX_EXTRACTED_CHARS]
        except Exception:
            continue
    return None


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    if not text:
        return None

    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        try:
            parsed = json.loads(text[first:last + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _flatten_text(value: Any, prefix: str = "") -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            return []
        return [f"{prefix}: {normalized}" if prefix else normalized]
    if isinstance(value, (int, float, bool)):
        return [f"{prefix}: {value}" if prefix else str(value)]
    if isinstance(value, list):
        rows: list[str] = []
        for idx, item in enumerate(value):
            rows.extend(_flatten_text(item, f"{prefix}[{idx}]" if prefix else f"[{idx}]"))
            if len(rows) > 120:
                break
        return rows
    if isinstance(value, dict):
        rows: list[str] = []
        for key, item in value.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten_text(item, label))
            if len(rows) > 160:
                break
        return rows
    return []


def _response_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return str(content)


def _looks_like_zip(raw: bytes) -> bool:
    return raw.startswith(b"PK\x03\x04")


def _looks_like_pbix(filename: str, members: list[str]) -> bool:
    lower = (filename or "").lower()
    if lower.endswith(".pbix"):
        return True
    member_set = {name.lower() for name in members}
    if PBIX_LAYOUT_PATH in member_set:
        return True
    return any(any(hint in member for hint in PBIX_MODEL_HINTS) for member in member_set)


def _summarize_archive_text(filename: str, text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line:
        return first_line[:280]
    return f"Extracted structured text from archive-backed file: {filename}"[:280]


def _extract_archive_text(filename: str, raw: bytes) -> DocumentExtractResponse | None:
    if not _looks_like_zip(raw):
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            members = [info.filename for info in zf.infolist() if not info.is_dir()]
            pbix_detected = _looks_like_pbix(filename, members)
            snippets: list[str] = []
            warnings: list[str] = []

            layout_text = ""
            for idx, member in enumerate(members):
                if idx >= MAX_ARCHIVE_MEMBERS:
                    warnings.append("Archive scan truncated due to member limit.")
                    break

                lower = member.lower()
                ext = "." + lower.rsplit(".", 1)[1] if "." in lower else ""
                should_attempt = ext in TEXT_EXTENSIONS or lower == PBIX_LAYOUT_PATH or "datamodel" in lower
                if not should_attempt:
                    continue

                info = zf.getinfo(member)
                if info.file_size <= 0:
                    continue
                if info.file_size > MAX_ARCHIVE_ENTRY_BYTES and lower != PBIX_LAYOUT_PATH:
                    continue

                try:
                    entry_bytes = zf.read(member)
                except Exception:
                    continue

                decoded = _decode_text_bytes(entry_bytes[:MAX_ARCHIVE_ENTRY_BYTES])
                if not decoded:
                    continue
                if lower == PBIX_LAYOUT_PATH:
                    layout_text = decoded

                snippet = decoded.strip()
                if len(snippet) < 20:
                    continue
                snippets.append(f"[{member}]\n{snippet[:12_000]}")

                if sum(len(s) for s in snippets) >= MAX_EXTRACTED_CHARS:
                    warnings.append("Archive text extraction truncated due to size.")
                    break

            if pbix_detected:
                query_refs = sorted(set(re.findall(r'"queryRef"\s*:\s*"([^"]+)"', layout_text)))
                visual_types = sorted(set(re.findall(r'"visualType"\s*:\s*"([^"]+)"', layout_text)))
                table_names = sorted({ref.split(".", 1)[0] for ref in query_refs if "." in ref})
                pbix_lines = [
                    f"[Power BI model hints from {filename}]",
                    f"- Referenced tables: {', '.join(table_names[:40])}" if table_names else "- Referenced tables: none",
                    f"- Referenced fields/measures: {', '.join(query_refs[:80])}" if query_refs else "- Referenced fields/measures: none",
                    f"- Visual types: {', '.join(visual_types[:20])}" if visual_types else "- Visual types: none",
                ]
                snippets.insert(0, "\n".join(pbix_lines))

            if not snippets:
                return DocumentExtractResponse(
                    status="pending",
                    method="archive_structured_fallback",
                    warnings=["Archive detected, but no readable structured content was found."],
                    metadata={
                        "archive_entries": len(members),
                        "pbix_detected": pbix_detected,
                    },
                )

            extracted_text = "\n\n".join(snippets)[:MAX_EXTRACTED_CHARS]
            return DocumentExtractResponse(
                status="completed",
                method="archive_structured_fallback",
                extracted_text=extracted_text,
                summary=_summarize_archive_text(filename, extracted_text),
                warnings=warnings,
                metadata={
                    "archive_entries": len(members),
                    "selected_entries": len(snippets),
                    "pbix_detected": pbix_detected,
                },
            )
    except Exception:
        return None


def _build_llm_blocks(req: DocumentExtractRequest) -> list[dict[str, Any]]:
    mime = req.content_type.lower().split(";", 1)[0].strip() or "application/octet-stream"

    instructions = (
        "Extract structured business context from the attached file. "
        "Return strict JSON with keys: summary, extracted_text, tables, entities, metrics, rules, confidence, notes. "
        "No markdown, no explanations outside JSON."
    )
    blocks: list[dict[str, Any]] = [{"type": "text", "text": instructions}]

    text_like = (
        mime.startswith("text/")
        or mime in {"application/json", "application/xml", "application/csv", "text/csv"}
        or req.filename.lower().endswith((".sql", ".ddl", ".dbml", ".yaml", ".yml", ".md"))
    )
    if text_like:
        decoded = _decode_text_bytes(_decode_base64_bytes(req.base64_data) or b"")
        if decoded:
            blocks.append(
                {
                    "type": "text",
                    "text": f"[Attached file: {req.filename}]\n{decoded[:MAX_LLM_TEXT_BLOCK_CHARS]}",
                }
            )
            return blocks

    if mime.startswith("image/"):
        try:
            from PIL import Image

            raw = _decode_base64_bytes(req.base64_data) or b""
            with Image.open(io.BytesIO(raw)) as img:
                rgb = img.convert("RGB")
                pdf_buffer = io.BytesIO()
                rgb.save(pdf_buffer, format="PDF")
            pdf_b64 = base64.b64encode(pdf_buffer.getvalue()).decode()
            blocks.append(
                {
                    "type": "file",
                    "file": {
                        "filename": f"{req.filename}.pdf",
                        "file_data": f"data:application/pdf;base64,{pdf_b64}",
                    },
                }
            )
            blocks.append(
                {
                    "type": "text",
                    "text": "Image was converted to a one-page PDF for provider compatibility.",
                }
            )
            return blocks
        except Exception:
            # Fall through to direct binary file mode.
            pass

    blocks.append(
        {
            "type": "file",
            "file": {
                "filename": req.filename,
                "file_data": f"data:{mime};base64,{req.base64_data}",
            },
        }
    )
    return blocks


@router.post("/extract", response_model=DocumentExtractResponse)
async def extract_document(req: DocumentExtractRequest) -> DocumentExtractResponse:
    raw = _decode_base64_bytes(req.base64_data)
    if raw is None:
        return DocumentExtractResponse(
            status="failed",
            method="decode_error",
            warnings=["Invalid base64 payload."],
        )

    raw_bytes = len(raw)
    if raw_bytes > MAX_FALLBACK_BYTES:
        return DocumentExtractResponse(
            status="pending",
            method="ai_multimodal_fallback",
            warnings=[
                (
                    "Document too large for LLM fallback extraction. "
                    "Use Snowflake stage parsing or upload a smaller file."
                )
            ],
            metadata={"max_bytes": MAX_FALLBACK_BYTES, "received_bytes": raw_bytes},
        )

    decoded_text = _decode_text_bytes(raw)
    if decoded_text:
        summary = next((line.strip() for line in decoded_text.splitlines() if line.strip()), "")
        return DocumentExtractResponse(
            status="completed",
            method="text_decode_fallback",
            extracted_text=decoded_text[:MAX_EXTRACTED_CHARS],
            summary=summary[:280] if summary else None,
            metadata={"bytes": raw_bytes},
        )

    archive_result = _extract_archive_text(req.filename, raw)
    if archive_result and archive_result.status == "completed":
        return archive_result

    blocks = _build_llm_blocks(req)

    try:
        model = get_chat_model()
        response = await model.ainvoke([HumanMessage(content=blocks)])
        response_text = _response_content_to_text(response.content)
        parsed = _extract_json_from_text(response_text)

        if not parsed:
            fallback_text = response_text.strip()
            if not fallback_text:
                warnings = ["LLM response did not include parseable content."]
                if archive_result and archive_result.warnings:
                    warnings.extend(archive_result.warnings)
                return DocumentExtractResponse(
                    status="pending",
                    method="ai_multimodal_fallback",
                    warnings=warnings,
                )
            return DocumentExtractResponse(
                status="completed",
                method="ai_multimodal_fallback",
                extracted_text=fallback_text[:MAX_LLM_TEXT_BLOCK_CHARS],
                summary=fallback_text[:280],
                warnings=["LLM response was not strict JSON; used plain-text fallback."],
            )

        summary = parsed.get("summary")
        extracted_text = parsed.get("extracted_text")

        lines: list[str] = []
        if isinstance(extracted_text, str) and extracted_text.strip():
            lines.append(extracted_text.strip())
        else:
            lines.extend(_flatten_text(parsed.get("tables"), "tables"))
            lines.extend(_flatten_text(parsed.get("entities"), "entities"))
            lines.extend(_flatten_text(parsed.get("metrics"), "metrics"))
            lines.extend(_flatten_text(parsed.get("rules"), "rules"))

        final_text = "\n".join(lines).strip()[:MAX_EXTRACTED_CHARS] if lines else None
        if not final_text and isinstance(summary, str) and summary.strip():
            final_text = summary.strip()

        status: Literal["completed", "pending"] = "completed" if final_text else "pending"
        warnings: list[str] = []
        if archive_result and archive_result.warnings:
            warnings.extend(archive_result.warnings)
        return DocumentExtractResponse(
            status=status,
            method="ai_multimodal_fallback",
            extracted_text=final_text,
            summary=summary.strip()[:280] if isinstance(summary, str) and summary.strip() else None,
            warnings=warnings,
            metadata={
                "confidence": parsed.get("confidence"),
                "notes": parsed.get("notes"),
            },
        )
    except Exception as e:
        logger.warning("Multimodal extraction failed for %s: %s", req.filename, e)
        warnings = [f"LLM extraction failed: {e}"]
        if archive_result and archive_result.warnings:
            warnings.extend(archive_result.warnings)
        return DocumentExtractResponse(
            status="failed",
            method="ai_multimodal_fallback",
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Normalize-to-graph: extract structured facts from document text and persist
# them as Document/Chunk/Fact/Entity nodes in the Neo4j knowledge graph.
# ---------------------------------------------------------------------------


class NormalizeToGraphRequest(BaseModel):
    data_product_id: str
    document_id: str
    extracted_text: str = Field(min_length=1)
    title: str = ""
    mime_type: str = "application/octet-stream"


class NormalizeToGraphResponse(BaseModel):
    status: str
    chunks_count: int = 0
    facts_count: int = 0
    entities_count: int = 0


@router.post("/normalize-to-graph", response_model=NormalizeToGraphResponse)
async def normalize_to_graph(req: NormalizeToGraphRequest) -> NormalizeToGraphResponse:
    """Extract structured facts from document text and persist to Neo4j graph.

    Uses LLM structured output to identify chunks, facts, and entities,
    then writes them to the document intelligence graph.
    """
    from tools.neo4j_document_tools import (
        upsert_document_node,
        upsert_document_chunks,
        upsert_document_facts,
        link_fact_to_entity,
    )

    # Step 1: Upsert the Document node
    try:
        await upsert_document_node.ainvoke({
            "data_product_id": req.data_product_id,
            "document_id": req.document_id,
            "title": req.title,
            "mime_type": req.mime_type,
        })
    except Exception as e:
        logger.error("Failed to upsert document node: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create document node: {e}")

    # Step 2: Use LLM to extract chunks, facts, and entities
    extraction_prompt = (
        "Analyze this document text and extract structured knowledge.\n"
        "Return strict JSON with this structure:\n"
        "{\n"
        '  "chunks": [{"text": "section text", "position": 0}],\n'
        '  "facts": [{"statement": "fact statement", "category": "metric|rule|definition|relationship", "chunk_position": 0}],\n'
        '  "entities": [{"name": "entity name", "entity_type": "metric|person|concept|organization|table|column", "fact_indices": [0]}]\n'
        "}\n\n"
        "Rules:\n"
        "- Break text into logical sections as chunks\n"
        "- Extract concrete, verifiable facts from each chunk\n"
        "- Identify named entities mentioned in facts\n"
        "- fact_indices references the 0-based index in the facts array\n"
        "- No markdown, no explanations outside JSON\n\n"
        f"Document text:\n{req.extracted_text[:MAX_LLM_TEXT_BLOCK_CHARS]}"
    )

    try:
        model = get_chat_model()
        response = await model.ainvoke([HumanMessage(content=extraction_prompt)])
        response_text = _response_content_to_text(response.content)
        parsed = _extract_json_from_text(response_text)
    except Exception as e:
        logger.warning("LLM extraction for graph normalization failed: %s", e)
        return NormalizeToGraphResponse(status="partial", chunks_count=0, facts_count=0, entities_count=0)

    if not parsed:
        logger.warning("LLM did not return parseable JSON for graph normalization")
        return NormalizeToGraphResponse(status="partial", chunks_count=0, facts_count=0, entities_count=0)

    chunks_raw = parsed.get("chunks", [])
    facts_raw = parsed.get("facts", [])
    entities_raw = parsed.get("entities", [])

    # Step 3: Upsert chunks
    chunk_id_map: dict[int, str] = {}
    chunks_for_neo4j = []
    for i, chunk in enumerate(chunks_raw):
        chunk_id = str(uuid.uuid4())
        chunk_id_map[chunk.get("position", i)] = chunk_id
        chunks_for_neo4j.append({
            "chunk_id": chunk_id,
            "text": chunk.get("text", ""),
            "position": chunk.get("position", i),
        })

    chunks_count = 0
    if chunks_for_neo4j:
        try:
            await upsert_document_chunks.ainvoke({
                "document_id": req.document_id,
                "chunks": json.dumps(chunks_for_neo4j),
            })
            chunks_count = len(chunks_for_neo4j)
        except Exception as e:
            logger.error("Failed to upsert document chunks: %s", e)

    # Step 4: Upsert facts per chunk
    fact_id_list: list[str] = []
    facts_count = 0
    # Group facts by their chunk
    chunk_facts: dict[str, list[dict[str, Any]]] = {}
    for fact in facts_raw:
        chunk_pos = fact.get("chunk_position", 0)
        chunk_id = chunk_id_map.get(chunk_pos, list(chunk_id_map.values())[0] if chunk_id_map else "")
        if not chunk_id:
            continue
        fact_id = str(uuid.uuid4())
        fact_id_list.append(fact_id)
        chunk_facts.setdefault(chunk_id, []).append({
            "fact_id": fact_id,
            "statement": fact.get("statement", ""),
            "category": fact.get("category"),
        })

    for chunk_id, facts in chunk_facts.items():
        try:
            await upsert_document_facts.ainvoke({
                "chunk_id": chunk_id,
                "facts": json.dumps(facts),
            })
            facts_count += len(facts)
        except Exception as e:
            logger.error("Failed to upsert facts for chunk %s: %s", chunk_id, e)

    # Step 5: Link entities to facts
    entities_count = 0
    for entity in entities_raw:
        entity_name = entity.get("name", "")
        entity_type = entity.get("entity_type", "concept")
        fact_indices = entity.get("fact_indices", [])

        for idx in fact_indices:
            if 0 <= idx < len(fact_id_list):
                try:
                    await link_fact_to_entity.ainvoke({
                        "fact_id": fact_id_list[idx],
                        "entity_name": entity_name,
                        "entity_type": entity_type,
                    })
                except Exception as e:
                    logger.error("Failed to link entity %s to fact: %s", entity_name, e)
        entities_count += 1

    return NormalizeToGraphResponse(
        status="completed",
        chunks_count=chunks_count,
        facts_count=facts_count,
        entities_count=entities_count,
    )
