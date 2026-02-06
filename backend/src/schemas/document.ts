import { z } from 'zod';

// --- Upload metadata (used after multipart parsing) ---

export const uploadDocumentSchema = z.object({
  data_product_id: z.string().uuid(),
});

export type UploadDocumentInput = z.infer<typeof uploadDocumentSchema>;

// --- Document query params ---

export const listDocumentsParamSchema = z.object({
  dataProductId: z.string().uuid(),
});

export type ListDocumentsParam = z.infer<typeof listDocumentsParamSchema>;

// --- Document content param ---

export const documentContentParamSchema = z.object({
  id: z.string().uuid(),
});

export type DocumentContentParam = z.infer<typeof documentContentParamSchema>;

// --- Allowed MIME types ---

export const ALLOWED_MIME_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
  'text/plain',
  'text/csv',
] as const;

export const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
