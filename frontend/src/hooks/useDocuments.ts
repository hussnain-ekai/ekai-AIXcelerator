import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

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
      formData.append('file', file);
      formData.append('data_product_id', dataProductId);
      return api.postForm<UploadDocumentResponse>('/documents/upload', formData);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['documents', dataProductId] });
    },
  });
}

export { useDocuments, useUploadDocument };
export type { UploadedDocument, DocumentsResponse, UploadDocumentResponse };
