# Redis Key Patterns

All Redis keys used by ekaiX, their purposes, TTLs, and monitoring guidance.

## Key Patterns

| Pattern | Example | Purpose | TTL | Service |
|---------|---------|---------|-----|---------|
| `agent:session:{session_id}` | `agent:session:abc-123` | Active agent session state (current phase, tool history) | `SESSION_TTL_SECONDS` (default 3600s) | AI Service |
| `checkpoint:{session_id}:{checkpoint_id}` | `checkpoint:abc-123:cp-456` | LangGraph conversation checkpoint for time-travel/rollback | `SESSION_TTL_SECONDS` (default 3600s) | AI Service |
| `interrupt:{session_id}` | `interrupt:abc-123` | Interrupt flag to stop a running agent session | `SESSION_TTL_SECONDS` (default 3600s) | AI Service |
| `discovery:pipeline:{data_product_id}` | `discovery:pipeline:dp-789` | Cached discovery pipeline results (schema, profiles, ERD) | 86400s (24h) | AI Service |
| `cache:working_layer:{data_product_id}` | `cache:working_layer:dp-789` | Cached working layer table list after transformation | `CACHE_TTL_SECONDS` (default 3600s) | AI Service |
| `cache:erd:{data_product_id}` | `cache:erd:dp-789` | Cached ERD graph data for frontend visualization | `CACHE_TTL_SECONDS` (default 3600s) | AI Service |
| `cache:profile:{table_fqn}` | `cache:profile:DB.SCHEMA.TABLE` | Cached table profile statistics | `CACHE_TTL_SECONDS` (default 3600s) | AI Service |
| `cache:{arbitrary_key}` | `cache:custom_key` | Generic cache via `StateBackend.cache_set()` | `CACHE_TTL_SECONDS` (default 3600s) | AI Service |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_TTL_SECONDS` | 3600 | TTL for session, checkpoint, and interrupt keys |
| `CACHE_TTL_SECONDS` | 3600 | TTL for generic cache keys |

The discovery pipeline has a hardcoded TTL of 86400 seconds (24 hours) for its results cache.

## Data Format

All keys store JSON-serialized values via `redis_service.set_json()` / `redis_service.get_json()`. The serialization uses Python's `json.dumps()` / `json.loads()`.

## Monitoring Tips

- **Key count by pattern:** `redis-cli --scan --pattern "agent:session:*" | wc -l`
- **Memory per key pattern:** `redis-cli --scan --pattern "discovery:pipeline:*" | xargs -I{} redis-cli memory usage {}`
- **Check for stale sessions:** `redis-cli --scan --pattern "agent:session:*" | while read key; do echo "$key TTL=$(redis-cli ttl $key)"; done`
- **Flush discovery cache for a product:** `redis-cli del "discovery:pipeline:<data_product_id>"`

## Workspace Isolation

Redis keys are scoped by `session_id` or `data_product_id`. There is no cross-workspace key collision because UUIDs are globally unique.

## Backend (Node.js)

The backend's `redisService.ts` provides basic client management (`getClient`, `healthCheck`, `close`) but does not currently store application keys. All application-level Redis usage is in the AI Service (Python).
