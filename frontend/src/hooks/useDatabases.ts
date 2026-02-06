import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

interface DatabaseSummary {
  name: string;
  comment?: string;
  schemas_count: number;
  tables_count?: number;
  created_at?: string;
}

interface SchemaSummary {
  name: string;
  database: string;
  tables_count: number;
  comment?: string;
}

interface DatabasesResponse {
  databases: DatabaseSummary[];
}

interface TableSummary {
  name: string;
  schema: string;
  database: string;
  fqn: string;
  table_type: string;
  row_count: number;
  comment?: string;
}

interface SchemasResponse {
  schemas: SchemaSummary[];
}

interface TablesResponse {
  tables: TableSummary[];
}

function useDatabases() {
  return useQuery<DatabasesResponse>({
    queryKey: ['databases'],
    queryFn: () => api.get<DatabasesResponse>('/databases'),
  });
}

function useSchemas(dbName: string | null) {
  return useQuery<SchemasResponse>({
    queryKey: ['schemas', dbName],
    queryFn: () =>
      api.get<SchemasResponse>(`/databases/${dbName}/schemas`),
    enabled: dbName !== null && dbName.length > 0,
  });
}

function useTables(dbName: string | null, schemaName: string | null) {
  return useQuery<TablesResponse>({
    queryKey: ['tables', dbName, schemaName],
    queryFn: () =>
      api.get<TablesResponse>(
        `/databases/${dbName}/schemas/${schemaName}/tables`,
      ),
    enabled:
      dbName !== null &&
      dbName.length > 0 &&
      schemaName !== null &&
      schemaName.length > 0,
  });
}

export { useDatabases, useSchemas, useTables };
export type { DatabaseSummary, SchemaSummary, TableSummary };
