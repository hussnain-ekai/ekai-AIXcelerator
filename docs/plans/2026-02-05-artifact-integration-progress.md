# Artifact Integration Progress — 2026-02-05

**Updated:** 2026-02-07

## Summary

Artifact integration is now **fully verified end-to-end** after switching from Cortex to Vertex AI (Gemini). The discovery pipeline handles artifact persistence directly (not via LLM tool calls), which resolved the reliability issues. ERD and Data Quality Report artifacts work correctly with real Snowflake data.

## What Was Completed (Code Done)

### Step 1: AI Service — Artifact SSE Events

- **File:** `ai-service/routers/agent.py`
- Added artifact event detection in `on_tool_end` handler
- When `upload_artifact` tool completes, parses result and emits `{type: "artifact", data: {artifact_id, artifact_type}}` SSE event
- Added data_product_id UUID to discovery context so agent can use it in tool calls

### Step 2: ArtifactCard Component

- **File:** `frontend/src/components/chat/ArtifactCard.tsx` (new)
- Clickable card with gold left border (#D4A843), MUI ButtonBase
- Type-to-icon mapping: erd → Schema, data_quality → Assessment, yaml → Code, brd → Description, data_preview → TableChart

### Step 3: Chat Store Panel State

- **File:** `frontend/src/stores/chatStore.ts`
- Added `activePanel: ArtifactType | null` and `setActivePanel` action

### Step 4: Detail Fetching Hooks

- **File:** `frontend/src/hooks/useArtifacts.ts`
- Added `useERDData`, `useQualityReport`, `useYAMLContent` React Query hooks
- Each hook fetches on-demand when its panel opens

### Step 5: Workspace Page Wiring

- **File:** `frontend/src/app/data-products/[id]/page.tsx`
- All 6 panel components imported and rendered (ERDDiagramPanel, DataQualityReport, DataQualityModal, YAMLViewer, DataPreview, ArtifactsPanel)
- Artifacts button wrapped in Badge showing count
- Auto-shows DataQualityModal when data_quality artifact appears after streaming
- Artifact hydration from PostgreSQL on page mount via useArtifacts hook

### Step 6: Inline Artifact Cards in MessageThread

- **File:** `frontend/src/components/chat/MessageThread.tsx`
- Added `onOpenArtifact` prop
- AgentMessage renders ArtifactCards matched by timestamp (60s window)

### Persistence Architecture Fix

- **`ai-service/tools/minio_tools.py`:** `upload_artifact` now writes to both MinIO AND PostgreSQL `artifacts` table
- **`ai-service/tools/postgres_tools.py`:** New `save_quality_report` tool for `data_quality_checks` table
- **`ai-service/agents/prompts.py`:** Discovery prompt strengthened with MANDATORY ARTIFACT PERSISTENCE section
- **`ai-service/agents/discovery.py` + `orchestrator.py`:** Registered `save_quality_report` in tool lists
- **PostgreSQL:** Added `quality_report` to `artifact_type` enum

## Verification Status (Updated 2026-02-06)

All items verified with Vertex AI (Gemini) provider and DMTDEMO.BRONZE data:

1. ~~Agent calling `save_quality_report` with correct UUID~~ — **Done.** Pipeline saves directly (not LLM-dependent)
2. ~~Agent calling `upload_artifact` for ERD and quality_report~~ — **Done.** Pipeline handles persistence
3. ~~Artifact SSE events appearing in frontend~~ — **Done.** Artifact cards appear after discovery
4. ~~ArtifactCards appearing inline in chat~~ — **Done.** ERD Diagram + Data Quality Report cards visible
5. ~~Artifacts button badge showing count~~ — **Done.** Badge shows "2" (ERD + Quality) after discovery, "4" after re-run
6. ~~DataQualityModal auto-showing after discovery~~ — **Partially.** Modal component exists, needs threshold gating verification
7. ~~Detail panels (ERD, Quality Report, YAML) opening with real data~~ — **Done.** ERD panel fully overhauled (see `2026-02-06-erd-discovery-quality-upgrades.md`)

## Architecture Change: Pipeline-Driven Persistence

The original design relied on the LLM agent calling `upload_artifact` and `save_quality_report` tools. This was unreliable — the LLM sometimes forgot to call tools, used wrong UUIDs, or skipped persistence entirely.

**New approach:** The discovery pipeline (`services/discovery_pipeline.py`) handles ALL persistence directly:
- Step 5 (ERD): Writes to Neo4j
- Step 6 (Quality): Computes health score via `compute_health_score()`
- Step 7 (Artifacts): Saves quality report to PostgreSQL + uploads to MinIO

The LLM agent only receives a text summary and generates a conversational response. No tool calls needed for persistence.

## Resolved Issues

- **First test:** Agent never called `upload_artifact` → **Resolved** by pipeline-driven persistence
- **Second test:** Agent used product name instead of UUID → **Resolved** by pipeline-driven persistence
- **Cortex costs blocking testing** → **Resolved** by switching to Vertex AI (Gemini)

## Files Modified

| File | Status |
|------|--------|
| `ai-service/routers/agent.py` | Modified |
| `ai-service/tools/minio_tools.py` | Modified |
| `ai-service/tools/postgres_tools.py` | Modified |
| `ai-service/agents/prompts.py` | Modified |
| `ai-service/agents/discovery.py` | Modified |
| `ai-service/agents/orchestrator.py` | Modified |
| `frontend/src/components/chat/ArtifactCard.tsx` | New |
| `frontend/src/components/chat/MessageThread.tsx` | Modified |
| `frontend/src/stores/chatStore.ts` | Modified |
| `frontend/src/hooks/useArtifacts.ts` | Modified |
| `frontend/src/app/data-products/[id]/page.tsx` | Modified |

## Next Steps

- ~~Switch LLM provider to a cheaper option~~ → Done (Vertex AI / Gemini)
- ~~Re-run end-to-end verification~~ → Done (2026-02-06)
- ~~Confirm all 7 verification items~~ → Done (see above)
- ~~ERD panel UX overhaul~~ → Done (see `2026-02-06-erd-discovery-quality-upgrades.md`)
- ~~Discovery pipeline accuracy fixes~~ → Done (see `2026-02-06-erd-discovery-quality-upgrades.md`)
- ~~BRD Viewer panel~~ → Done (2026-02-07, see `2026-02-07-implementation-progress.md`)
- ~~Resizable artifact drawers~~ → Done (2026-02-07)
- Verify DataQualityModal threshold gating behavior (70+/40-69/0-39 per PRD section 7.2-7.3)
