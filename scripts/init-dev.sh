#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== ekaiX Dev Environment Init ==="

# 1. Start Docker Compose
echo ""
echo "[1/5] Starting data services..."
cd "$PROJECT_ROOT"
docker-compose up -d

# 2. Wait for healthchecks
echo ""
echo "[2/5] Waiting for services to be healthy..."

wait_for_service() {
  local service=$1
  local max_attempts=30
  local attempt=0
  while [ $attempt -lt $max_attempts ]; do
    if docker-compose ps "$service" | grep -q "healthy"; then
      echo "  ✓ $service is healthy"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  echo "  ✗ $service failed to become healthy after ${max_attempts} attempts"
  return 1
}

wait_for_service postgresql
wait_for_service neo4j
wait_for_service redis
wait_for_service minio

# 3. PostgreSQL schema is auto-loaded via docker-entrypoint-initdb.d
echo ""
echo "[3/5] PostgreSQL schema loaded via init script (docker-entrypoint-initdb.d)"

# 4. Run Neo4j constraints
echo ""
echo "[4/5] Creating Neo4j constraints and indexes..."
NEO4J_PASSWORD="${NEO4J_PASSWORD:-ekaix_dev_password}"

docker exec ekaix-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" <<'CYPHER'
CREATE CONSTRAINT db_fqn IF NOT EXISTS FOR (d:Database) REQUIRE d.fqn IS UNIQUE;
CREATE CONSTRAINT schema_fqn IF NOT EXISTS FOR (s:Schema) REQUIRE s.fqn IS UNIQUE;
CREATE CONSTRAINT table_fqn IF NOT EXISTS FOR (t:Table) REQUIRE t.fqn IS UNIQUE;
CREATE CONSTRAINT column_fqn IF NOT EXISTS FOR (c:Column) REQUIRE c.fqn IS UNIQUE;
CREATE INDEX table_classification IF NOT EXISTS FOR (t:Table) ON (t.classification);
CREATE INDEX table_data_product IF NOT EXISTS FOR (t:Table) ON (t.data_product_id);
CYPHER
echo "  ✓ Neo4j constraints created"

# 5. Create MinIO buckets
echo ""
echo "[5/5] Creating MinIO buckets..."
MINIO_ROOT_USER="${MINIO_ROOT_USER:-ekaix}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-ekaix_dev_password}"

# Wait for MinIO API to be ready
sleep 2

docker exec ekaix-minio mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" 2>/dev/null || true

for bucket in artifacts documents workspace; do
  docker exec ekaix-minio mc mb "local/$bucket" --ignore-existing 2>/dev/null || true
  docker exec ekaix-minio mc versioning enable "local/$bucket" 2>/dev/null || true
  echo "  ✓ Bucket '$bucket' created with versioning"
done

echo ""
echo "=== ekaiX Dev Environment Ready ==="
echo ""
echo "Services:"
echo "  PostgreSQL: localhost:5432 (user: ekaix, db: ekaix)"
echo "  Neo4j:      localhost:7474 (browser) / localhost:7687 (bolt)"
echo "  Redis:      localhost:6379"
echo "  MinIO:      localhost:9000 (API) / localhost:9001 (console)"
echo ""
echo "Next steps:"
echo "  cd frontend && npm install"
echo "  cd backend && npm install"
echo "  cd ai-service && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
echo "  pm2 start pm2.config.js"
