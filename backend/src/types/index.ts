// --- User ---

export interface User {
  snowflakeUser: string;
  displayName: string;
  role: string;
  account: string;
}

// --- Data Product ---

export type DataProductStatus =
  | 'CREATED'
  | 'DISCOVERING'
  | 'DISCOVERED'
  | 'REQUIREMENTS'
  | 'GENERATING'
  | 'GENERATED'
  | 'VALIDATING'
  | 'VALIDATED'
  | 'PUBLISHING'
  | 'PUBLISHED'
  | 'ERROR';

export interface DataProduct {
  id: string;
  workspaceId: string;
  name: string;
  description: string;
  databaseReference: string;
  schemas: string[];
  status: DataProductStatus;
  state: Record<string, unknown>;
  healthScore: number | null;
  publishedAt: string | null;
  publishedAgentFqn: string | null;
  createdAt: string;
  updatedAt: string;
}

// --- Artifacts ---

export type ArtifactType =
  | 'ERD'
  | 'DATA_QUALITY_REPORT'
  | 'BRD'
  | 'YAML'
  | 'DATA_PREVIEW';

// --- Sharing ---

export type SharePermission = 'VIEW' | 'EDIT' | 'ADMIN';

// --- Pagination ---

export interface PaginationMeta {
  page: number;
  pageSize: number;
  totalItems: number;
  totalPages: number;
}

export interface PaginatedResponse<T> {
  data: T[];
  meta: PaginationMeta;
}

// --- Agent ---

export interface AgentMessage {
  id: string;
  sessionId: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  toolCalls: ToolCall[] | null;
  timestamp: string;
}

export interface ToolCall {
  name: string;
  description: string;
  status: 'pending' | 'running' | 'completed' | 'error';
}

export type AgentEventType =
  | 'message'
  | 'tool_start'
  | 'tool_end'
  | 'error'
  | 'done'
  | 'interrupt';

export interface AgentEvent {
  type: AgentEventType;
  data: Record<string, unknown>;
  timestamp: string;
}

// --- Fastify Extension ---

declare module 'fastify' {
  interface FastifyRequest {
    user: User;
  }
}
