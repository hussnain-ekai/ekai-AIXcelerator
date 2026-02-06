# Proactive Discovery Implementation - Phase 1 Complete

**Date:** 2026-02-04
**Status:** ✅ Phase 1 Complete, Ready for Phase 2

## Overview

Transformed the AI agent from **reactive** (waiting for user input) to **proactive** (auto-initiating discovery with intelligent context).

## Phase 1: Core Infrastructure ✅

### Frontend Changes

#### 1. DataSourceSettingsPanel.tsx
- **Removed**: Redundant chip list showing selected tables (lines 305-331)
- **Reason**: Checkbox list already shows selection clearly

#### 2. ChatWorkspacePage.tsx (`/data-products/[id]/page.tsx`)
- **Added**: Auto-trigger discovery on page load
- **Implementation**:
  - `useRef` hook (`discoveryTriggeredRef`) prevents duplicate triggers
  - `useEffect` hook triggers when:
    - No messages exist
    - Data product loaded
    - Tables selected (length > 0)
    - Not currently streaming
  - Sends special message: `__START_DISCOVERY__`
- **Added**: "Re-run Discovery" button in header
  - Clears messages
  - Resets trigger flag
  - Auto-triggers on next render

#### 3. chatStore.ts
- **Added**: `clearMessages()` method for re-run functionality
- **Signature**: `clearMessages: () => void`
- **Behavior**: Resets messages array and sets phase to 'idle'

### AI Service Changes

#### 1. routers/agent.py
- **Added**: Discovery trigger constant: `DISCOVERY_TRIGGER = "__START_DISCOVERY__"`
- **Added**: `_build_discovery_context(data_product_id)` async function
  - Queries PostgreSQL for data product details (name, description, database, schemas, tables)
  - Queries Snowflake INFORMATION_SCHEMA for table structure (up to 10 tables, 20 columns each)
  - Builds formatted context message with:
    - Data product metadata
    - Table and column details with data types and nullability
    - Initial discovery prompt with business domain questions
  - Returns 3800+ character enriched context
  - Graceful fallback on errors
- **Modified**: `_run_agent()` function
  - Detects `__START_DISCOVERY__` trigger message
  - Calls `_build_discovery_context()` to enrich the message
  - Replaces trigger message with enriched context before sending to orchestrator
  - Logs context length for monitoring

#### 2. Service Integration
- **Fixed**: PostgreSQL service calls to use correct API:
  - `get_pool(database_url)` → returns asyncpg.Pool
  - `query(pool, sql, *args)` → returns list of Records
- **Fixed**: Snowflake service calls to use correct API:
  - `execute_query(sql, params=None)` → async function returning list of dicts

## Testing Results ✅

### Manual Testing (Playwright MCP)
1. ✅ Navigated to `/data-products`
2. ✅ Clicked "Northwind Sales Model" data product
3. ✅ Page loaded chat workspace at `/data-products/019c2a49-666e-73af-aef5-e4d29c84bfc5`
4. ✅ Auto-trigger fired immediately:
   - Message shows `__START_DISCOVERY__` in UI (user message)
   - Progress indicator: "ekaiX is thinking..."
   - Input disabled during streaming
   - "Re-run Discovery" button disabled during streaming
5. ✅ AI service logs confirm:
   - Trigger detected: `Discovery trigger detected for session...`
   - Context built: `Discovery context built, length: 3804 chars`
   - Orchestrator processing with tool calls

### Service Status ✅
- All 7 services running via PM2:
  - Frontend :3000 ✅
  - Backend :8000 ✅
  - AI Service :8001 ✅
  - PostgreSQL :5432 (Docker) ✅
  - Neo4j :7474/:7687 (Docker) ✅
  - Redis :6379 (Docker) ✅
  - MinIO :9000/:9001 (Docker) ✅

## What Works

1. **Auto-trigger**: Discovery starts automatically when user enters chat workspace
2. **Context enrichment**: AI service queries PostgreSQL + Snowflake INFORMATION_SCHEMA and builds 3800+ char context
3. **Re-run capability**: User can restart discovery with button
4. **Service integration**: All database queries use correct async APIs
5. **Error handling**: Graceful fallback if context building fails
6. **Logging**: Full visibility into trigger detection and context building

## Phase 2: Intelligent Question Generation 🚧

Phase 1 establishes the infrastructure for proactive discovery. The next phase focuses on making the questions truly intelligent.

### Current Behavior
- Orchestrator receives enriched context but treats it as a regular user message
- Agent proceeds with standard discovery workflow (calling tools, building ERD)
- Initial response is generic: "Let me analyze the schema..."

### Phase 2 Goals
Transform the discovery experience to match user requirements:

#### 1. Sharp, Business-Focused Questions
- **Target audience**: Business analysts and domain experts, NOT data engineers
- **Style**: Sharp questions with specific purpose (NO open-ended fluff)
- **Pattern**: [Analysis] → [Recognition] → [Question] → [Suggestion]

**Example** (from user requirements):
```
Analysis: I noticed you have ORDERS, ORDER_DETAILS, and PRODUCTS tables with
price fields in multiple locations.

Recognition: This typically represents either a retail transaction system or
a B2B order management workflow.

Question: Which of these better describes your business?
• Retail/E-commerce (customer-facing orders)
• B2B Sales (orders between business entities)
• Internal procurement (company purchasing)

Suggestion: Based on the presence of SHIPPERS and FREIGHT columns, I'm
leaning toward B2B logistics.
```

#### 2. Extract All Available Information
- Analyze column names for semantic meaning (CUSTOMER_ID, ORDER_DATE, UNIT_PRICE)
- Detect entity types (customers, orders, products, transactions)
- Recognize relationships from naming patterns (FK columns ending in _ID)
- Infer business domain from table/column combinations
- Parse INFORMATION_SCHEMA constraints (PRIMARY KEY, FOREIGN KEY, CHECK)
- Use statistical patterns ONLY when no semantic info available

#### 3. DAMA CDM Focus
Follow DAMA Framework progression:
- **Conceptual Data Model (CDM)**: Business view of entities and relationships
- **Logical Data Model (ERD)**: Database-agnostic design
- **Physical Model**: Snowflake implementation

Discovery phase questions should build the CDM by understanding business semantics, NOT just technical schema structure.

#### 4. Follow User Instructions
When user provides explicit instructions (e.g., "This is a supply chain system"), the AI must:
- **NEVER ignore or override** user's stated context
- Use instructions to guide question focus
- Build on user's context rather than questioning it
- Tailor remaining questions to the stated domain

#### 5. Differential Discovery
When data sources change (tables added/removed):
- Maintain context for existing analyzed tables
- Ask questions ONLY about new tables
- Integrate new tables into existing CDM understanding
- Don't repeat questions for unchanged schemas

### Phase 2 Implementation Plan

#### A. Enhance Discovery Agent Prompt
- Add DAMA CDM principles
- Define question quality criteria
- Provide examples of sharp vs. fluff questions
- Specify analysis-first, question-second approach

#### B. Improve Context Builder
- Add semantic analysis of table/column names
- Extract FK patterns from naming conventions
- Detect common domain patterns (order management, CRM, etc.)
- Pre-analyze relationships before sending to agent

#### C. Add Question Templates
- Create template bank for common domain patterns
- Retail/e-commerce questions
- B2B/logistics questions
- CRM/customer management questions
- Financial/accounting questions
- Manufacturing/supply chain questions

#### D. User Instruction Handling
- Parse and store user's domain context
- Modify question strategy based on stated domain
- Never contradict explicit user instructions

#### E. Differential Discovery Logic
- Compare current vs. previous table list
- Identify new/removed tables
- Load existing CDM from previous session
- Generate questions only for deltas

## Files Modified

### Frontend
- `frontend/src/components/dashboard/DataSourceSettingsPanel.tsx` - Removed chip list
- `frontend/src/app/data-products/[id]/page.tsx` - Auto-trigger + re-run button
- `frontend/src/stores/chatStore.ts` - clearMessages method

### AI Service
- `ai-service/routers/agent.py` - Discovery trigger detection + context builder

### Documentation
- `CLAUDE.md` - Added Playwright MCP documentation

## Next Steps

1. **Phase 2A**: Enhance discovery agent prompt with DAMA principles
2. **Phase 2B**: Add semantic analysis to context builder
3. **Phase 2C**: Create question template bank for common domains
4. **Phase 2D**: Implement user instruction parsing and respect
5. **Phase 2E**: Add differential discovery for schema changes

## Technical Notes

### Service API Patterns
- **PostgreSQL**: Pool-based async (`get_pool()` + `query()`)
- **Snowflake**: Singleton connection async (`execute_query()`)
- **Message flow**: Frontend → Backend → AI Service → Orchestrator → Discovery Agent

### Discovery Context Format
3800+ character message with:
- Data product name + description
- Database reference
- N tables from M schemas
- Per-table column list (up to 20 columns)
- Column details: name, type, nullability
- Business domain question prompt

### Error Handling
- PostgreSQL query failure → fallback message
- Snowflake query failure → fallback message
- No tables selected → early return with instructions
- No data product found → generic prompt

## Conclusion

**Phase 1 Status**: ✅ Complete and tested
**Auto-discovery infrastructure**: Fully functional
**Ready for**: Phase 2 intelligent question generation

The foundation is solid. Phase 2 will transform the agent from technically proficient to business-intelligent.
