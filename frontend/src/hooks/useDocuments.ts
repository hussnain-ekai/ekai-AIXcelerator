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

interface DeleteDocumentResponse {
  status: 'deleted';
  document_id: string;
  impacted_steps: string[];
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
    },
  });
}

export { useDocuments, useUploadDocument };
export {
  useDocumentContext,
  useApplyDocumentContext,
  useDeleteDocument,
  useReextractDocument,
};
export type {
  UploadedDocument,
  DocumentsResponse,
  UploadDocumentResponse,
  MissionStep,
  ContextSelectionState,
  DocumentContextResponse,
  StepContextState,
  DocumentContextItem,
  ReextractDocumentResponse,
};
