# LookML / Looker Output Support Plan

## Goal
Add Looker as an alternative publish target alongside Snowflake Intelligence. Same data source (Snowflake), same agents, different output format.

## Architecture

```
User → ekaiX Agents → Discovery (Snowflake, unchanged)
                     → Requirements (unchanged)
                     → Generation → Snowflake YAML **OR** LookML
                     → Validation → Snowflake verify **OR** Looker API validation
                     → Publishing → Snowflake DDL **OR** Looker Git/API push
```

## Configuration
- Data product setting: `publish_target: "snowflake_intelligence" | "looker"`
- Looker connection config: instance URL, API client ID/secret, Looker project name
- Stored in `data_products.settings` or workspace-level config

## What Changes

| Component | Change |
|---|---|
| **Generation agent** | New LookML template set (views, explores, models). Parallel to existing YAML templates |
| **Generation prompt** | Conditional: if publish_target=looker, output LookML structure instead of YAML JSON |
| **Validation agent** | Add `validate_lookml` tool using Looker API `lookml_validation` endpoint |
| **Publishing agent** | Add `publish_to_looker` tool — push LookML files via Looker API or Git integration |
| **Frontend** | LookML viewer (syntax-highlighted `.lkml` files) in artifact panel |
| **Config UI** | Looker connection settings in workspace or data product config |

## What Stays the Same
- Discovery pipeline (still queries Snowflake)
- Requirements agent / BRD capture
- ERD, Data Quality, Data Description artifacts
- Orchestrator flow (just different subagent tools based on target)
- Transformation agent (if used)
- Modeling agent

## LookML Output Structure
```
views/
  iot_readings_data.view.lkml    # dimensions, measures per table
  maintenance_events.view.lkml
  water_sensors_master.view.lkml
models/
  water_treatment.model.lkml     # connection, includes, explores
explores/
  sensor_readings.explore.lkml   # joins, access filters
```

## New Tools Needed
1. `generate_lookml` — converts BRD + metadata into LookML files (or reuse generation agent with LookML templates)
2. `validate_lookml` — calls Looker API `POST /lookml_validation`
3. `publish_lookml` — pushes to Looker project via API (`POST /projects/{id}/deploy`)
4. `save_lookml` — stores LookML in PostgreSQL (like save_semantic_view but for .lkml content)

## Looker API Endpoints Used
- `POST /lookml_validation` — validate LookML syntax
- `GET /connections` — verify Snowflake connection exists
- `POST /projects/{id}/git/deploy` — deploy LookML to production
- `GET /lookml_models` — list existing models (avoid conflicts)

## Implementation Order
1. LookML templates + generation logic (biggest piece)
2. save/retrieve LookML in PostgreSQL
3. Frontend LookML viewer
4. Looker API integration (validate + publish)
5. Config UI for Looker connection
6. E2E test: same Snowflake data → LookML output → Looker validation

## Estimated Effort
~5-7 days after Snowflake path is production-ready

## Dependencies
- Looker instance for testing (Google Cloud trial or customer sandbox)
- Looker API credentials (client ID + secret)
- Snowflake connection already configured in Looker
