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

export const documentStatusParamSchema = z.object({
  dataProductId: z.string().uuid(),
  documentId: z.string().uuid(),
});
export type DocumentStatusParam = z.infer<typeof documentStatusParamSchema>;

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

export const semanticEvidenceLinkParamSchema = semanticRegistryParamSchema;
export type SemanticEvidenceLinkParam = z.infer<typeof semanticEvidenceLinkParamSchema>;

export const semanticEvidenceLinkQuerySchema = z.object({
  citation_type: z.enum(['sql', 'document_fact', 'document_chunk']),
  reference_id: z.string().trim().min(1).max(256),
  query_id: z.string().trim().min(1).max(128).optional(),
});
export type SemanticEvidenceLinkQuery = z.infer<typeof semanticEvidenceLinkQuerySchema>;

export const semanticAuditParamSchema = semanticRegistryParamSchema;
export type SemanticAuditParam = z.infer<typeof semanticAuditParamSchema>;

export const semanticAuditQuerySchema = z.object({
  query_id: z.string().trim().min(1).max(128).optional(),
  final_decision: z.string().trim().min(1).max(32).optional(),
  limit: z.coerce.number().int().min(1).max(500).optional().default(100),
  offset: z.coerce.number().int().min(0).optional().default(0),
});
export type SemanticAuditQuery = z.infer<typeof semanticAuditQuerySchema>;

export const semanticOpsSummaryParamSchema = semanticRegistryParamSchema;
export type SemanticOpsSummaryParam = z.infer<typeof semanticOpsSummaryParamSchema>;

export const semanticOpsSummaryQuerySchema = z.object({
  window_hours: z.coerce.number().int().min(1).max(24 * 30).optional().default(24),
});
export type SemanticOpsSummaryQuery = z.infer<typeof semanticOpsSummaryQuerySchema>;

export const semanticOpsDashboardParamSchema = semanticRegistryParamSchema;
export type SemanticOpsDashboardParam = z.infer<typeof semanticOpsDashboardParamSchema>;

export const semanticOpsDashboardQuerySchema = z.object({
  window_hours: z.coerce.number().int().min(1).max(24 * 30).optional().default(24),
  trace_limit: z.coerce.number().int().min(1).max(200).optional().default(25),
  alert_limit: z.coerce.number().int().min(1).max(500).optional().default(100),
});
export type SemanticOpsDashboardQuery = z.infer<typeof semanticOpsDashboardQuerySchema>;

export const governanceAuditParamSchema = semanticRegistryParamSchema;
export type GovernanceAuditParam = z.infer<typeof governanceAuditParamSchema>;

export const governanceAuditQuerySchema = z.object({
  event_type: z.string().trim().min(1).max(64).optional(),
  limit: z.coerce.number().int().min(1).max(500).optional().default(100),
  offset: z.coerce.number().int().min(0).optional().default(0),
});
export type GovernanceAuditQuery = z.infer<typeof governanceAuditQuerySchema>;

export const legalHoldParamSchema = semanticRegistryParamSchema;
export type LegalHoldParam = z.infer<typeof legalHoldParamSchema>;

export const legalHoldBodySchema = z
  .object({
    document_id: z.string().uuid(),
    action: z.enum(['activate', 'release']).optional().default('activate'),
    hold_reason: z.string().trim().min(1).max(2000).optional(),
    hold_ref: z.string().trim().max(128).optional(),
  })
  .superRefine((value, ctx) => {
    if (value.action === 'activate' && !value.hold_reason) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['hold_reason'],
        message: 'hold_reason is required when action=activate',
      });
    }
  });
export type LegalHoldBody = z.infer<typeof legalHoldBodySchema>;

export const retentionRunParamSchema = semanticRegistryParamSchema;
export type RetentionRunParam = z.infer<typeof retentionRunParamSchema>;

export const retentionRunBodySchema = z.object({
  retention_now: z.string().datetime().optional(),
  dry_run: z.coerce.boolean().optional().default(false),
});
export type RetentionRunBody = z.infer<typeof retentionRunBodySchema>;

// --- Allowed MIME types ---

export const ALLOWED_MIME_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
  'text/plain',
  'text/csv',
] as const;

export const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
