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

// --- Allowed MIME types ---

export const ALLOWED_MIME_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
  'text/plain',
  'text/csv',
] as const;

export const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
