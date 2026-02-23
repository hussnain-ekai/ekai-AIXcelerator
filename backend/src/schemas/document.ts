import { z } from 'zod';

export const missionStepSchema = z.enum([
  'discovery',
  'requirements',
  'modeling',
  'generation',
  'validation',
  'publishing',
]);
export type MissionStep = z.infer<typeof missionStepSchema>;

export const documentSourceChannelSchema = z.enum([
  'create_flow',
  'chat_attachment',
  'documents_panel',
]);
export type DocumentSourceChannel = z.infer<typeof documentSourceChannelSchema>;

export const contextSelectionStateSchema = z.enum([
  'candidate',
  'active',
  'reference',
  'excluded',
]);
export type ContextSelectionState = z.infer<typeof contextSelectionStateSchema>;

// --- Upload metadata (used after multipart parsing) ---

export const uploadDocumentSchema = z.object({
  data_product_id: z.string().uuid(),
  source_channel: documentSourceChannelSchema.optional().default('documents_panel'),
  user_note: z.string().max(2000).optional(),
  auto_activate_step: missionStepSchema.optional(),
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

export const extractDocumentParamSchema = documentContentParamSchema;
export type ExtractDocumentParam = z.infer<typeof extractDocumentParamSchema>;

export const deleteDocumentParamSchema = documentContentParamSchema;
export type DeleteDocumentParam = z.infer<typeof deleteDocumentParamSchema>;

export const contextCurrentParamSchema = z.object({
  dataProductId: z.string().uuid(),
});
export type ContextCurrentParam = z.infer<typeof contextCurrentParamSchema>;

export const contextCurrentQuerySchema = z.object({
  step: missionStepSchema.optional(),
});
export type ContextCurrentQuery = z.infer<typeof contextCurrentQuerySchema>;

export const contextDeltaParamSchema = contextCurrentParamSchema;
export type ContextDeltaParam = z.infer<typeof contextDeltaParamSchema>;

export const contextDeltaQuerySchema = z.object({
  from_version: z.coerce.number().int().positive().optional(),
  to_version: z.coerce.number().int().positive().optional(),
});
export type ContextDeltaQuery = z.infer<typeof contextDeltaQuerySchema>;

export const applyContextParamSchema = contextCurrentParamSchema;
export type ApplyContextParam = z.infer<typeof applyContextParamSchema>;

export const applyContextSchema = z.object({
  step: missionStepSchema,
  reason: z.string().max(250).optional(),
  updates: z
    .array(
      z.object({
        evidence_id: z.string().uuid(),
        state: contextSelectionStateSchema,
      }),
    )
    .min(1),
});
export type ApplyContextInput = z.infer<typeof applyContextSchema>;

// --- Document semantic layer contracts ---

export const semanticRegistryParamSchema = z.object({
  dataProductId: z.string().uuid(),
});
export type SemanticRegistryParam = z.infer<typeof semanticRegistryParamSchema>;

export const semanticRegistryQuerySchema = z.object({
  include_deleted: z.coerce.boolean().optional().default(false),
});
export type SemanticRegistryQuery = z.infer<typeof semanticRegistryQuerySchema>;

export const semanticFactsParamSchema = semanticRegistryParamSchema;
export type SemanticFactsParam = z.infer<typeof semanticFactsParamSchema>;

export const semanticFactsQuerySchema = z.object({
  fact_type: z.string().trim().min(1).max(64).optional(),
  document_id: z.string().uuid().optional(),
  limit: z.coerce.number().int().min(1).max(500).optional().default(100),
  offset: z.coerce.number().int().min(0).optional().default(0),
});
export type SemanticFactsQuery = z.infer<typeof semanticFactsQuerySchema>;

export const semanticChunksParamSchema = semanticRegistryParamSchema;
export type SemanticChunksParam = z.infer<typeof semanticChunksParamSchema>;

export const semanticChunksQuerySchema = z.object({
  document_id: z.string().uuid().optional(),
  limit: z.coerce.number().int().min(1).max(500).optional().default(100),
  offset: z.coerce.number().int().min(0).optional().default(0),
});
export type SemanticChunksQuery = z.infer<typeof semanticChunksQuerySchema>;

export const semanticEvidenceParamSchema = semanticRegistryParamSchema;
export type SemanticEvidenceParam = z.infer<typeof semanticEvidenceParamSchema>;

export const semanticEvidenceQuerySchema = z.object({
  query_id: z.string().trim().min(1).max(128).optional(),
  limit: z.coerce.number().int().min(1).max(500).optional().default(100),
  offset: z.coerce.number().int().min(0).optional().default(0),
});
export type SemanticEvidenceQuery = z.infer<typeof semanticEvidenceQuerySchema>;

// --- Allowed MIME types ---

export const ALLOWED_MIME_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
  'text/plain',
  'text/csv',
] as const;

export const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
