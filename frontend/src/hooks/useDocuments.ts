import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

type MissionStep =
  | 'discovery'
  | 'requirements'
  | 'modeling'
  | 'generation'
  | 'validation'
  | 'publishing';

type ContextSelectionState = 'candidate' | 'active' | 'reference' | 'excluded';

interface UploadedDocument {
  id: string;
  data_product_id: string;
  filename: string;
  minio_path: string;
  file_size_bytes: number | null;
  content_type: string | null;
  extraction_status: string;
  extraction_error: string | null;
  uploaded_by: string;
  created_at: string;
  extracted_at: string | null;
  source_channel?: string;
  user_note?: string | null;
  doc_kind?: string | null;
  summary?: string | null;
  is_deleted?: boolean;
  deleted_at?: string | null;
  deleted_by?: string | null;
  context_version_id?: string | null;
}

interface DocumentsResponse {
  data: UploadedDocument[];
}

interface UploadDocumentResponse {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  created_at: string;
}

interface DocumentContextItem {
  evidence_id: string;
  evidence_type: string;
  payload: Record<string, unknown>;
  step_candidates: string[];
  impact_scope: string[];
  document: {
    id: string;
    filename: string;
    doc_kind: string | null;
    summary: string | null;
    source_channel: string;
    created_at: string;
  };
  updated_at: string;
}

interface StepContextState {
  active: DocumentContextItem[];
  candidate: DocumentContextItem[];
  reference: DocumentContextItem[];
  excluded: DocumentContextItem[];
}

interface ContextVersion {
  id: string;
  version: number;
}

interface StaleArtifact {
  artifact_type: string;
  artifact_label: string;
  impacted_steps: MissionStep[];
  snapshot_context_version: number | null;
  latest_context_version: number | null;
  reason: string;
}

interface DocumentContextResponse {
  data_product_id: string;
  current_step: MissionStep;
  requested_step?: MissionStep;
  context_version: ContextVersion | null;
  step?: Partial<Record<MissionStep, StepContextState>>;
  steps?: Partial<Record<MissionStep, StepContextState>>;
}

interface ApplyContextPayload {
  step: MissionStep;
  reason?: string;
  updates: Array<{
    evidence_id: string;
    state: ContextSelectionState;
  }>;
}

interface ApplyContextResponse {
  data_product_id: string;
  step: MissionStep;
  applied: number;
  context_version: ContextVersion | null;
}

interface ContextDeltaChange {
  id: string;
  version: number;
  reason: string;
  changed_by: string;
  change_summary: Record<string, unknown>;
  created_at: string;
}

interface ContextDeltaResponse {
  data_product_id: string;
  from_version: number | null;
  to_version: number | null;
  changes: ContextDeltaChange[];
  impacted_steps: MissionStep[];
  stale_artifacts: StaleArtifact[];
  recommended_actions: string[];
  note?: string;
}

interface DeleteDocumentResponse {
  status: 'deleted';
  document_id: string;
  impacted_steps: string[];
  stale_artifacts?: StaleArtifact[];
  context_version: ContextVersion | null;
  recommended_actions: string[];
}

interface ReextractDocumentResponse {
  status: 'completed' | 'pending' | 'failed';
  message?: string;
  extracted_chars?: number;
  extraction_method?: string;
  warnings?: string[];
}

interface SemanticRegistryRow {
  registry_id: string | null;
  document_id: string;
  source_system: string;
  source_uri: string | null;
  title: string;
  mime_type: string | null;
  checksum_sha256: string | null;
  version_id: number;
  uploaded_by: string;
  uploaded_at: string;
  deleted_at: string | null;
  extraction_status: string;
  extraction_method: string | null;
  parse_quality_score: number | null;
  extraction_diagnostics: Record<string, unknown>;
  metadata: Record<string, unknown>;
  updated_at: string;
  filename?: string;
  source_channel?: string | null;
  doc_kind?: string | null;
  summary?: string | null;
  context_version_id?: string | null;
}

interface SemanticRegistryResponse {
  data_product_id: string;
  data: SemanticRegistryRow[];
  fallback?: boolean;
  note?: string;
}

interface SemanticChunkRow {
  id: string;
  document_id: string;
  chunk_seq: number;
  section_path: string | null;
  page_no: number | null;
  chunk_text: string;
  parser_version: string | null;
  extraction_confidence: number | null;
  created_at: string;
  filename?: string | null;
}

interface SemanticChunksResponse {
  data_product_id: string;
  limit: number;
  offset: number;
  data: SemanticChunkRow[];
  note?: string;
}

interface SemanticFactLink {
  target_domain: string;
  target_key: string;
  link_reason: string | null;
  link_confidence: number | null;
}

interface SemanticFactRow {
  id: string;
  document_id: string;
  fact_type: string;
  subject_key: string | null;
  predicate: string | null;
  object_value: string | null;
  object_unit: string | null;
  numeric_value: number | null;
  event_time: string | null;
  currency: string | null;
  confidence: number | null;
  source_page: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
  filename?: string | null;
  links: SemanticFactLink[];
}

interface SemanticFactsResponse {
  data_product_id: string;
  limit: number;
  offset: number;
  data: SemanticFactRow[];
  note?: string;
}

interface SemanticEvidenceRow {
  id: string;
  query_id: string;
  answer_id: string | null;
  source_mode: string;
  confidence: string;
  exactness_state: string;
  tool_calls: unknown[];
  sql_refs: unknown[];
  fact_refs: unknown[];
  chunk_refs: unknown[];
  conflicts: unknown[];
  recovery_plan: Record<string, unknown>;
  final_decision: string;
  created_by: string;
  created_at: string;
}

interface SemanticEvidenceResponse {
  data_product_id: string;
  limit: number;
  offset: number;
  data: SemanticEvidenceRow[];
  note?: string;
}

function useDocuments(dataProductId: string | null) {
  return useQuery<DocumentsResponse>({
    queryKey: ['documents', dataProductId],
    queryFn: () => api.get<DocumentsResponse>(`/documents/${dataProductId}`),
    enabled: !!dataProductId,
  });
}

function useUploadDocument(dataProductId: string) {
  const queryClient = useQueryClient();

  return useMutation<UploadDocumentResponse, Error, File>({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append('data_product_id', dataProductId);
      formData.append('source_channel', 'documents_panel');
      // Keep file part last so multipart field metadata is always parsed first.
      formData.append('file', file);
      return api.postForm<UploadDocumentResponse>('/documents/upload', formData);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['documents', dataProductId] });
      void queryClient.invalidateQueries({ queryKey: ['documents-semantic-registry', dataProductId] });
      void queryClient.invalidateQueries({ queryKey: ['document-context', dataProductId] });
      void queryClient.invalidateQueries({ queryKey: ['document-context-delta', dataProductId] });
    },
  });
}

function useDocumentContext(
  dataProductId: string | null,
  step?: MissionStep,
) {
  return useQuery<DocumentContextResponse>({
    queryKey: ['document-context', dataProductId, step ?? 'all'],
    queryFn: () =>
      api.get<DocumentContextResponse>(
        step
          ? `/documents/context/${dataProductId}/current?step=${step}`
          : `/documents/context/${dataProductId}/current`,
      ),
    enabled: !!dataProductId,
  });
}

function useDocumentContextDelta(
  dataProductId: string | null,
  options?: { fromVersion?: number; toVersion?: number },
) {
  return useQuery<ContextDeltaResponse>({
    queryKey: [
      'document-context-delta',
      dataProductId,
      options?.fromVersion ?? null,
      options?.toVersion ?? null,
    ],
    queryFn: () => {
      const query = new URLSearchParams();
      if (options?.fromVersion !== undefined) query.set('from_version', String(options.fromVersion));
      if (options?.toVersion !== undefined) query.set('to_version', String(options.toVersion));
      const qs = query.toString();
      return api.get<ContextDeltaResponse>(
        `/documents/context/${dataProductId}/delta${qs ? `?${qs}` : ''}`,
      );
    },
    enabled: !!dataProductId,
  });
}

function useSemanticRegistry(dataProductId: string | null, includeDeleted = false) {
  return useQuery<SemanticRegistryResponse>({
    queryKey: ['documents-semantic-registry', dataProductId, includeDeleted],
    queryFn: () =>
      api.get<SemanticRegistryResponse>(
        `/documents/semantic/${dataProductId}/registry?include_deleted=${includeDeleted ? 'true' : 'false'}`,
      ),
    enabled: !!dataProductId,
  });
}

function useSemanticChunks(
  dataProductId: string | null,
  options?: { documentId?: string; limit?: number; offset?: number },
) {
  return useQuery<SemanticChunksResponse>({
    queryKey: [
      'documents-semantic-chunks',
      dataProductId,
      options?.documentId ?? null,
      options?.limit ?? 100,
      options?.offset ?? 0,
    ],
    queryFn: () => {
      const query = new URLSearchParams();
      if (options?.documentId) query.set('document_id', options.documentId);
      if (options?.limit !== undefined) query.set('limit', String(options.limit));
      if (options?.offset !== undefined) query.set('offset', String(options.offset));
      const qs = query.toString();
      return api.get<SemanticChunksResponse>(
        `/documents/semantic/${dataProductId}/chunks${qs ? `?${qs}` : ''}`,
      );
    },
    enabled: !!dataProductId,
  });
}

function useSemanticFacts(
  dataProductId: string | null,
  options?: { factType?: string; documentId?: string; limit?: number; offset?: number },
) {
  return useQuery<SemanticFactsResponse>({
    queryKey: [
      'documents-semantic-facts',
      dataProductId,
      options?.factType ?? null,
      options?.documentId ?? null,
      options?.limit ?? 100,
      options?.offset ?? 0,
    ],
    queryFn: () => {
      const query = new URLSearchParams();
      if (options?.factType) query.set('fact_type', options.factType);
      if (options?.documentId) query.set('document_id', options.documentId);
      if (options?.limit !== undefined) query.set('limit', String(options.limit));
      if (options?.offset !== undefined) query.set('offset', String(options.offset));
      const qs = query.toString();
      return api.get<SemanticFactsResponse>(
        `/documents/semantic/${dataProductId}/facts${qs ? `?${qs}` : ''}`,
      );
    },
    enabled: !!dataProductId,
  });
}

function useSemanticEvidence(
  dataProductId: string | null,
  options?: { queryId?: string; limit?: number; offset?: number },
) {
  return useQuery<SemanticEvidenceResponse>({
    queryKey: [
      'documents-semantic-evidence',
      dataProductId,
      options?.queryId ?? null,
      options?.limit ?? 100,
      options?.offset ?? 0,
    ],
    queryFn: () => {
      const query = new URLSearchParams();
      if (options?.queryId) query.set('query_id', options.queryId);
      if (options?.limit !== undefined) query.set('limit', String(options.limit));
      if (options?.offset !== undefined) query.set('offset', String(options.offset));
      const qs = query.toString();
      return api.get<SemanticEvidenceResponse>(
        `/documents/semantic/${dataProductId}/evidence${qs ? `?${qs}` : ''}`,
      );
    },
    enabled: !!dataProductId,
  });
}

function useApplyDocumentContext(dataProductId: string) {
  const queryClient = useQueryClient();

  return useMutation<ApplyContextResponse, Error, ApplyContextPayload>({
    mutationFn: (payload) =>
      api.post<ApplyContextResponse>(
        `/documents/context/${dataProductId}/apply`,
        payload,
      ),
    onSuccess: (_result, payload) => {
      void queryClient.invalidateQueries({ queryKey: ['documents', dataProductId] });
      void queryClient.invalidateQueries({
        queryKey: ['document-context', dataProductId, payload.step],
      });
      void queryClient.invalidateQueries({
        queryKey: ['document-context', dataProductId, 'all'],
      });
      void queryClient.invalidateQueries({
        queryKey: ['document-context-delta', dataProductId],
      });
    },
  });
}

function useDeleteDocument(dataProductId: string) {
  const queryClient = useQueryClient();

  return useMutation<DeleteDocumentResponse, Error, string>({
    mutationFn: (documentId) => api.del<DeleteDocumentResponse>(`/documents/${documentId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['documents', dataProductId] });
      void queryClient.invalidateQueries({
        queryKey: ['document-context', dataProductId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['document-context-delta', dataProductId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['documents-semantic-registry', dataProductId],
      });
    },
  });
}

function useReextractDocument(dataProductId: string) {
  const queryClient = useQueryClient();

  return useMutation<ReextractDocumentResponse, Error, string>({
    mutationFn: (documentId) =>
      api.post<ReextractDocumentResponse>(`/documents/${documentId}/extract`, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['documents', dataProductId] });
      void queryClient.invalidateQueries({
        queryKey: ['document-context', dataProductId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['document-context-delta', dataProductId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['documents-semantic-registry', dataProductId],
      });
    },
  });
}

interface DocumentStatusResponse {
  document_id: string;
  filename: string;
  extraction_status: string;
  extraction_error: string | null;
  extracted_at: string | null;
}

function useDocumentStatus(
  dataProductId: string | null,
  documentId: string | null,
  enabled = false,
) {
  return useQuery<DocumentStatusResponse>({
    queryKey: ['document-status', dataProductId, documentId],
    queryFn: () =>
      api.get<DocumentStatusResponse>(
        `/documents/${dataProductId}/${documentId}/status`,
      ),
    enabled: !!dataProductId && !!documentId && enabled,
    refetchInterval: 3000,
  });
}

/**
 * Poll extraction status for a document after upload.
 * Automatically stops polling when status is 'completed' or 'failed',
 * and invalidates the documents list.
 */
function useDocumentExtractionPoller(
  dataProductId: string | null,
  documentId: string | null,
  extractionStatus: string | null | undefined,
) {
  const queryClient = useQueryClient();
  const shouldPoll =
    !!dataProductId &&
    !!documentId &&
    (extractionStatus === 'pending' || extractionStatus === 'processing');

  const statusQuery = useDocumentStatus(dataProductId, documentId, shouldPoll);

  const prevStatus = useRef(extractionStatus);
  useEffect(() => {
    const currentStatus = statusQuery.data?.extraction_status;
    if (
      currentStatus &&
      currentStatus !== prevStatus.current &&
      (currentStatus === 'completed' || currentStatus === 'failed')
    ) {
      void queryClient.invalidateQueries({ queryKey: ['documents', dataProductId] });
      void queryClient.invalidateQueries({
        queryKey: ['documents-semantic-registry', dataProductId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['document-context', dataProductId],
      });
    }
    prevStatus.current = currentStatus ?? extractionStatus;
  }, [statusQuery.data, dataProductId, extractionStatus, queryClient]);

  return statusQuery;
}

export { useDocuments, useUploadDocument };
export {
  useDocumentContext,
  useDocumentContextDelta,
  useApplyDocumentContext,
  useDeleteDocument,
  useReextractDocument,
  useDocumentStatus,
  useDocumentExtractionPoller,
  useSemanticRegistry,
  useSemanticChunks,
  useSemanticFacts,
  useSemanticEvidence,
};
export type {
  StaleArtifact,
  UploadedDocument,
  DocumentsResponse,
  UploadDocumentResponse,
  DocumentStatusResponse,
  MissionStep,
  ContextSelectionState,
  DocumentContextResponse,
  StepContextState,
  DocumentContextItem,
  ContextDeltaResponse,
  ContextDeltaChange,
  ReextractDocumentResponse,
  SemanticRegistryResponse,
  SemanticRegistryRow,
  SemanticChunksResponse,
  SemanticChunkRow,
  SemanticFactsResponse,
  SemanticFactRow,
  SemanticEvidenceResponse,
  SemanticEvidenceRow,
};
