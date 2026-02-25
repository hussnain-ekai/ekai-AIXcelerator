import crypto from 'node:crypto';

import type { FastifyInstance, FastifyRequest } from 'fastify';
import multipart from '@fastify/multipart';

import { config } from '../config.js';
import { postgresService } from '../services/postgresService.js';
import { minioService } from '../services/minioService.js';
import {
  extractDocumentContent,
  uploadChunksToSnowflake,
  uploadPageAwareChunksToSnowflake,
  type DocumentExtractionResult,
} from '../services/documentExtractionService.js';
import {
  MAX_FILE_SIZE_BYTES,
  applyContextParamSchema,
  applyContextSchema,
  contextCurrentParamSchema,
  contextCurrentQuerySchema,
  contextDeltaParamSchema,
  contextDeltaQuerySchema,
  deleteDocumentParamSchema,
  documentContentParamSchema,
  documentStatusParamSchema,
  extractDocumentParamSchema,
  governanceAuditParamSchema,
  governanceAuditQuerySchema,
  legalHoldBodySchema,
  legalHoldParamSchema,
  listDocumentsParamSchema,
  missionStepSchema,
  retentionRunBodySchema,
  retentionRunParamSchema,
  semanticAuditParamSchema,
  semanticAuditQuerySchema,
  semanticOpsDashboardParamSchema,
  semanticOpsDashboardQuerySchema,
  semanticOpsSummaryParamSchema,
  semanticOpsSummaryQuerySchema,
  semanticChunksParamSchema,
  semanticChunksQuerySchema,
  semanticEvidenceParamSchema,
  semanticEvidenceLinkParamSchema,
  semanticEvidenceLinkQuerySchema,
  semanticEvidenceQuerySchema,
  semanticFactsParamSchema,
  semanticFactsQuerySchema,
  semanticRegistryParamSchema,
  semanticRegistryQuerySchema,
  uploadDocumentSchema,
} from '../schemas/document.js';

const DOCUMENTS_BUCKET = 'documents';

const MISSION_STEPS = [
  'discovery',
  'requirements',
  'modeling',
  'generation',
  'validation',
  'publishing',
] as const;

type MissionStep = (typeof MISSION_STEPS)[number];

type ContextSelectionState = 'candidate' | 'active' | 'reference' | 'excluded';

interface UploadedDocumentRow {
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
  source_channel: string;
  user_note: string | null;
  doc_kind: string | null;
  summary: string | null;
  is_deleted: boolean;
  deleted_at: string | null;
  deleted_by: string | null;
  context_version_id: string | null;
}

interface ContextVersion {
  id: string;
  version: number;
}

interface EvidenceSummary {
  table_names: string[];
  relationship_hints: string[];
  metric_hints: string[];
  excerpt: string | null;
}

interface InsertedDocumentFact {
  id: string;
  factType: string;
  subjectKey: string | null;
  objectValue: string | null;
  numericValue: number | null;
}

interface KeyMention {
  domain: string;
  value: string;
  reason: string;
  confidence: number;
}

interface FactLinkCandidate {
  targetDomain: string;
  targetKey: string;
  linkReason: string;
  linkConfidence: number;
}

interface RegistrySyncInput {
  dataProductId: string;
  documentId: string;
  filename: string;
  contentType: string | null;
  sourceChannel: string;
  uploadedBy: string;
  fileSize: number;
  minioPath: string;
  checksumSha256: string | null;
  extraction: DocumentExtractionResult;
  userNote: string | null;
  docKind: string | null;
  summary: string | null;
  contextVersionId: string | null;
}

interface StaleArtifactImpact {
  artifact_type: string;
  artifact_label: string;
  impacted_steps: MissionStep[];
  snapshot_context_version: number | null;
  latest_context_version: number | null;
  reason: string;
}

interface ContextImpactSummary {
  impactedSteps: MissionStep[];
  staleArtifacts: StaleArtifactImpact[];
  recommendedActions: string[];
}

const STEP_ARTIFACT_CANDIDATES: Record<MissionStep, string[]> = {
  discovery: ['data_description', 'erd', 'quality_report'],
  requirements: ['brd'],
  modeling: ['data_catalog', 'business_glossary', 'metrics', 'validation_rules', 'lineage'],
  generation: ['yaml', 'semantic_view'],
  validation: ['yaml', 'validation_rules'],
  publishing: ['semantic_view', 'published_agent'],
};

const STEP_RERUN_ACTIONS: Record<MissionStep, string> = {
  discovery: 'Re-run discovery to refresh source profiling and relationship evidence.',
  requirements: 'Re-generate requirements so the BRD reflects the updated document context.',
  modeling: 'Re-run modeling outputs (catalog, glossary, metrics, and lineage) with updated context.',
  generation: 'Re-generate the semantic model so definitions align with the latest context.',
  validation: 'Re-run validation to confirm the model still satisfies business rules.',
  publishing: 'Re-publish the data product/agent after upstream artifacts are refreshed.',
};

function deriveParseQualityScore(extraction: DocumentExtractionResult): number {
  const raw = extraction.extractionMetadata['quality_score'];
  const parsed = typeof raw === 'number' ? raw : Number(raw);
  if (Number.isFinite(parsed) && parsed >= 0 && parsed <= 1) {
    return Number((parsed * 100).toFixed(2));
  }

  if (extraction.extractionStatus === 'failed') return 0;
  if (extraction.extractionStatus === 'pending') return 40;

  const warningPenalty = Math.min(extraction.extractionWarnings.length * 8, 40);
  const base = 92 - warningPenalty;
  return Math.max(40, Math.min(99, Number(base.toFixed(2))));
}

function buildExtractionDiagnostics(extraction: DocumentExtractionResult): Record<string, unknown> {
  return {
    status: extraction.extractionStatus,
    method: extraction.extractionMethod,
    warnings: extraction.extractionWarnings,
    metadata: extraction.extractionMetadata,
  };
}

function splitIntoChunks(text: string, chunkSize = 1800): string[] {
  const normalized = text.replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];

  const chunks: string[] = [];
  let cursor = 0;
  while (cursor < normalized.length) {
    let end = Math.min(cursor + chunkSize, normalized.length);
    if (end < normalized.length) {
      const breakAt = normalized.lastIndexOf('\n', end);
      if (breakAt > cursor + Math.floor(chunkSize * 0.5)) {
        end = breakAt;
      }
    }
    const piece = normalized.slice(cursor, end).trim();
    if (piece.length > 0) chunks.push(piece);
    cursor = end;
  }
  return chunks;
}

function extractMonetaryFacts(text: string | null): Array<{ value: number; currency: string | null }> {
  if (!text) return [];
  const facts: Array<{ value: number; currency: string | null }> = [];
  const seen = new Set<string>();
  const regex = /\b([$€£])?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\b/g;

  for (const match of text.matchAll(regex)) {
    const symbol = match[1] ?? null;
    const raw = match[2] ?? '';
    const normalized = raw.replace(/,/g, '');
    const value = Number(normalized);
    if (!Number.isFinite(value)) continue;
    if (value < 0) continue;
    const key = `${symbol ?? ''}:${value}`;
    if (seen.has(key)) continue;
    seen.add(key);
    facts.push({
      value,
      currency: symbol === '$' ? 'USD' : symbol === '€' ? 'EUR' : symbol === '£' ? 'GBP' : null,
    });
    if (facts.length >= 30) break;
  }

  return facts;
}

const KEY_MENTION_PATTERNS: Array<{
  domain: string;
  regex: RegExp;
  reason: string;
  confidence: number;
}> = [
  {
    domain: 'invoice',
    regex: /\b(?:invoice|inv)\s*(?:no|number|#)?\s*[:=\-]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{2,})/gi,
    reason: 'Detected invoice identifier pattern in document text.',
    confidence: 0.82,
  },
  {
    domain: 'purchase_order',
    regex: /\b(?:purchase\s*order|po)\s*(?:no|number|#)?\s*[:=\-]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{2,})/gi,
    reason: 'Detected purchase-order identifier pattern in document text.',
    confidence: 0.8,
  },
  {
    domain: 'part',
    regex: /\b(?:part|item|sku)\s*(?:no|number|#)?\s*[:=\-]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{2,})/gi,
    reason: 'Detected part/item identifier pattern in document text.',
    confidence: 0.78,
  },
  {
    domain: 'customer',
    regex: /\b(?:customer|client|account)\s*(?:id|no|number|#)?\s*[:=\-]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{2,})/gi,
    reason: 'Detected customer/account identifier pattern in document text.',
    confidence: 0.76,
  },
];

const DOMAIN_TABLE_HINTS: Record<string, string[]> = {
  invoice: ['INVOICE', 'BILLING', 'SALES'],
  purchase_order: ['PURCHASE', 'ORDER', 'PROCUREMENT'],
  part: ['PART', 'ITEM', 'SKU', 'PRODUCT', 'INVENTORY'],
  customer: ['CUSTOMER', 'CLIENT', 'ACCOUNT'],
  metric: ['METRIC', 'KPI', 'MEASURE', 'FACT'],
  transaction_amount: ['INVOICE', 'ORDER', 'PAYMENT', 'TRANSACTION'],
};

function normalizeSemanticToken(value: string, maxLen = 128): string {
  return value
    .trim()
    .replace(/[`"'“”]/g, '')
    .replace(/\s+/g, '')
    .replace(/[^A-Za-z0-9:_./-]/g, '')
    .toUpperCase()
    .slice(0, maxLen);
}

function normalizeTableAnchors(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return dedupeStrings(
    raw
      .map((entry) =>
        typeof entry === 'string' ? entry.replace(/"/g, '').trim().toUpperCase() : '',
      )
      .filter((entry) => entry.length > 0),
  ).slice(0, 60);
}

function pickDomainTableAnchor(tableAnchors: string[], domain: string): string | null {
  const hints = DOMAIN_TABLE_HINTS[domain] ?? [];
  if (hints.length === 0) return null;
  for (const table of tableAnchors) {
    if (hints.some((hint) => table.includes(hint))) {
      return table;
    }
  }
  return tableAnchors[0] ?? null;
}

function extractKeyMentions(text: string | null): KeyMention[] {
  if (!text) return [];
  const mentions: KeyMention[] = [];
  const seen = new Set<string>();

  for (const pattern of KEY_MENTION_PATTERNS) {
    const regex = new RegExp(pattern.regex.source, pattern.regex.flags);
    for (const match of text.matchAll(regex)) {
      const rawValue = match[1] ?? '';
      const token = normalizeSemanticToken(rawValue, 96);
      if (token.length < 3) continue;

      const key = `${pattern.domain}:${token}`;
      if (seen.has(key)) continue;
      seen.add(key);

      mentions.push({
        domain: pattern.domain,
        value: token,
        reason: pattern.reason,
        confidence: pattern.confidence,
      });

      if (mentions.length >= 80) return mentions;
    }
  }

  return mentions;
}

function buildFactLinkCandidates(
  fact: InsertedDocumentFact,
  mentions: KeyMention[],
  tableAnchors: string[],
): FactLinkCandidate[] {
  const candidates: FactLinkCandidate[] = [];
  const factText = `${fact.subjectKey ?? ''} ${fact.objectValue ?? ''}`.toUpperCase();

  if (fact.factType === 'metric_hint') {
    const metricToken = normalizeSemanticToken(fact.subjectKey ?? fact.objectValue ?? '', 96);
    if (metricToken.length >= 3) {
      const metricTable = pickDomainTableAnchor(tableAnchors, 'metric');
      const metricKey = metricTable
        ? `${metricTable}::METRIC:${metricToken}`
        : `METRIC:${metricToken}`;
      candidates.push({
        targetDomain: 'metric',
        targetKey: metricKey,
        linkReason: 'Metric-hint fact mapped to semantic metric key.',
        linkConfidence: 0.72,
      });
    }
  }

  if (fact.factType === 'monetary_amount' && fact.numericValue !== null) {
    candidates.push({
      targetDomain: 'transaction_amount',
      targetKey: `AMOUNT:${fact.numericValue}`,
      linkReason: 'Monetary fact mapped as transaction amount candidate.',
      linkConfidence: 0.65,
    });
  }

  const allowedDomains =
    fact.factType === 'monetary_amount'
      ? new Set(['invoice', 'purchase_order', 'part', 'customer'])
      : new Set(['metric', 'invoice', 'purchase_order', 'part', 'customer']);

  for (const mention of mentions) {
    if (!allowedDomains.has(mention.domain)) continue;
    if (
      fact.factType === 'metric_hint' &&
      mention.domain !== 'metric' &&
      !factText.includes(mention.value)
    ) {
      continue;
    }

    const tableAnchor = pickDomainTableAnchor(tableAnchors, mention.domain);
    const targetKey = tableAnchor
      ? `${tableAnchor}::${mention.domain.toUpperCase()}:${mention.value}`
      : `${mention.domain.toUpperCase()}:${mention.value}`;

    candidates.push({
      targetDomain: mention.domain,
      targetKey,
      linkReason: mention.reason,
      linkConfidence: mention.confidence,
    });
  }

  const deduped = new Map<string, FactLinkCandidate>();
  for (const candidate of candidates) {
    const key = `${candidate.targetDomain}:${candidate.targetKey}`;
    if (!deduped.has(key)) {
      deduped.set(key, candidate);
    }
  }

  return Array.from(deduped.values()).slice(0, 8);
}

function readFieldValue(field: unknown): string | undefined {
  if (!field || typeof field !== 'object') return undefined;
  const value = (field as { value?: unknown }).value;
  return typeof value === 'string' ? value : undefined;
}

function normalizeStepName(input: string | null | undefined): MissionStep {
  const normalized = (input ?? '').toLowerCase();

  if (normalized === 'prepare' || normalized === 'transformation') {
    return 'discovery';
  }
  if (normalized === 'requirements') return 'requirements';
  if (normalized === 'modeling') return 'modeling';
  if (normalized === 'generation') return 'generation';
  if (normalized === 'validation') return 'validation';
  if (normalized === 'publishing' || normalized === 'explorer') return 'publishing';

  return 'discovery';
}

function artifactLabel(artifactType: string): string {
  const key = artifactType.toLowerCase();
  const labels: Record<string, string> = {
    erd: 'ERD',
    data_description: 'Data description',
    quality_report: 'Data quality report',
    brd: 'BRD',
    data_catalog: 'Data catalog',
    business_glossary: 'Business glossary',
    metrics: 'Metrics',
    metrics_definitions: 'Metrics',
    validation_rules: 'Validation rules',
    lineage: 'Lineage',
    yaml: 'Semantic model',
    semantic_view: 'Semantic model',
    published_agent: 'Published agent',
  };
  return labels[key] ?? key.replace(/_/g, ' ');
}

function uniqueMissionSteps(values: Array<string | MissionStep>): MissionStep[] {
  const deduped = new Set<MissionStep>();
  for (const value of values) {
    deduped.add(normalizeStepName(String(value)));
  }
  return Array.from(deduped.values());
}

function collectStepsFromChangeSummary(summary: Record<string, unknown>): MissionStep[] {
  const raw: Array<string | MissionStep> = [];

  const directStep = summary['step'];
  if (typeof directStep === 'string' && directStep.trim().length > 0) {
    raw.push(directStep);
  }

  const impacted = summary['impacted_steps'];
  if (Array.isArray(impacted)) {
    for (const item of impacted) {
      if (typeof item === 'string' && item.trim().length > 0) raw.push(item);
    }
  }

  const updates = summary['updates'];
  if (Array.isArray(updates)) {
    for (const update of updates) {
      if (!update || typeof update !== 'object') continue;
      const step = (update as Record<string, unknown>)['step'];
      if (typeof step === 'string' && step.trim().length > 0) raw.push(step);
    }
  }

  return raw.length > 0 ? uniqueMissionSteps(raw) : [];
}

function inferDocumentKind(filename: string, contentType: string | null): string {
  const lowerName = filename.toLowerCase();
  const ext = lowerName.includes('.') ? lowerName.split('.').pop() ?? '' : '';
  const mime = (contentType ?? '').toLowerCase();

  if (
    ext === 'sql' ||
    ext === 'ddl' ||
    ext === 'dbml' ||
    mime.includes('sql') ||
    lowerName.includes('schema') ||
    lowerName.includes('create table')
  ) {
    return 'schema_definition';
  }

  if (ext === 'pbix' || lowerName.includes('power bi') || lowerName.includes('powerbi')) {
    return 'bi_model';
  }

  if (lowerName.includes('erd') || lowerName.includes('diagram')) {
    return 'erd_reference';
  }

  if (lowerName.includes('brd') || lowerName.includes('requirement')) {
    return 'requirements_doc';
  }

  if (
    lowerName.includes('glossary') ||
    lowerName.includes('metric') ||
    lowerName.includes('kpi') ||
    lowerName.includes('policy')
  ) {
    return 'business_rules_doc';
  }

  if (ext === 'pdf' || ext === 'doc' || ext === 'docx') {
    return 'business_doc';
  }

  if (ext === 'csv') {
    return 'tabular_extract';
  }

  if (ext === 'txt' || ext === 'md') {
    return 'notes';
  }

  return 'general_reference';
}

function inferStepCandidates(docKind: string, filename: string): MissionStep[] {
  const lowerName = filename.toLowerCase();
  const steps = new Set<MissionStep>();

  switch (docKind) {
    case 'schema_definition':
    case 'erd_reference':
    case 'bi_model':
      steps.add('discovery');
      steps.add('modeling');
      steps.add('generation');
      break;
    case 'requirements_doc':
      steps.add('requirements');
      steps.add('modeling');
      steps.add('generation');
      break;
    case 'business_rules_doc':
      steps.add('requirements');
      steps.add('modeling');
      steps.add('validation');
      break;
    case 'business_doc':
      steps.add('requirements');
      steps.add('modeling');
      break;
    case 'tabular_extract':
      steps.add('discovery');
      steps.add('requirements');
      break;
    default:
      steps.add('requirements');
      break;
  }

  if (lowerName.includes('compliance') || lowerName.includes('regulation') || lowerName.includes('audit')) {
    steps.add('validation');
    steps.add('publishing');
  }

  return Array.from(steps);
}

function dedupeStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const deduped: string[] = [];

  for (const value of values) {
    const trimmed = value.trim();
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(trimmed);
  }

  return deduped;
}

function extractEvidenceSummary(text: string | null): EvidenceSummary {
  if (!text) {
    return {
      table_names: [],
      relationship_hints: [],
      metric_hints: [],
      excerpt: null,
    };
  }

  const tableNames = dedupeStrings(
    Array.from(text.matchAll(/create\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z0-9_."`]+)/gi)).map(
      (match) => match[1] ?? '',
    ),
  ).slice(0, 25);

  const relationshipHints = dedupeStrings(
    Array.from(text.matchAll(/references\s+([A-Za-z0-9_."`]+)/gi)).map((match) => match[1] ?? ''),
  ).slice(0, 25);

  const metricHints = dedupeStrings(
    Array.from(text.matchAll(/\b(kpi|metric|measure|sla|threshold|target)\b[^\n]{0,120}/gi)).map(
      (match) => match[0] ?? '',
    ),
  ).slice(0, 20);

  const excerpt = text.slice(0, 1200);

  return {
    table_names: tableNames,
    relationship_hints: relationshipHints,
    metric_hints: metricHints,
    excerpt,
  };
}

function summarizeDocument(
  docKind: string,
  evidenceSummary: EvidenceSummary,
  textContent: string | null,
): string {
  if (docKind === 'schema_definition') {
    const tableCount = evidenceSummary.table_names.length;
    const relCount = evidenceSummary.relationship_hints.length;
    return `Schema-focused document: ${tableCount} table references and ${relCount} relationship hints detected.`;
  }

  if (docKind === 'bi_model') {
    return 'BI model file detected. Use this in discovery/modeling to cross-check entities, measures, and relationships.';
  }

  if (docKind === 'requirements_doc') {
    const metricCount = evidenceSummary.metric_hints.length;
    return `Requirements-oriented document detected. ${metricCount} potential KPI/metric hints extracted.`;
  }

  if (textContent) {
    const firstLine = textContent
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line.length > 0);

    if (firstLine) {
      return firstLine.slice(0, 240);
    }
  }

  return 'Reference document uploaded. Review and activate relevant evidence by mission step before regeneration.';
}

function summarizeExtractionWarnings(warnings: string[]): string | null {
  if (warnings.length === 0) return null;
  const compact = warnings
    .map((warning) => warning.replace(/\s+/g, ' ').trim())
    .filter((warning) => warning.length > 0)
    .slice(0, 6)
    .join(' | ');
  if (!compact) return null;
  return compact.slice(0, 2000);
}

function buildEvidencePayload(
  evidenceSummary: EvidenceSummary,
  extraction: DocumentExtractionResult,
): Record<string, unknown> {
  return {
    ...evidenceSummary,
    extraction: {
      method: extraction.extractionMethod,
      status: extraction.extractionStatus,
      warnings: extraction.extractionWarnings,
      metadata: extraction.extractionMetadata,
    },
  };
}

function isRecoverableContextSchemaError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  const code = (err as { code?: unknown }).code;
  return code === '42P01' || code === '42703' || code === '23503';
}

function asJsonArray(value: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(value)) {
    return value.filter((entry): entry is Record<string, unknown> => !!entry && typeof entry === 'object');
  }
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) {
        return parsed.filter(
          (entry): entry is Record<string, unknown> => !!entry && typeof entry === 'object',
        );
      }
    } catch {
      return [];
    }
  }
  return [];
}

function inferModelVersionHash(toolCalls: unknown): string | null {
  const entries = asJsonArray(toolCalls);
  for (const entry of entries) {
    const type = String(entry['type'] ?? '').toLowerCase();
    if (type !== 'model') continue;
    const hash = entry['model_hash'];
    if (typeof hash === 'string' && hash.trim().length > 0) {
      return hash.trim();
    }
  }
  return null;
}

export async function documentRoutes(app: FastifyInstance): Promise<void> {
  // Register multipart support for this plugin scope
  await app.register(multipart, {
    limits: {
      fileSize: MAX_FILE_SIZE_BYTES,
      files: 1,
    },
  });

  async function emitOpsAlertEvent(input: {
    dataProductId: string;
    snowflakeUser: string;
    signal: string;
    severity?: 'info' | 'warning' | 'high' | 'critical';
    message: string;
    sourceRoute: string;
    sessionId?: string | null;
    queryId?: string | null;
    metadata?: Record<string, unknown>;
  }): Promise<void> {
    try {
      await postgresService.query(
        `INSERT INTO ops_alert_events
           (data_product_id, signal, severity, message, source_service, source_route,
            session_id, query_id, metadata, created_by)
         VALUES
           ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)`,
        [
          input.dataProductId,
          input.signal,
          input.severity ?? 'warning',
          input.message,
          'backend',
          input.sourceRoute,
          input.sessionId ?? null,
          input.queryId ?? null,
          JSON.stringify(input.metadata ?? {}),
          'ekaix-backend',
        ],
        input.snowflakeUser,
      );
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) return;
      app.log.debug(
        { err, signal: input.signal, data_product_id: input.dataProductId },
        'Failed to persist ops alert event',
      );
    }
  }

  async function createContextVersion(
    dataProductId: string,
    snowflakeUser: string,
    reason: string,
    summary: Record<string, unknown>,
  ): Promise<ContextVersion | null> {
    try {
      const result = await postgresService.query(
        `INSERT INTO context_versions
           (data_product_id, version, reason, changed_by, change_summary)
         VALUES ($1::uuid, 1, $2, $3, $4::jsonb)
         RETURNING id, version`,
        [dataProductId, reason, snowflakeUser, JSON.stringify(summary)],
        snowflakeUser,
      );

      const row = result.rows[0] as { id: string; version: number } | undefined;
      if (!row) return null;
      return { id: row.id, version: Number(row.version) };
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) {
        app.log.warn(
          { err, dataProductId },
          'Context schema not available yet; proceeding without version tracking',
        );
        return null;
      }
      throw err;
    }
  }

  async function upsertContextSelection(
    dataProductId: string,
    step: MissionStep,
    documentId: string,
    evidenceId: string,
    state: ContextSelectionState,
    contextVersionId: string | null,
    snowflakeUser: string,
  ): Promise<void> {
    try {
      await postgresService.query(
        `INSERT INTO context_step_selections
           (data_product_id, step_name, document_id, evidence_id, state, selected_by, context_version_id)
         VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5, $6, $7::uuid)
         ON CONFLICT (data_product_id, step_name, evidence_id)
         DO UPDATE
           SET state = EXCLUDED.state,
               selected_by = EXCLUDED.selected_by,
               context_version_id = EXCLUDED.context_version_id,
               updated_at = now()`,
        [
          dataProductId,
          step,
          documentId,
          evidenceId,
          state,
          snowflakeUser,
          contextVersionId,
        ],
        snowflakeUser,
      );
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) return;
      throw err;
    }
  }

  async function buildContextImpactSummary(
    dataProductId: string,
    snowflakeUser: string,
    impactedStepHints: Array<string | MissionStep>,
    latestContextVersionHint: number | null,
  ): Promise<ContextImpactSummary> {
    const impactedSteps = uniqueMissionSteps(impactedStepHints);
    const impactedStepSet = new Set<MissionStep>(impactedSteps);

    let latestContextVersion = latestContextVersionHint;
    if (latestContextVersion === null) {
      try {
        const latestVersionResult = await postgresService.query(
          `SELECT MAX(version) AS version
           FROM context_versions
           WHERE data_product_id = $1::uuid`,
          [dataProductId],
          snowflakeUser,
        );
        const rawVersion = latestVersionResult.rows[0] as { version?: number | null } | undefined;
        latestContextVersion =
          rawVersion && rawVersion.version !== undefined && rawVersion.version !== null
            ? Number(rawVersion.version)
            : null;
      } catch (err) {
        if (!isRecoverableContextSchemaError(err)) throw err;
      }
    }

    const staleByType = new Map<string, StaleArtifactImpact>();
    try {
      const snapshotResult = await postgresService.query(
        `SELECT DISTINCT ON (acs.artifact_type)
           acs.artifact_type,
           acs.snapshot,
           cv.version AS snapshot_context_version
         FROM artifact_context_snapshots acs
         LEFT JOIN context_versions cv ON cv.id = acs.context_version_id
         WHERE acs.data_product_id = $1::uuid
         ORDER BY acs.artifact_type, acs.created_at DESC`,
        [dataProductId],
        snowflakeUser,
      );

      for (const rawRow of snapshotResult.rows as Array<{
        artifact_type: string;
        snapshot: unknown;
        snapshot_context_version: number | null;
      }>) {
        const artifactType = String(rawRow.artifact_type || '').toLowerCase();
        if (!artifactType) continue;

        let snapshotPayload: Record<string, unknown> = {};
        if (rawRow.snapshot && typeof rawRow.snapshot === 'object') {
          snapshotPayload = rawRow.snapshot as Record<string, unknown>;
        } else if (typeof rawRow.snapshot === 'string') {
          try {
            const parsed = JSON.parse(rawRow.snapshot);
            if (parsed && typeof parsed === 'object') {
              snapshotPayload = parsed as Record<string, unknown>;
            }
          } catch {
            snapshotPayload = {};
          }
        }

        const stepCandidatesRaw: Array<string | MissionStep> = [];
        const snapshotStep = snapshotPayload['step'];
        if (typeof snapshotStep === 'string' && snapshotStep.trim().length > 0) {
          stepCandidatesRaw.push(snapshotStep);
        }

        const snapshotImpacted = snapshotPayload['impacted_steps'];
        if (Array.isArray(snapshotImpacted)) {
          for (const item of snapshotImpacted) {
            if (typeof item === 'string' && item.trim().length > 0) {
              stepCandidatesRaw.push(item);
            }
          }
        }
        const snapshotSteps = uniqueMissionSteps(stepCandidatesRaw);

        const snapshotVersion =
          rawRow.snapshot_context_version !== null &&
          rawRow.snapshot_context_version !== undefined
            ? Number(rawRow.snapshot_context_version)
            : null;
        const hasContextDrift =
          latestContextVersion !== null &&
          snapshotVersion !== null &&
          snapshotVersion < latestContextVersion;
        const stepMatch =
          impactedStepSet.size === 0 ||
          snapshotSteps.length === 0 ||
          snapshotSteps.some((step) => impactedStepSet.has(step));

        if (!hasContextDrift || !stepMatch) continue;

        const impactedForArtifact =
          snapshotSteps.length > 0
            ? snapshotSteps.filter((step) => impactedStepSet.size === 0 || impactedStepSet.has(step))
            : impactedSteps;

        staleByType.set(artifactType, {
          artifact_type: artifactType,
          artifact_label: artifactLabel(artifactType),
          impacted_steps: impactedForArtifact.length > 0 ? impactedForArtifact : impactedSteps,
          snapshot_context_version: snapshotVersion,
          latest_context_version: latestContextVersion,
          reason: `Built with context version ${snapshotVersion}; latest context is ${latestContextVersion}.`,
        });
      }
    } catch (err) {
      if (!isRecoverableContextSchemaError(err)) throw err;
    }

    // Fallback for environments without artifact snapshots: infer likely stale
    // artifacts from impacted mission-control steps.
    for (const step of impactedSteps) {
      for (const artifactType of STEP_ARTIFACT_CANDIDATES[step] ?? []) {
        const existing = staleByType.get(artifactType);
        if (existing) {
          const merged = uniqueMissionSteps([...existing.impacted_steps, step]);
          existing.impacted_steps = merged;
          staleByType.set(artifactType, existing);
          continue;
        }
        staleByType.set(artifactType, {
          artifact_type: artifactType,
          artifact_label: artifactLabel(artifactType),
          impacted_steps: [step],
          snapshot_context_version: null,
          latest_context_version: latestContextVersion,
          reason: `${artifactLabel(artifactType)} may be outdated due to ${step} context changes.`,
        });
      }
    }

    const recommendedActions = Array.from(
      new Set(
        impactedSteps.map((step) => STEP_RERUN_ACTIONS[step]).filter((item) => !!item),
      ),
    );
    if (staleByType.has('published_agent')) {
      recommendedActions.push('After refresh, re-publish so the agent serves updated evidence.');
    }

    return {
      impactedSteps,
      staleArtifacts: Array.from(staleByType.values()),
      recommendedActions: recommendedActions.slice(0, 8),
    };
  }

  async function syncDocumentRegistry(
    input: RegistrySyncInput,
    snowflakeUser: string,
  ): Promise<string | null> {
    try {
      const diagnostics = buildExtractionDiagnostics(input.extraction);
      const qualityScore = deriveParseQualityScore(input.extraction);
      const metadata = {
        file_size_bytes: input.fileSize,
        minio_path: input.minioPath,
        source_channel: input.sourceChannel,
        user_note: input.userNote,
        doc_kind: input.docKind,
        summary: input.summary,
        context_version_id: input.contextVersionId,
      };

      const result = await postgresService.query(
        `INSERT INTO doc_registry
           (data_product_id, document_id, source_system, source_uri, title, mime_type,
            checksum_sha256, version_id, uploaded_by, uploaded_at,
            extraction_status, extraction_method, parse_quality_score,
            extraction_diagnostics, metadata, deleted_at)
         VALUES
           ($1::uuid, $2::uuid, $3, $4, $5, $6,
            $7, 1, $8, now(),
            $9::extraction_status, $10, $11,
            $12::jsonb, $13::jsonb, NULL)
         ON CONFLICT (data_product_id, document_id)
         DO UPDATE
           SET source_system = EXCLUDED.source_system,
               source_uri = EXCLUDED.source_uri,
               title = EXCLUDED.title,
               mime_type = EXCLUDED.mime_type,
               checksum_sha256 = EXCLUDED.checksum_sha256,
               version_id = CASE
                 WHEN doc_registry.checksum_sha256 IS DISTINCT FROM EXCLUDED.checksum_sha256
                   THEN doc_registry.version_id + 1
                 ELSE doc_registry.version_id
               END,
               uploaded_by = EXCLUDED.uploaded_by,
               extraction_status = EXCLUDED.extraction_status,
               extraction_method = EXCLUDED.extraction_method,
               parse_quality_score = EXCLUDED.parse_quality_score,
               extraction_diagnostics = EXCLUDED.extraction_diagnostics,
               metadata = COALESCE(doc_registry.metadata, '{}'::jsonb) || EXCLUDED.metadata,
               deleted_at = NULL
         RETURNING id`,
        [
          input.dataProductId,
          input.documentId,
          input.sourceChannel,
          input.minioPath,
          input.filename,
          input.contentType,
          input.checksumSha256,
          input.uploadedBy,
          input.extraction.extractionStatus,
          input.extraction.extractionMethod,
          qualityScore,
          JSON.stringify(diagnostics),
          JSON.stringify(metadata),
        ],
        snowflakeUser,
      );

      const row = result.rows[0] as { id: string } | undefined;
      return row?.id ?? null;
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) return null;
      throw err;
    }
  }

  async function refreshDocumentChunks(
    dataProductId: string,
    documentId: string,
    registryId: string,
    extraction: DocumentExtractionResult,
    snowflakeUser: string,
  ): Promise<number> {
    try {
      await postgresService.query(
        `DELETE FROM doc_chunks
         WHERE data_product_id = $1::uuid
           AND document_id = $2::uuid`,
        [dataProductId, documentId],
        snowflakeUser,
      );

      if (extraction.extractionStatus !== 'completed' || !extraction.extractedText) {
        return 0;
      }

      const chunks = splitIntoChunks(extraction.extractedText);
      if (chunks.length === 0) return 0;

      for (let i = 0; i < chunks.length; i += 1) {
        await postgresService.query(
          `INSERT INTO doc_chunks
             (id, data_product_id, document_id, registry_id, chunk_seq,
              section_path, page_no, chunk_text, parser_version, extraction_confidence)
           VALUES
             ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, $9, $10)`,
          [
            crypto.randomUUID(),
            dataProductId,
            documentId,
            registryId,
            i + 1,
            `auto_chunk_${i + 1}`,
            null,
            chunks[i],
            'ekaix_chunker_v1',
            Math.max(0, Math.min(1, deriveParseQualityScore(extraction) / 100)),
          ],
          snowflakeUser,
        );
      }

      return chunks.length;
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) return 0;
      throw err;
    }
  }

  async function refreshDocumentEntitiesAndFacts(
    dataProductId: string,
    documentId: string,
    extraction: DocumentExtractionResult,
    evidenceSummary: EvidenceSummary,
    snowflakeUser: string,
  ): Promise<{ entities: number; facts: number; links: number }> {
    try {
      await postgresService.query(
        `DELETE FROM doc_entities
         WHERE data_product_id = $1::uuid
           AND document_id = $2::uuid`,
        [dataProductId, documentId],
        snowflakeUser,
      );
      await postgresService.query(
        `DELETE FROM doc_facts
         WHERE data_product_id = $1::uuid
           AND document_id = $2::uuid`,
        [dataProductId, documentId],
        snowflakeUser,
      );

      if (extraction.extractionStatus !== 'completed') {
        return { entities: 0, facts: 0, links: 0 };
      }

      const confidence = Math.max(0, Math.min(1, deriveParseQualityScore(extraction) / 100));
      const metadataJson = JSON.stringify({
        extraction_method: extraction.extractionMethod,
        extraction_warnings: extraction.extractionWarnings,
      });

      let entityCount = 0;
      let factCount = 0;
      const insertedFacts: InsertedDocumentFact[] = [];

      const addEntity = async (entityType: string, canonicalValue: string): Promise<void> => {
        await postgresService.query(
          `INSERT INTO doc_entities
             (id, data_product_id, document_id, entity_type, canonical_value, raw_value, confidence, metadata)
           VALUES
             ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8::jsonb)`,
          [
            crypto.randomUUID(),
            dataProductId,
            documentId,
            entityType,
            canonicalValue,
            canonicalValue,
            confidence,
            metadataJson,
          ],
          snowflakeUser,
        );
        entityCount += 1;
      };

      for (const tableName of evidenceSummary.table_names.slice(0, 30)) {
        await addEntity('table_reference', tableName);
      }
      for (const relationshipHint of evidenceSummary.relationship_hints.slice(0, 30)) {
        await addEntity('relationship_reference', relationshipHint);
      }
      for (const metricHint of evidenceSummary.metric_hints.slice(0, 30)) {
        await addEntity('metric_hint', metricHint);
      }

      for (const metricHint of evidenceSummary.metric_hints.slice(0, 30)) {
        const factId = crypto.randomUUID();
        await postgresService.query(
          `INSERT INTO doc_facts
             (id, data_product_id, document_id, fact_type, subject_key, predicate,
              object_value, confidence, metadata)
           VALUES
             ($1::uuid, $2::uuid, $3::uuid, 'metric_hint', $4, 'describes_metric', $5, $6, $7::jsonb)`,
          [
            factId,
            dataProductId,
            documentId,
            metricHint.slice(0, 120),
            metricHint,
            confidence,
            metadataJson,
          ],
          snowflakeUser,
        );
        insertedFacts.push({
          id: factId,
          factType: 'metric_hint',
          subjectKey: metricHint.slice(0, 120),
          objectValue: metricHint,
          numericValue: null,
        });
        factCount += 1;
      }

      const monetaryFacts = extractMonetaryFacts(extraction.extractedText);
      for (const fact of monetaryFacts) {
        const factId = crypto.randomUUID();
        await postgresService.query(
          `INSERT INTO doc_facts
             (id, data_product_id, document_id, fact_type, subject_key, predicate, object_value,
              numeric_value, currency, confidence, metadata)
           VALUES
             ($1::uuid, $2::uuid, $3::uuid, 'monetary_amount', 'document_amount', 'reported_amount',
              $4, $5, $6, $7, $8::jsonb)`,
          [
            factId,
            dataProductId,
            documentId,
            String(fact.value),
            fact.value,
            fact.currency,
            confidence,
            metadataJson,
          ],
          snowflakeUser,
        );
        insertedFacts.push({
          id: factId,
          factType: 'monetary_amount',
          subjectKey: 'document_amount',
          objectValue: String(fact.value),
          numericValue: fact.value,
        });
        factCount += 1;
      }

      const linkCount = await refreshDocumentFactLinks(
        dataProductId,
        documentId,
        insertedFacts,
        extraction.extractedText,
        snowflakeUser,
      );

      return { entities: entityCount, facts: factCount, links: linkCount };
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) return { entities: 0, facts: 0, links: 0 };
      throw err;
    }
  }

  async function refreshDocumentFactLinks(
    dataProductId: string,
    documentId: string,
    insertedFacts: InsertedDocumentFact[],
    extractedText: string | null,
    snowflakeUser: string,
  ): Promise<number> {
    try {
      await postgresService.query(
        `DELETE FROM doc_fact_links l
         USING doc_facts f
         WHERE l.fact_id = f.id
           AND f.data_product_id = $1::uuid
           AND f.document_id = $2::uuid`,
        [dataProductId, documentId],
        snowflakeUser,
      );

      if (insertedFacts.length === 0) return 0;

      let tableAnchors: string[] = [];
      try {
        const dpRows = await postgresService.query(
          `SELECT tables
           FROM data_products
           WHERE id = $1::uuid`,
          [dataProductId],
          snowflakeUser,
        );
        const rawTables = (dpRows.rows[0] as { tables?: unknown } | undefined)?.tables;
        tableAnchors = normalizeTableAnchors(rawTables);
      } catch (err) {
        if (!isRecoverableContextSchemaError(err)) throw err;
      }

      const mentions = extractKeyMentions(extractedText).slice(0, 60);
      let linked = 0;

      for (const fact of insertedFacts.slice(0, 200)) {
        const candidates = buildFactLinkCandidates(fact, mentions, tableAnchors);
        for (const candidate of candidates) {
          await postgresService.query(
            `INSERT INTO doc_fact_links
               (id, data_product_id, fact_id, target_domain, target_key, link_reason, link_confidence)
             VALUES
               ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7)
             ON CONFLICT (fact_id, target_domain, target_key)
             DO UPDATE
               SET link_reason = EXCLUDED.link_reason,
                   link_confidence = GREATEST(
                     COALESCE(doc_fact_links.link_confidence, 0),
                     COALESCE(EXCLUDED.link_confidence, 0)
                   )`,
            [
              crypto.randomUUID(),
              dataProductId,
              fact.id,
              candidate.targetDomain,
              candidate.targetKey,
              candidate.linkReason,
              candidate.linkConfidence,
            ],
            snowflakeUser,
          );
          linked += 1;
        }
      }

      return linked;
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) return 0;
      throw err;
    }
  }

  async function markRegistryDeleted(
    dataProductId: string,
    documentId: string,
    deletedBy: string,
    snowflakeUser: string,
  ): Promise<void> {
    try {
      await postgresService.query(
        `UPDATE doc_registry
         SET deleted_at = now(),
             metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('deleted_by', $3::text)
         WHERE data_product_id = $1::uuid
           AND document_id = $2::uuid`,
        [dataProductId, documentId, deletedBy],
        snowflakeUser,
      );
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) return;
      throw err;
    }
  }

  /**
   * POST /documents/upload
   * Multipart file upload from any source channel (create flow, chat attachment,
   * documents panel). Stores file in MinIO and records evidence/context metadata.
   */
  app.post('/upload', async (request: FastifyRequest, reply) => {
    const { snowflakeUser } = request.user;
    const data = await request.file();

    if (!data) {
      return reply.status(400).send({
        error: 'VALIDATION_ERROR',
        message: 'No file uploaded. Send a multipart form with a "file" field.',
      });
    }

    const filename = data.filename;
    const contentType = data.mimetype;

    const chunks: Buffer[] = [];
    for await (const chunk of data.file) {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }
    const fileBuffer = Buffer.concat(chunks);

    if (data.file.truncated) {
      return reply.status(413).send({
        error: 'FILE_TOO_LARGE',
        message: `File exceeds maximum size of ${MAX_FILE_SIZE_BYTES / (1024 * 1024)}MB`,
      });
    }

    // Parse multipart metadata only after streaming the file so fields appended after
    // the file part are available for normal-sized uploads.
    const fields = data.fields as Record<string, unknown>;
    const rawUploadInput = {
      data_product_id: readFieldValue(fields['data_product_id']),
      source_channel: readFieldValue(fields['source_channel']),
      user_note: readFieldValue(fields['user_note']),
      auto_activate_step: readFieldValue(fields['auto_activate_step']),
    };

    const parsedUploadInput = uploadDocumentSchema.safeParse(rawUploadInput);
    if (!parsedUploadInput.success) {
      return reply.status(400).send({
        error: 'VALIDATION_ERROR',
        message: 'Invalid upload metadata',
        details: parsedUploadInput.error.flatten().fieldErrors,
      });
    }

    const {
      data_product_id: dataProductId,
      source_channel: sourceChannel,
      user_note: userNote,
      auto_activate_step: autoActivateStep,
    } = parsedUploadInput.data;

    const dpCheck = await postgresService.query(
      'SELECT id FROM data_products WHERE id = $1::uuid',
      [dataProductId],
      snowflakeUser,
    );

    if (dpCheck.rowCount === 0) {
      return reply.status(404).send({
        error: 'NOT_FOUND',
        message: 'Data product not found',
      });
    }

    const fileSize = fileBuffer.length;
    const checksumSha256 = crypto.createHash('sha256').update(fileBuffer).digest('hex');
    const documentId = crypto.randomUUID();
    const minioPath = `${dataProductId}/uploads/${documentId}/${filename}`;

    const docKind = inferDocumentKind(filename, contentType);
    const stepCandidates = inferStepCandidates(docKind, filename);
    const effectiveStepCandidates =
      autoActivateStep && !stepCandidates.includes(autoActivateStep)
        ? [...stepCandidates, autoActivateStep]
        : stepCandidates;
    const extraction = await extractDocumentContent({
      dataProductId,
      documentId,
      filename,
      contentType,
      buffer: fileBuffer,
    });
    const extractedText = extraction.extractedText;
    const evidenceSummary = extractEvidenceSummary(extractedText);
    const summary =
      extraction.summaryHint ?? summarizeDocument(docKind, evidenceSummary, extractedText);
    const extractionStatus = extraction.extractionStatus;
    const extractionError =
      extractionStatus === 'completed'
        ? null
        : summarizeExtractionWarnings(extraction.extractionWarnings);
    const extractedAt = extractionStatus === 'completed' ? new Date().toISOString() : null;

    if (extractionStatus !== 'completed') {
      app.log.warn(
        {
          data_product_id: dataProductId,
          document_id: documentId,
          filename,
          status: extractionStatus,
          extraction_method: extraction.extractionMethod,
          warning_count: extraction.extractionWarnings.length,
        },
        'OPS_ALERT[extraction_failure] upload extraction did not complete',
      );
      await emitOpsAlertEvent({
        dataProductId,
        snowflakeUser,
        signal: 'extraction_failure',
        severity: 'warning',
        message: 'Upload extraction did not complete',
        sourceRoute: 'documents.upload',
        metadata: {
          document_id: documentId,
          filename,
          extraction_status: extractionStatus,
          extraction_method: extraction.extractionMethod,
          warning_count: extraction.extractionWarnings.length,
        },
      });
    }

    await minioService.uploadFile(DOCUMENTS_BUCKET, minioPath, fileBuffer, contentType);

    const contextVersion = await createContextVersion(
      dataProductId,
      snowflakeUser,
      'document_uploaded',
      {
        document_id: documentId,
        source_channel: sourceChannel,
        step_candidates: effectiveStepCandidates,
        extraction_method: extraction.extractionMethod,
        extraction_status: extractionStatus,
      },
    );

    const insertResult = await postgresService.query(
      `INSERT INTO uploaded_documents
         (id, data_product_id, filename, minio_path, file_size_bytes,
          content_type, extraction_status, extraction_error, extracted_content, extracted_at,
          uploaded_by, source_channel, user_note, doc_kind, summary, context_version_id)
       VALUES ($1::uuid, $2::uuid, $3, $4, $5,
               $6, $7, $8, $9, $10,
               $11, $12, $13, $14, $15, $16::uuid)
       RETURNING id, filename, file_size_bytes, content_type, created_at, extraction_status`,
      [
        documentId,
        dataProductId,
        filename,
        minioPath,
        fileSize,
        contentType,
        extractionStatus,
        extractionError,
        extractedText,
        extractedAt,
        snowflakeUser,
        sourceChannel,
        userNote ?? null,
        docKind,
        summary,
        contextVersion?.id ?? null,
      ],
      snowflakeUser,
    );

    const doc = insertResult.rows[0] as
      | {
          id: string;
          filename: string;
          file_size_bytes: number;
          content_type: string;
          created_at: string;
          extraction_status: string;
        }
      | undefined;

    if (!doc) {
      return reply.status(500).send({
        error: 'INTERNAL_ERROR',
        message: 'Failed to create document record',
      });
    }

    const registryId = await syncDocumentRegistry(
      {
        dataProductId,
        documentId,
        filename,
        contentType,
        sourceChannel,
        uploadedBy: snowflakeUser,
        fileSize,
        minioPath,
        checksumSha256,
        extraction,
        userNote: userNote ?? null,
        docKind,
        summary,
        contextVersionId: contextVersion?.id ?? null,
      },
      snowflakeUser,
    );

    if (registryId) {
      await refreshDocumentChunks(
        dataProductId,
        documentId,
        registryId,
        extraction,
        snowflakeUser,
      );
    }

    // Upload chunks to Snowflake for Cortex Search Service (fire-and-forget)
    if (extractionStatus === 'completed' && extractedText) {
      const dpNameRow = await postgresService.query(
        'SELECT name FROM data_products WHERE id = $1::uuid',
        [dataProductId],
        snowflakeUser,
      );
      const dpName = (dpNameRow.rows[0] as Record<string, unknown> | undefined)?.name;
      if (typeof dpName === 'string' && dpName) {
        // Prefer page-aware upload for citation support when pages are available
        if (extraction.pages && extraction.pages.length > 0) {
          void uploadPageAwareChunksToSnowflake(
            dataProductId, dpName, documentId, filename, docKind, extraction.pages,
          ).catch((err) => {
            app.log.warn({ err, document_id: documentId }, 'Page-aware chunk upload failed, falling back');
            void uploadChunksToSnowflake(
              dataProductId, dpName, documentId, filename, docKind, extractedText,
            ).catch((fallbackErr) => {
              app.log.warn({ err: fallbackErr, document_id: documentId }, 'Snowflake chunk upload failed');
            });
          });
        } else {
          void uploadChunksToSnowflake(
            dataProductId, dpName, documentId, filename, docKind, extractedText,
          ).catch((err) => {
            app.log.warn({ err, document_id: documentId }, 'Snowflake chunk upload failed');
          });
        }
      }
    }

    await refreshDocumentEntitiesAndFacts(
      dataProductId,
      documentId,
      extraction,
      evidenceSummary,
      snowflakeUser,
    );

    // Normalize extracted text to Neo4j document knowledge graph (fire-and-forget)
    if (extractionStatus === 'completed' && extractedText) {
      fetch(`${config.AI_SERVICE_URL}/documents/normalize-to-graph`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          data_product_id: dataProductId,
          document_id: documentId,
          extracted_text: extractedText,
          title: filename,
          mime_type: contentType,
        }),
      }).catch((err) => {
        app.log.warn({ err, document_id: documentId }, 'Failed to normalize document to graph');
      });
    }

    let evidenceId: string | null = null;

    try {
      const evidenceResult = await postgresService.query(
        `INSERT INTO document_evidence
           (data_product_id, document_id, evidence_type, step_candidates, impact_scope, payload, provenance)
         VALUES ($1::uuid, $2::uuid, $3, $4::text[], $5::text[], $6::jsonb, $7::jsonb)
         RETURNING id`,
        [
          dataProductId,
          documentId,
          'document_summary',
          effectiveStepCandidates,
          effectiveStepCandidates,
          JSON.stringify(buildEvidencePayload(evidenceSummary, extraction)),
          JSON.stringify({
            source_channel: sourceChannel,
            filename,
            content_type: contentType,
            extraction_method: extraction.extractionMethod,
          }),
        ],
        snowflakeUser,
      );

      evidenceId = (evidenceResult.rows[0] as { id: string } | undefined)?.id ?? null;
    } catch (err) {
      if (!isRecoverableContextSchemaError(err)) {
        throw err;
      }
    }

    if (evidenceId) {
      const activeStep = autoActivateStep
        ? missionStepSchema.safeParse(autoActivateStep).success
          ? (autoActivateStep as MissionStep)
          : undefined
        : undefined;

      for (const step of effectiveStepCandidates) {
        const state: ContextSelectionState = activeStep === step ? 'active' : 'candidate';
        await upsertContextSelection(
          dataProductId,
          step,
          documentId,
          evidenceId,
          state,
          contextVersion?.id ?? null,
          snowflakeUser,
        );
      }
    }

    return reply.status(201).send({
      id: doc.id,
      filename: doc.filename,
      size: doc.file_size_bytes,
      content_type: doc.content_type,
      created_at: doc.created_at,
      extraction_status: doc.extraction_status,
      source_channel: sourceChannel,
      doc_kind: docKind,
      summary,
      extraction_method: extraction.extractionMethod,
      step_candidates: effectiveStepCandidates,
      context_version: contextVersion,
    });
  });

  /**
   * POST /documents/:id/extract
   * Trigger extraction for an existing uploaded document.
   */
  app.post(
    '/:id/extract',
    async (request: FastifyRequest<{ Params: { id: string } }>, reply) => {
      const paramResult = extractDocumentParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid document id',
        });
      }

      const { id } = paramResult.data;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT id, data_product_id, filename, minio_path, content_type, extraction_status, doc_kind
         FROM uploaded_documents
         WHERE id = $1::uuid AND is_deleted = false`,
        [id],
        snowflakeUser,
      );

      const doc = result.rows[0] as
        | {
            id: string;
            data_product_id: string;
            filename: string;
            minio_path: string;
            content_type: string | null;
            extraction_status: string;
            doc_kind: string | null;
          }
        | undefined;

      if (!doc) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Document not found',
        });
      }

      if (doc.extraction_status === 'processing') {
        return reply.status(409).send({
          error: 'CONFLICT',
          message: 'Extraction is already in progress',
        });
      }

      await postgresService.query(
        `UPDATE uploaded_documents
         SET extraction_status = 'processing', extraction_error = NULL
         WHERE id = $1::uuid`,
        [id],
        snowflakeUser,
      );

      try {
        const fileBuffer = await minioService.getFile(DOCUMENTS_BUCKET, doc.minio_path);
        const extraction = await extractDocumentContent({
          dataProductId: doc.data_product_id,
          documentId: doc.id,
          filename: doc.filename,
          contentType: doc.content_type,
          buffer: fileBuffer,
        });
        const extractionError =
          extraction.extractionStatus === 'completed'
            ? null
            : summarizeExtractionWarnings(extraction.extractionWarnings);
        const extractedText = extraction.extractedText;
        const docKind = doc.doc_kind ?? inferDocumentKind(doc.filename, doc.content_type);
        const evidenceSummary = extractEvidenceSummary(extractedText);
        const summary =
          extraction.summaryHint ?? summarizeDocument(docKind, evidenceSummary, extractedText);
        const contextVersion = await createContextVersion(
          doc.data_product_id,
          snowflakeUser,
          'document_reextracted',
          {
            document_id: doc.id,
            extraction_method: extraction.extractionMethod,
            extraction_status: extraction.extractionStatus,
          },
        );

        await postgresService.query(
          `UPDATE uploaded_documents
           SET extraction_status = $2::extraction_status,
               extracted_content = $3,
               extraction_error = $4,
               summary = COALESCE($5, summary),
               context_version_id = $6::uuid,
               extracted_at = CASE
                 WHEN ($2::extraction_status)::text IN ('completed', 'failed') THEN now()
                 ELSE extracted_at
               END
           WHERE id = $1::uuid`,
          [
            id,
            extraction.extractionStatus,
            extractedText,
            extractionError,
            summary,
            contextVersion?.id ?? null,
          ],
          snowflakeUser,
        );

        const checksumSha256 = crypto.createHash('sha256').update(fileBuffer).digest('hex');
        const registryId = await syncDocumentRegistry(
          {
            dataProductId: doc.data_product_id,
            documentId: doc.id,
            filename: doc.filename,
            contentType: doc.content_type,
            sourceChannel: 'reextract',
            uploadedBy: snowflakeUser,
            fileSize: fileBuffer.length,
            minioPath: doc.minio_path,
            checksumSha256,
            extraction,
            userNote: null,
            docKind,
            summary,
            contextVersionId: contextVersion?.id ?? null,
          },
          snowflakeUser,
        );

        if (registryId) {
          await refreshDocumentChunks(
            doc.data_product_id,
            doc.id,
            registryId,
            extraction,
            snowflakeUser,
          );
        }

        await refreshDocumentEntitiesAndFacts(
          doc.data_product_id,
          doc.id,
          extraction,
          evidenceSummary,
          snowflakeUser,
        );

        try {
          await postgresService.query(
            `UPDATE document_evidence
             SET payload = $3::jsonb,
                 provenance = COALESCE(provenance, '{}'::jsonb) || $4::jsonb
             WHERE data_product_id = $1::uuid
               AND document_id = $2::uuid`,
            [
              doc.data_product_id,
              doc.id,
              JSON.stringify(buildEvidencePayload(evidenceSummary, extraction)),
              JSON.stringify({
                extraction_method: extraction.extractionMethod,
                extraction_status: extraction.extractionStatus,
                updated_at: new Date().toISOString(),
              }),
            ],
            snowflakeUser,
          );
        } catch (err) {
          if (!isRecoverableContextSchemaError(err)) {
            throw err;
          }
        }

        try {
          await postgresService.query(
            `UPDATE context_step_selections
             SET context_version_id = $3::uuid,
                 updated_at = now()
             WHERE data_product_id = $1::uuid
               AND document_id = $2::uuid`,
            [doc.data_product_id, doc.id, contextVersion?.id ?? null],
            snowflakeUser,
          );
        } catch (err) {
          if (!isRecoverableContextSchemaError(err)) {
            throw err;
          }
        }

        if (extraction.extractionStatus === 'failed') {
          app.log.warn(
            {
              data_product_id: doc.data_product_id,
              document_id: doc.id,
              filename: doc.filename,
              status: extraction.extractionStatus,
              extraction_method: extraction.extractionMethod,
              warning_count: extraction.extractionWarnings.length,
            },
            'OPS_ALERT[extraction_failure] reextract failed',
          );
          await emitOpsAlertEvent({
            dataProductId: doc.data_product_id,
            snowflakeUser,
            signal: 'extraction_failure',
            severity: 'high',
            message: 'Document re-extraction failed',
            sourceRoute: 'documents.extract',
            metadata: {
              document_id: doc.id,
              filename: doc.filename,
              extraction_status: extraction.extractionStatus,
              extraction_method: extraction.extractionMethod,
              warning_count: extraction.extractionWarnings.length,
            },
          });
          return reply.status(202).send({
            status: 'failed',
            message: extractionError ?? 'Extraction failed',
            extraction_method: extraction.extractionMethod,
            warnings: extraction.extractionWarnings,
            context_version: contextVersion,
          });
        }

        if (extraction.extractionStatus === 'pending') {
          app.log.warn(
            {
              data_product_id: doc.data_product_id,
              document_id: doc.id,
              filename: doc.filename,
              status: extraction.extractionStatus,
              extraction_method: extraction.extractionMethod,
              warning_count: extraction.extractionWarnings.length,
            },
            'OPS_ALERT[extraction_failure] reextract pending without completed output',
          );
          await emitOpsAlertEvent({
            dataProductId: doc.data_product_id,
            snowflakeUser,
            signal: 'extraction_failure',
            severity: 'warning',
            message: 'Document re-extraction remained pending',
            sourceRoute: 'documents.extract',
            metadata: {
              document_id: doc.id,
              filename: doc.filename,
              extraction_status: extraction.extractionStatus,
              extraction_method: extraction.extractionMethod,
              warning_count: extraction.extractionWarnings.length,
            },
          });
          return reply.status(202).send({
            status: 'pending',
            message: extraction.summaryHint ?? 'Extraction did not produce text yet',
            extraction_method: extraction.extractionMethod,
            warnings: extraction.extractionWarnings,
            context_version: contextVersion,
          });
        }

        return reply.status(202).send({
          status: 'completed',
          extracted_chars: extractedText?.length ?? 0,
          extraction_method: extraction.extractionMethod,
          warnings: extraction.extractionWarnings,
          summary,
          context_version: contextVersion,
        });
      } catch (err) {
        app.log.error({ err, documentId: id }, 'Document extraction failed');

        await postgresService.query(
          `UPDATE uploaded_documents
           SET extraction_status = 'failed', extraction_error = $2
           WHERE id = $1::uuid`,
          [id, 'Extraction failed due to processing error'],
          snowflakeUser,
        );

        return reply.status(500).send({
          error: 'EXTRACTION_FAILED',
          message: 'Failed to extract document content',
        });
      }
    },
  );

  /**
   * GET /documents/context/:dataProductId/current
   * Return context selections grouped by mission step.
   */
  app.get(
    '/context/:dataProductId/current',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { step?: string };
      }>,
      reply,
    ) => {
      const paramResult = contextCurrentParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = contextCurrentQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid context query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { step } = queryResult.data;
      const { snowflakeUser } = request.user;

      let latestContextVersion: { id: string; version: number } | null = null;

      try {
        const latestResult = await postgresService.query(
          `SELECT id, version
           FROM context_versions
           WHERE data_product_id = $1::uuid
           ORDER BY version DESC
           LIMIT 1`,
          [dataProductId],
          snowflakeUser,
        );

        const latest = latestResult.rows[0] as { id: string; version: number } | undefined;
        if (latest) {
          latestContextVersion = { id: latest.id, version: Number(latest.version) };
        }
      } catch (err) {
        if (!isRecoverableContextSchemaError(err)) {
          throw err;
        }
      }

      const phaseResult = await postgresService.query(
        `SELECT state->>'current_phase' AS current_phase
         FROM data_products
         WHERE id = $1::uuid`,
        [dataProductId],
        snowflakeUser,
      );

      const currentPhase =
        (phaseResult.rows[0] as { current_phase?: string } | undefined)?.current_phase ?? 'discovery';
      const currentStep = normalizeStepName(currentPhase);

      const steps: Record<
        MissionStep,
        {
          active: Array<Record<string, unknown>>;
          candidate: Array<Record<string, unknown>>;
          reference: Array<Record<string, unknown>>;
          excluded: Array<Record<string, unknown>>;
        }
      > = {
        discovery: { active: [], candidate: [], reference: [], excluded: [] },
        requirements: { active: [], candidate: [], reference: [], excluded: [] },
        modeling: { active: [], candidate: [], reference: [], excluded: [] },
        generation: { active: [], candidate: [], reference: [], excluded: [] },
        validation: { active: [], candidate: [], reference: [], excluded: [] },
        publishing: { active: [], candidate: [], reference: [], excluded: [] },
      };

      try {
        const whereStep = step ? 'AND cs.step_name = $2' : '';
        const params: unknown[] = step ? [dataProductId, step] : [dataProductId];

        const rows = await postgresService.query(
          `SELECT
             cs.step_name,
             cs.state,
             cs.updated_at,
             de.id AS evidence_id,
             de.evidence_type,
             de.payload,
             de.step_candidates,
             de.impact_scope,
             ud.id AS document_id,
             ud.filename,
             ud.doc_kind,
             ud.summary,
             ud.source_channel,
             ud.created_at AS document_created_at
           FROM context_step_selections cs
           JOIN document_evidence de ON de.id = cs.evidence_id
           JOIN uploaded_documents ud ON ud.id = cs.document_id
           WHERE cs.data_product_id = $1::uuid
             ${whereStep}
             AND ud.is_deleted = false
           ORDER BY cs.step_name, cs.updated_at DESC`,
          params,
          snowflakeUser,
        );

        for (const rawRow of rows.rows) {
          const row = rawRow as {
            step_name: string;
            state: ContextSelectionState;
            updated_at: string;
            evidence_id: string;
            evidence_type: string;
            payload: Record<string, unknown>;
            step_candidates: string[];
            impact_scope: string[];
            document_id: string;
            filename: string;
            doc_kind: string | null;
            summary: string | null;
            source_channel: string;
            document_created_at: string;
          };

          const stepName = normalizeStepName(row.step_name);
          const state = row.state;
          if (!steps[stepName]) continue;

          const item = {
            evidence_id: row.evidence_id,
            evidence_type: row.evidence_type,
            payload: row.payload,
            step_candidates: row.step_candidates,
            impact_scope: row.impact_scope,
            document: {
              id: row.document_id,
              filename: row.filename,
              doc_kind: row.doc_kind,
              summary: row.summary,
              source_channel: row.source_channel,
              created_at: row.document_created_at,
            },
            updated_at: row.updated_at,
          };

          steps[stepName][state].push(item);
        }
      } catch (err) {
        if (!isRecoverableContextSchemaError(err)) {
          throw err;
        }
      }

      if (step) {
        const parsedStep = missionStepSchema.safeParse(step);
        if (!parsedStep.success) {
          return reply.status(400).send({
            error: 'VALIDATION_ERROR',
            message: 'Invalid mission step',
          });
        }

        return reply.send({
          data_product_id: dataProductId,
          current_step: currentStep,
          requested_step: parsedStep.data,
          context_version: latestContextVersion,
          step: {
            [parsedStep.data]: steps[parsedStep.data],
          },
        });
      }

      return reply.send({
        data_product_id: dataProductId,
        current_step: currentStep,
        context_version: latestContextVersion,
        steps,
      });
    },
  );

  /**
   * GET /documents/context/:dataProductId/delta
   * Return context version changes between two points.
   */
  app.get(
    '/context/:dataProductId/delta',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { from_version?: number; to_version?: number };
      }>,
      reply,
    ) => {
      const paramResult = contextDeltaParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = contextDeltaQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid delta query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { from_version: fromVersionRaw, to_version: toVersionRaw } = queryResult.data;
      const { snowflakeUser } = request.user;

      try {
        const versionRows = await postgresService.query(
          `SELECT id, version, reason, changed_by, change_summary, created_at
           FROM context_versions
           WHERE data_product_id = $1::uuid
           ORDER BY version DESC
           LIMIT 100`,
          [dataProductId],
          snowflakeUser,
        );

        const versions = versionRows.rows as Array<{
          id: string;
          version: number;
          reason: string;
          changed_by: string;
          change_summary: Record<string, unknown>;
          created_at: string;
        }>;

        if (versions.length === 0) {
          return reply.send({
            data_product_id: dataProductId,
            from_version: null,
            to_version: null,
            changes: [],
            impacted_steps: [],
            stale_artifacts: [],
            recommended_actions: [],
          });
        }

        const maxVersion = Number(versions[0]?.version ?? 1);
        const toVersion = toVersionRaw ?? maxVersion;
        const fromVersion = fromVersionRaw ?? Math.max(1, toVersion - 1);

        const bounded = versions
          .filter((row) => Number(row.version) >= fromVersion && Number(row.version) <= toVersion)
          .sort((a, b) => Number(a.version) - Number(b.version));

        const impactedStepHints: Array<string | MissionStep> = [];
        for (const row of bounded) {
          let summary: Record<string, unknown> = {};
          if (row.change_summary && typeof row.change_summary === 'object') {
            summary = row.change_summary;
          } else if (typeof row.change_summary === 'string') {
            try {
              const parsed = JSON.parse(row.change_summary);
              if (parsed && typeof parsed === 'object') {
                summary = parsed as Record<string, unknown>;
              }
            } catch {
              summary = {};
            }
          }
          impactedStepHints.push(...collectStepsFromChangeSummary(summary));
        }

        const contextImpact = await buildContextImpactSummary(
          dataProductId,
          snowflakeUser,
          impactedStepHints,
          toVersion,
        );

        return reply.send({
          data_product_id: dataProductId,
          from_version: fromVersion,
          to_version: toVersion,
          changes: bounded,
          impacted_steps: contextImpact.impactedSteps,
          stale_artifacts: contextImpact.staleArtifacts,
          recommended_actions: contextImpact.recommendedActions,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.send({
            data_product_id: dataProductId,
            from_version: null,
            to_version: null,
            changes: [],
            impacted_steps: [],
            stale_artifacts: [],
            recommended_actions: [],
            note: 'Context version tables are not available yet. Apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * POST /documents/context/:dataProductId/apply
   * User-driven activation/exclusion updates per mission step.
   */
  app.post(
    '/context/:dataProductId/apply',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Body: {
          step: MissionStep;
          reason?: string;
          updates: Array<{ evidence_id: string; state: ContextSelectionState }>;
        };
      }>,
      reply,
    ) => {
      const paramResult = applyContextParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const bodyResult = applyContextSchema.safeParse(request.body);
      if (!bodyResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid context update payload',
          details: bodyResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { step, reason, updates } = bodyResult.data;
      const { snowflakeUser } = request.user;

      try {
        const evidenceIds = updates.map((update) => update.evidence_id);
        const evidenceRows = await postgresService.query(
          `SELECT de.id, de.document_id
           FROM document_evidence de
           JOIN uploaded_documents ud ON ud.id = de.document_id
           WHERE de.data_product_id = $1::uuid
             AND de.id = ANY($2::uuid[])
             AND ud.is_deleted = false`,
          [dataProductId, evidenceIds],
          snowflakeUser,
        );

        const evidenceMap = new Map<string, string>();
        for (const row of evidenceRows.rows as Array<{ id: string; document_id: string }>) {
          evidenceMap.set(row.id, row.document_id);
        }

        const missing = evidenceIds.filter((id) => !evidenceMap.has(id));
        if (missing.length > 0) {
          return reply.status(400).send({
            error: 'VALIDATION_ERROR',
            message: 'One or more evidence ids are invalid for this data product',
            details: { missing_evidence_ids: missing },
          });
        }

        const contextVersion = await createContextVersion(
          dataProductId,
          snowflakeUser,
          'context_applied',
          {
            step,
            updates: updates.map((update) => ({
              evidence_id: update.evidence_id,
              state: update.state,
            })),
            reason: reason ?? null,
          },
        );

        for (const update of updates) {
          const documentId = evidenceMap.get(update.evidence_id);
          if (!documentId) continue;

          await upsertContextSelection(
            dataProductId,
            step,
            documentId,
            update.evidence_id,
            update.state,
            contextVersion?.id ?? null,
            snowflakeUser,
          );
        }

        return reply.send({
          data_product_id: dataProductId,
          step,
          applied: updates.length,
          context_version: contextVersion,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.status(409).send({
            error: 'CONTEXT_SCHEMA_MISSING',
            message: 'Document context tables are not available yet. Apply migration first.',
          });
        }

        throw err;
      }
    },
  );

  /**
   * GET /documents/semantic/:dataProductId/registry
   * Canonical document registry including extraction diagnostics and version info.
   */
  app.get(
    '/semantic/:dataProductId/registry',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { include_deleted?: boolean };
      }>,
      reply,
    ) => {
      const paramResult = semanticRegistryParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = semanticRegistryQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid semantic registry query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { include_deleted: includeDeleted } = queryResult.data;
      const { snowflakeUser } = request.user;

      try {
        const result = await postgresService.query(
          `SELECT
             dr.id AS registry_id,
             dr.document_id,
             dr.source_system,
             dr.source_uri,
             dr.title,
             dr.mime_type,
             dr.checksum_sha256,
             dr.version_id,
             dr.uploaded_by,
             dr.uploaded_at,
             dr.deleted_at,
             dr.extraction_status,
             dr.extraction_method,
             dr.parse_quality_score,
             dr.extraction_diagnostics,
             dr.metadata,
             dr.updated_at,
             ud.filename,
             ud.source_channel,
             ud.doc_kind,
             ud.summary,
             ud.context_version_id
           FROM doc_registry dr
           LEFT JOIN uploaded_documents ud
             ON ud.id = dr.document_id
           WHERE dr.data_product_id = $1::uuid
             AND ($2::boolean OR dr.deleted_at IS NULL)
           ORDER BY dr.updated_at DESC`,
          [dataProductId, includeDeleted],
          snowflakeUser,
        );

        return reply.send({
          data_product_id: dataProductId,
          data: result.rows,
        });
      } catch (err) {
        if (!isRecoverableContextSchemaError(err)) {
          throw err;
        }

        // Backward-compatible fallback from uploaded_documents.
        const fallback = await postgresService.query(
          `SELECT
             id AS document_id,
             filename,
             content_type AS mime_type,
             extraction_status,
             uploaded_by,
             created_at AS uploaded_at,
             is_deleted,
             deleted_at,
             source_channel,
             doc_kind,
             summary,
             extraction_error,
             context_version_id
           FROM uploaded_documents
           WHERE data_product_id = $1::uuid
             AND ($2::boolean OR is_deleted = false)
           ORDER BY created_at DESC`,
          [dataProductId, includeDeleted],
          snowflakeUser,
        );

        const mapped = fallback.rows.map((row) => ({
          registry_id: null,
          document_id: (row as { document_id: string }).document_id,
          source_system: 'uploaded_documents_fallback',
          source_uri: null,
          title: (row as { filename: string }).filename,
          mime_type: (row as { mime_type: string | null }).mime_type,
          checksum_sha256: null,
          version_id: 1,
          uploaded_by: (row as { uploaded_by: string }).uploaded_by,
          uploaded_at: (row as { uploaded_at: string }).uploaded_at,
          deleted_at: (row as { deleted_at: string | null }).deleted_at,
          extraction_status: (row as { extraction_status: string }).extraction_status,
          extraction_method: null,
          parse_quality_score: null,
          extraction_diagnostics: {
            status: (row as { extraction_status: string }).extraction_status,
            extraction_error: (row as { extraction_error: string | null }).extraction_error,
            fallback: true,
          },
          metadata: {
            source_channel: (row as { source_channel: string | null }).source_channel,
            doc_kind: (row as { doc_kind: string | null }).doc_kind,
            summary: (row as { summary: string | null }).summary,
            context_version_id: (row as { context_version_id: string | null }).context_version_id,
          },
          updated_at: (row as { uploaded_at: string }).uploaded_at,
          filename: (row as { filename: string }).filename,
          source_channel: (row as { source_channel: string | null }).source_channel,
          doc_kind: (row as { doc_kind: string | null }).doc_kind,
          summary: (row as { summary: string | null }).summary,
          context_version_id: (row as { context_version_id: string | null }).context_version_id,
        }));

        return reply.send({
          data_product_id: dataProductId,
          data: mapped,
          fallback: true,
          note: 'doc_registry migration not applied yet; serving fallback registry view.',
        });
      }
    },
  );

  /**
   * GET /documents/semantic/:dataProductId/chunks
   * Browse normalized text chunks used for document retrieval.
   */
  app.get(
    '/semantic/:dataProductId/chunks',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { document_id?: string; limit?: number; offset?: number };
      }>,
      reply,
    ) => {
      const paramResult = semanticChunksParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = semanticChunksQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid semantic chunks query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { document_id: documentId, limit, offset } = queryResult.data;
      const { snowflakeUser } = request.user;

      const params: unknown[] = [dataProductId];
      const where: string[] = ['c.data_product_id = $1::uuid'];

      if (documentId) {
        params.push(documentId);
        where.push(`c.document_id = $${params.length}::uuid`);
      }

      params.push(limit);
      const limitPos = params.length;
      params.push(offset);
      const offsetPos = params.length;

      try {
        const result = await postgresService.query(
          `SELECT
             c.id,
             c.document_id,
             c.chunk_seq,
             c.section_path,
             c.page_no,
             c.chunk_text,
             c.parser_version,
             c.extraction_confidence,
             c.created_at,
             ud.filename
           FROM doc_chunks c
           JOIN uploaded_documents ud
             ON ud.id = c.document_id
            AND ud.is_deleted = false
           WHERE ${where.join(' AND ')}
           ORDER BY c.document_id, c.chunk_seq
           LIMIT $${limitPos}
           OFFSET $${offsetPos}`,
          params,
          snowflakeUser,
        );

        return reply.send({
          data_product_id: dataProductId,
          limit,
          offset,
          data: result.rows,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.send({
            data_product_id: dataProductId,
            limit,
            offset,
            data: [],
            note: 'doc_chunks table not available yet; apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/semantic/:dataProductId/facts
   * Browse normalized document facts for exact-value lookup workflows.
   */
  app.get(
    '/semantic/:dataProductId/facts',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { fact_type?: string; document_id?: string; limit?: number; offset?: number };
      }>,
      reply,
    ) => {
      const paramResult = semanticFactsParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = semanticFactsQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid semantic facts query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { fact_type: factType, document_id: documentId, limit, offset } = queryResult.data;
      const { snowflakeUser } = request.user;

      const params: unknown[] = [dataProductId];
      const where: string[] = ['f.data_product_id = $1::uuid'];

      if (factType) {
        params.push(factType);
        where.push(`f.fact_type = $${params.length}`);
      }
      if (documentId) {
        params.push(documentId);
        where.push(`f.document_id = $${params.length}::uuid`);
      }

      params.push(limit);
      const limitPos = params.length;
      params.push(offset);
      const offsetPos = params.length;

      try {
        const result = await postgresService.query(
          `SELECT
             f.id,
             f.document_id,
             f.fact_type,
             f.subject_key,
             f.predicate,
             f.object_value,
             f.object_unit,
             f.numeric_value,
             f.event_time,
             f.currency,
             f.confidence,
             f.source_page,
             f.metadata,
             f.created_at,
             ud.filename,
             COALESCE(
               json_agg(
                 jsonb_build_object(
                   'target_domain', l.target_domain,
                   'target_key', l.target_key,
                   'link_reason', l.link_reason,
                   'link_confidence', l.link_confidence
                 )
               ) FILTER (WHERE l.id IS NOT NULL),
               '[]'::json
             ) AS links
           FROM doc_facts f
           LEFT JOIN doc_fact_links l
             ON l.fact_id = f.id
           JOIN uploaded_documents ud
             ON ud.id = f.document_id
            AND ud.is_deleted = false
           WHERE ${where.join(' AND ')}
           GROUP BY f.id, ud.filename
           ORDER BY f.created_at DESC
           LIMIT $${limitPos}
           OFFSET $${offsetPos}`,
          params,
          snowflakeUser,
        );

        return reply.send({
          data_product_id: dataProductId,
          limit,
          offset,
          data: result.rows,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.send({
            data_product_id: dataProductId,
            limit,
            offset,
            data: [],
            note: 'doc_facts table not available yet; apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/semantic/:dataProductId/evidence
   * Retrieve answer-evidence packets persisted for auditability.
   */
  app.get(
    '/semantic/:dataProductId/evidence',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { query_id?: string; limit?: number; offset?: number };
      }>,
      reply,
    ) => {
      const paramResult = semanticEvidenceParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = semanticEvidenceQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid semantic evidence query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { query_id: queryId, limit, offset } = queryResult.data;
      const { snowflakeUser } = request.user;

      const params: unknown[] = [dataProductId];
      const where: string[] = ['data_product_id = $1::uuid'];
      if (queryId) {
        params.push(queryId);
        where.push(`query_id = $${params.length}`);
      }

      params.push(limit);
      const limitPos = params.length;
      params.push(offset);
      const offsetPos = params.length;

      try {
        const result = await postgresService.query(
          `SELECT
             id,
             query_id,
             answer_id,
             source_mode,
             confidence,
             exactness_state,
             tool_calls,
             sql_refs,
             fact_refs,
             chunk_refs,
             conflicts,
             recovery_plan,
             final_decision,
             created_by,
             created_at
           FROM qa_evidence
           WHERE ${where.join(' AND ')}
           ORDER BY created_at DESC
           LIMIT $${limitPos}
           OFFSET $${offsetPos}`,
          params,
          snowflakeUser,
        );

        return reply.send({
          data_product_id: dataProductId,
          limit,
          offset,
          data: result.rows,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.send({
            data_product_id: dataProductId,
            limit,
            offset,
            data: [],
            note: 'qa_evidence table not available yet; apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/semantic/:dataProductId/evidence/link
   * Resolve stable deep-link context for a citation reference.
   */
  app.get(
    '/semantic/:dataProductId/evidence/link',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { citation_type: 'sql' | 'document_fact' | 'document_chunk'; reference_id: string; query_id?: string };
      }>,
      reply,
    ) => {
      const paramResult = semanticEvidenceLinkParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = semanticEvidenceLinkQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid evidence-link query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const {
        citation_type: citationType,
        reference_id: referenceId,
        query_id: queryId,
      } = queryResult.data;
      const { snowflakeUser } = request.user;

      async function logCrossTenantCitationProbe(): Promise<void> {
        try {
          if (citationType === 'sql') {
            const probe = await postgresService.query(
              `SELECT data_product_id
               FROM qa_evidence
               WHERE EXISTS (
                 SELECT 1
                 FROM jsonb_array_elements(sql_refs) AS ref
                 WHERE ref->>'reference_id' = $1
               )
                 AND ($2::text IS NULL OR query_id = $2)
               ORDER BY created_at DESC
               LIMIT 1`,
              [referenceId, queryId ?? null],
              snowflakeUser,
            );
            const actualProductId = String(
              (probe.rows[0] as { data_product_id?: string } | undefined)?.data_product_id ?? '',
            );
            if (actualProductId && actualProductId !== dataProductId) {
              app.log.error(
                {
                  user: snowflakeUser,
                  requested_data_product_id: dataProductId,
                  actual_data_product_id: actualProductId,
                  citation_type: citationType,
                  reference_id: referenceId,
                  query_id: queryId ?? null,
                },
                'OPS_ALERT[cross_tenant_query_violation] sql citation probe resolved to different data product',
              );
              await emitOpsAlertEvent({
                dataProductId,
                snowflakeUser,
                signal: 'cross_tenant_query_violation',
                severity: 'critical',
                message: 'SQL citation probe resolved to different data product',
                sourceRoute: 'documents.semantic.evidence.link',
                queryId: queryId ?? null,
                metadata: {
                  citation_type: citationType,
                  reference_id: referenceId,
                  requested_data_product_id: dataProductId,
                  actual_data_product_id: actualProductId,
                },
              });
            }
            return;
          }

          if (citationType === 'document_fact') {
            const probe = await postgresService.query(
              `SELECT data_product_id
               FROM doc_facts
               WHERE id = $1::uuid
               LIMIT 1`,
              [referenceId],
              snowflakeUser,
            );
            const actualProductId = String(
              (probe.rows[0] as { data_product_id?: string } | undefined)?.data_product_id ?? '',
            );
            if (actualProductId && actualProductId !== dataProductId) {
              app.log.error(
                {
                  user: snowflakeUser,
                  requested_data_product_id: dataProductId,
                  actual_data_product_id: actualProductId,
                  citation_type: citationType,
                  reference_id: referenceId,
                },
                'OPS_ALERT[cross_tenant_query_violation] document_fact probe resolved to different data product',
              );
              await emitOpsAlertEvent({
                dataProductId,
                snowflakeUser,
                signal: 'cross_tenant_query_violation',
                severity: 'critical',
                message: 'Document fact probe resolved to different data product',
                sourceRoute: 'documents.semantic.evidence.link',
                queryId: queryId ?? null,
                metadata: {
                  citation_type: citationType,
                  reference_id: referenceId,
                  requested_data_product_id: dataProductId,
                  actual_data_product_id: actualProductId,
                },
              });
            }
            return;
          }

          const probe = await postgresService.query(
            `SELECT data_product_id
             FROM doc_chunks
             WHERE id = $1::uuid
             LIMIT 1`,
            [referenceId],
            snowflakeUser,
          );
          const actualProductId = String(
            (probe.rows[0] as { data_product_id?: string } | undefined)?.data_product_id ?? '',
          );
          if (actualProductId && actualProductId !== dataProductId) {
            app.log.error(
              {
                user: snowflakeUser,
                requested_data_product_id: dataProductId,
                actual_data_product_id: actualProductId,
                citation_type: citationType,
                reference_id: referenceId,
              },
              'OPS_ALERT[cross_tenant_query_violation] document_chunk probe resolved to different data product',
            );
            await emitOpsAlertEvent({
              dataProductId,
              snowflakeUser,
              signal: 'cross_tenant_query_violation',
              severity: 'critical',
              message: 'Document chunk probe resolved to different data product',
              sourceRoute: 'documents.semantic.evidence.link',
              queryId: queryId ?? null,
              metadata: {
                citation_type: citationType,
                reference_id: referenceId,
                requested_data_product_id: dataProductId,
                actual_data_product_id: actualProductId,
              },
            });
          }
        } catch (probeError) {
          if (!isRecoverableContextSchemaError(probeError)) {
            app.log.debug(
              { err: probeError, citationType, referenceId },
              'Evidence-link cross-tenant probe failed',
            );
          }
        }
      }

      try {
        if (citationType === 'sql') {
          const rows = await postgresService.query(
            `SELECT id, query_id, answer_id, created_at, sql_refs, tool_calls
             FROM qa_evidence
             WHERE data_product_id = $1::uuid
               AND ($2::text IS NULL OR query_id = $2)
               AND EXISTS (
                 SELECT 1
                 FROM jsonb_array_elements(sql_refs) AS ref
                 WHERE ref->>'reference_id' = $3
               )
             ORDER BY created_at DESC
             LIMIT 1`,
            [dataProductId, queryId ?? null, referenceId],
            snowflakeUser,
          );

          const row = rows.rows[0] as
            | {
                id: string;
                query_id: string;
                answer_id: string | null;
                created_at: string;
                sql_refs: unknown;
                tool_calls: unknown;
              }
            | undefined;

          if (!row) {
            await logCrossTenantCitationProbe();
            return reply.status(404).send({
              error: 'NOT_FOUND',
              message: 'No SQL citation context found for the requested reference',
              citation_type: citationType,
              reference_id: referenceId,
            });
          }

          const refs = asJsonArray(row.sql_refs);
          const citation = refs.find((ref) => String(ref['reference_id'] ?? '') === referenceId) ?? null;
          return reply.send({
            data_product_id: dataProductId,
            citation_type: citationType,
            reference_id: referenceId,
            deep_link_id: `${citationType}:${referenceId}`,
            resolved: true,
            source: {
              evidence_id: row.id,
              query_id: row.query_id,
              answer_id: row.answer_id,
              created_at: row.created_at,
              citation,
              model_version_hash: inferModelVersionHash(row.tool_calls),
            },
          });
        }

        if (citationType === 'document_fact') {
          const rows = await postgresService.query(
            `SELECT
               f.id,
               f.document_id,
               f.fact_type,
               f.subject_key,
               f.predicate,
               f.object_value,
               f.object_unit,
               f.numeric_value,
               f.currency,
               f.event_time,
               f.source_page,
               f.metadata,
               f.created_at,
               ud.filename,
               COALESCE(
                 json_agg(
                   jsonb_build_object(
                     'target_domain', l.target_domain,
                     'target_key', l.target_key,
                     'link_reason', l.link_reason,
                     'link_confidence', l.link_confidence
                   )
                 ) FILTER (WHERE l.id IS NOT NULL),
                 '[]'::json
               ) AS links
             FROM doc_facts f
             LEFT JOIN doc_fact_links l
               ON l.fact_id = f.id
             JOIN uploaded_documents ud
               ON ud.id = f.document_id
             WHERE f.data_product_id = $1::uuid
               AND f.id = $2::uuid
             GROUP BY f.id, ud.filename
             LIMIT 1`,
            [dataProductId, referenceId],
            snowflakeUser,
          );

          if (rows.rowCount === 0) {
            await logCrossTenantCitationProbe();
            return reply.status(404).send({
              error: 'NOT_FOUND',
              message: 'No document fact found for the requested reference',
              citation_type: citationType,
              reference_id: referenceId,
            });
          }

          return reply.send({
            data_product_id: dataProductId,
            citation_type: citationType,
            reference_id: referenceId,
            deep_link_id: `${citationType}:${referenceId}`,
            resolved: true,
            source: rows.rows[0],
          });
        }

        const rows = await postgresService.query(
          `SELECT
             c.id,
             c.document_id,
             c.chunk_seq,
             c.section_path,
             c.page_no,
             c.chunk_text,
             c.parser_version,
             c.extraction_confidence,
             c.created_at,
             ud.filename
           FROM doc_chunks c
           JOIN uploaded_documents ud ON ud.id = c.document_id
           WHERE c.data_product_id = $1::uuid
             AND c.id = $2::uuid
           LIMIT 1`,
          [dataProductId, referenceId],
          snowflakeUser,
        );

        if (rows.rowCount === 0) {
          await logCrossTenantCitationProbe();
          return reply.status(404).send({
            error: 'NOT_FOUND',
            message: 'No document chunk found for the requested reference',
            citation_type: citationType,
            reference_id: referenceId,
          });
        }

        return reply.send({
          data_product_id: dataProductId,
          citation_type: citationType,
          reference_id: referenceId,
          deep_link_id: `${citationType}:${referenceId}`,
          resolved: true,
          source: rows.rows[0],
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.status(409).send({
            error: 'CONTEXT_SCHEMA_MISSING',
            message: 'Evidence deep-link tables are not available yet. Apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/semantic/:dataProductId/audit
   * Compliance-focused answer trace retrieval with model hash and evidence ids.
   */
  app.get(
    '/semantic/:dataProductId/audit',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { query_id?: string; final_decision?: string; limit?: number; offset?: number };
      }>,
      reply,
    ) => {
      const paramResult = semanticAuditParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = semanticAuditQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid audit query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { query_id: queryId, final_decision: finalDecision, limit, offset } = queryResult.data;
      const { snowflakeUser } = request.user;

      const params: unknown[] = [dataProductId];
      const where: string[] = ['data_product_id = $1::uuid'];
      if (queryId) {
        params.push(queryId);
        where.push(`query_id = $${params.length}`);
      }
      if (finalDecision) {
        params.push(finalDecision);
        where.push(`final_decision = $${params.length}`);
      }
      params.push(limit);
      const limitPos = params.length;
      params.push(offset);
      const offsetPos = params.length;

      try {
        const result = await postgresService.query(
          `SELECT
             id,
             query_id,
             answer_id,
             source_mode,
             confidence,
             exactness_state,
             tool_calls,
             sql_refs,
             fact_refs,
             chunk_refs,
             conflicts,
             recovery_plan,
             final_decision,
             created_by,
             created_at
           FROM qa_evidence
           WHERE ${where.join(' AND ')}
           ORDER BY created_at DESC
           LIMIT $${limitPos}
           OFFSET $${offsetPos}`,
          params,
          snowflakeUser,
        );

        const data = result.rows.map((rawRow) => {
          const row = rawRow as Record<string, unknown>;
          const sqlRefs = asJsonArray(row['sql_refs']);
          const factRefs = asJsonArray(row['fact_refs']);
          const chunkRefs = asJsonArray(row['chunk_refs']);
          const toolCalls = asJsonArray(row['tool_calls']);
          const evidenceIds = [
            ...sqlRefs.map((ref) => String(ref['reference_id'] ?? '')).filter((value) => value.length > 0),
            ...factRefs.map((ref) => String(ref['reference_id'] ?? '')).filter((value) => value.length > 0),
            ...chunkRefs.map((ref) => String(ref['reference_id'] ?? '')).filter((value) => value.length > 0),
          ];

          return {
            ...row,
            model_version_hash: inferModelVersionHash(toolCalls),
            evidence_ids: Array.from(new Set(evidenceIds)),
            tool_calls: toolCalls,
            sql_refs: sqlRefs,
            fact_refs: factRefs,
            chunk_refs: chunkRefs,
          };
        });

        return reply.send({
          data_product_id: dataProductId,
          limit,
          offset,
          data,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.send({
            data_product_id: dataProductId,
            limit,
            offset,
            data: [],
            note: 'qa_evidence table not available yet; apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/semantic/:dataProductId/ops/summary
   * Dashboard-friendly operational summary for alert and SLO tracking.
   */
  app.get(
    '/semantic/:dataProductId/ops/summary',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { window_hours?: number };
      }>,
      reply,
    ) => {
      const paramResult = semanticOpsSummaryParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = semanticOpsSummaryQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid ops summary query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { window_hours: windowHours } = queryResult.data;
      const { snowflakeUser } = request.user;

      try {
        const summaryResult = await postgresService.query(
          `SELECT
             COUNT(*)::int AS total_answers,
             SUM(
               CASE
                 WHEN final_decision IN ('abstained_missing_evidence', 'abstained_conflicting_evidence')
                 THEN 1
                 ELSE 0
               END
             )::int AS abstained_answers,
             SUM(
               CASE
                 WHEN final_decision = 'abstained_conflicting_evidence' THEN 1 ELSE 0
               END
             )::int AS conflicting_answers,
             SUM(
               CASE
                 WHEN final_decision IN ('answer_ready', 'answer_with_warnings')
                   AND (
                     COALESCE(jsonb_array_length(sql_refs), 0) +
                     COALESCE(jsonb_array_length(fact_refs), 0) +
                     COALESCE(jsonb_array_length(chunk_refs), 0)
                   ) = 0
                 THEN 1
                 ELSE 0
               END
             )::int AS citation_missing_answers,
             AVG(
               COALESCE(jsonb_array_length(sql_refs), 0) +
               COALESCE(jsonb_array_length(fact_refs), 0) +
               COALESCE(jsonb_array_length(chunk_refs), 0)
             )::numeric AS avg_citations,
             AVG(COALESCE(jsonb_array_length(tool_calls), 0))::numeric AS avg_tool_calls
           FROM qa_evidence
           WHERE data_product_id = $1::uuid
             AND created_at >= NOW() - ($2::text || ' hours')::interval`,
          [dataProductId, String(windowHours)],
          snowflakeUser,
        );

        const rawRow = (summaryResult.rows[0] ?? {}) as Record<string, unknown>;
        const toInt = (value: unknown): number => {
          const parsed = Number(value ?? 0);
          return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
        };
        const toFixed = (value: unknown): number => {
          const parsed = Number(value ?? 0);
          return Number.isFinite(parsed) ? Number(parsed.toFixed(3)) : 0;
        };

        const totalAnswers = toInt(rawRow.total_answers);
        const abstainedAnswers = toInt(rawRow.abstained_answers);
        const conflictingAnswers = toInt(rawRow.conflicting_answers);
        const citationMissingAnswers = toInt(rawRow.citation_missing_answers);
        const avgCitations = toFixed(rawRow.avg_citations);
        const avgToolCalls = toFixed(rawRow.avg_tool_calls);

        const alerts = [
          citationMissingAnswers > 0
            ? {
                signal: 'citation_missing_answers',
                severity: citationMissingAnswers >= 5 ? 'high' : 'warning',
                count: citationMissingAnswers,
                description: 'Answers marked ready/review but with zero citations.',
              }
            : null,
          conflictingAnswers > 0
            ? {
                signal: 'conflicting_evidence_answers',
                severity: 'warning',
                count: conflictingAnswers,
                description: 'Answers abstained due to conflicting evidence.',
              }
            : null,
          abstainedAnswers > 0
            ? {
                signal: 'abstained_answers',
                severity: 'info',
                count: abstainedAnswers,
                description: 'Answers abstained due to insufficient/conflicting evidence.',
              }
            : null,
        ].filter((item): item is NonNullable<typeof item> => item !== null);

        return reply.send({
          data_product_id: dataProductId,
          window_hours: windowHours,
          summary: {
            total_answers: totalAnswers,
            abstained_answers: abstainedAnswers,
            conflicting_answers: conflictingAnswers,
            citation_missing_answers: citationMissingAnswers,
            avg_citations: avgCitations,
            avg_tool_calls: avgToolCalls,
          },
          alerts,
          generated_at: new Date().toISOString(),
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.send({
            data_product_id: dataProductId,
            window_hours: windowHours,
            summary: {
              total_answers: 0,
              abstained_answers: 0,
              conflicting_answers: 0,
              citation_missing_answers: 0,
              avg_citations: 0,
              avg_tool_calls: 0,
            },
            alerts: [],
            note: 'qa_evidence table not available yet; apply migration first.',
            generated_at: new Date().toISOString(),
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/semantic/:dataProductId/ops/dashboard
   * Dashboard-ready payload with summary metrics, recent traces, and recent alert events.
   */
  app.get(
    '/semantic/:dataProductId/ops/dashboard',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { window_hours?: number; trace_limit?: number; alert_limit?: number };
      }>,
      reply,
    ) => {
      const paramResult = semanticOpsDashboardParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = semanticOpsDashboardQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid ops dashboard query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const {
        window_hours: windowHours,
        trace_limit: traceLimit,
        alert_limit: alertLimit,
      } = queryResult.data;
      const { snowflakeUser } = request.user;

      const toInt = (value: unknown): number => {
        const parsed = Number(value ?? 0);
        return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
      };
      const toFixed = (value: unknown): number => {
        const parsed = Number(value ?? 0);
        return Number.isFinite(parsed) ? Number(parsed.toFixed(3)) : 0;
      };

      try {
        const summaryResult = await postgresService.query(
          `SELECT
             COUNT(*)::int AS total_answers,
             SUM(
               CASE
                 WHEN final_decision IN ('abstained_missing_evidence', 'abstained_conflicting_evidence')
                 THEN 1
                 ELSE 0
               END
             )::int AS abstained_answers,
             SUM(
               CASE
                 WHEN final_decision = 'abstained_conflicting_evidence' THEN 1 ELSE 0
               END
             )::int AS conflicting_answers,
             SUM(
               CASE
                 WHEN final_decision IN ('answer_ready', 'answer_with_warnings')
                   AND (
                     COALESCE(jsonb_array_length(sql_refs), 0) +
                     COALESCE(jsonb_array_length(fact_refs), 0) +
                     COALESCE(jsonb_array_length(chunk_refs), 0)
                   ) = 0
                 THEN 1
                 ELSE 0
               END
             )::int AS citation_missing_answers,
             AVG(
               COALESCE(jsonb_array_length(sql_refs), 0) +
               COALESCE(jsonb_array_length(fact_refs), 0) +
               COALESCE(jsonb_array_length(chunk_refs), 0)
             )::numeric AS avg_citations,
             AVG(COALESCE(jsonb_array_length(tool_calls), 0))::numeric AS avg_tool_calls
           FROM qa_evidence
           WHERE data_product_id = $1::uuid
             AND created_at >= NOW() - ($2::text || ' hours')::interval`,
          [dataProductId, String(windowHours)],
          snowflakeUser,
        );

        const rawSummary = (summaryResult.rows[0] ?? {}) as Record<string, unknown>;
        const summary = {
          total_answers: toInt(rawSummary.total_answers),
          abstained_answers: toInt(rawSummary.abstained_answers),
          conflicting_answers: toInt(rawSummary.conflicting_answers),
          citation_missing_answers: toInt(rawSummary.citation_missing_answers),
          avg_citations: toFixed(rawSummary.avg_citations),
          avg_tool_calls: toFixed(rawSummary.avg_tool_calls),
        };

        const tracesResult = await postgresService.query(
          `SELECT
             id,
             query_id,
             answer_id,
             source_mode,
             confidence,
             exactness_state,
             final_decision,
             (
               COALESCE(jsonb_array_length(sql_refs), 0) +
               COALESCE(jsonb_array_length(fact_refs), 0) +
               COALESCE(jsonb_array_length(chunk_refs), 0)
             )::int AS citation_count,
             COALESCE(jsonb_array_length(tool_calls), 0)::int AS tool_call_count,
             tool_calls,
             created_at
           FROM qa_evidence
           WHERE data_product_id = $1::uuid
             AND created_at >= NOW() - ($2::text || ' hours')::interval
           ORDER BY created_at DESC
           LIMIT $3`,
          [dataProductId, String(windowHours), traceLimit],
          snowflakeUser,
        );

        const recentTraces = tracesResult.rows.map((rawRow) => {
          const row = rawRow as Record<string, unknown>;
          return {
            id: row.id,
            query_id: row.query_id,
            answer_id: row.answer_id,
            source_mode: row.source_mode,
            confidence: row.confidence,
            exactness_state: row.exactness_state,
            final_decision: row.final_decision,
            citation_count: toInt(row.citation_count),
            tool_call_count: toInt(row.tool_call_count),
            model_version_hash: inferModelVersionHash(row.tool_calls),
            created_at: row.created_at,
          };
        });

        let recentAlertEvents: Array<Record<string, unknown>> = [];
        try {
          const alertEvents = await postgresService.query(
            `SELECT
               id,
               signal,
               severity,
               message,
               source_service,
               source_route,
               session_id,
               query_id,
               metadata,
               created_at
             FROM ops_alert_events
             WHERE data_product_id = $1::uuid
               AND created_at >= NOW() - ($2::text || ' hours')::interval
             ORDER BY created_at DESC
             LIMIT $3`,
            [dataProductId, String(windowHours), alertLimit],
            snowflakeUser,
          );
          recentAlertEvents = alertEvents.rows.map(
            (rawRow) => rawRow as Record<string, unknown>,
          );
        } catch (alertErr) {
          if (!isRecoverableContextSchemaError(alertErr)) {
            throw alertErr;
          }
          recentAlertEvents = [];
        }

        const eventCounts = new Map<string, number>();
        for (const event of recentAlertEvents) {
          const signal = String(event.signal ?? '').trim();
          if (!signal) continue;
          eventCounts.set(signal, (eventCounts.get(signal) ?? 0) + 1);
        }

        const alerts = [
          summary.citation_missing_answers > 0
            ? {
                signal: 'citation_missing_answers',
                severity: summary.citation_missing_answers >= 5 ? 'high' : 'warning',
                count: summary.citation_missing_answers,
                description: 'Answers marked ready/review but with zero citations.',
              }
            : null,
          summary.conflicting_answers > 0
            ? {
                signal: 'conflicting_evidence_answers',
                severity: 'warning',
                count: summary.conflicting_answers,
                description: 'Answers abstained due to conflicting evidence.',
              }
            : null,
          summary.abstained_answers > 0
            ? {
                signal: 'abstained_answers',
                severity: 'info',
                count: summary.abstained_answers,
                description: 'Answers abstained due to insufficient/conflicting evidence.',
              }
            : null,
          ...Array.from(eventCounts.entries()).map(([signal, count]) => ({
            signal,
            severity: 'warning',
            count,
            description: 'Operational alert events captured for this signal.',
          })),
        ].filter((item): item is NonNullable<typeof item> => item !== null);

        return reply.send({
          data_product_id: dataProductId,
          window_hours: windowHours,
          summary,
          alerts,
          recent_traces: recentTraces,
          recent_alert_events: recentAlertEvents,
          generated_at: new Date().toISOString(),
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.send({
            data_product_id: dataProductId,
            window_hours: windowHours,
            summary: {
              total_answers: 0,
              abstained_answers: 0,
              conflicting_answers: 0,
              citation_missing_answers: 0,
              avg_citations: 0,
              avg_tool_calls: 0,
            },
            alerts: [],
            recent_traces: [],
            recent_alert_events: [],
            note: 'Hybrid ops dashboard tables are not available yet; apply migrations first.',
            generated_at: new Date().toISOString(),
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/governance/:dataProductId/audit
   * Retrieve governance retention/legal-hold audit trail.
   */
  app.get(
    '/governance/:dataProductId/audit',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { event_type?: string; limit?: number; offset?: number };
      }>,
      reply,
    ) => {
      const paramResult = governanceAuditParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const queryResult = governanceAuditQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid governance audit query',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { event_type: eventType, limit, offset } = queryResult.data;
      const { snowflakeUser } = request.user;

      const params: unknown[] = [dataProductId];
      const where: string[] = ['a.data_product_id = $1::uuid'];
      if (eventType) {
        params.push(eventType);
        where.push(`a.event_type = $${params.length}`);
      }
      params.push(limit);
      const limitPos = params.length;
      params.push(offset);
      const offsetPos = params.length;

      try {
        const result = await postgresService.query(
          `SELECT
             a.id,
             a.data_product_id,
             a.document_id,
             a.event_type,
             a.actor,
             a.details,
             a.created_at,
             ud.filename
           FROM doc_governance_audit a
           LEFT JOIN uploaded_documents ud ON ud.id = a.document_id
           WHERE ${where.join(' AND ')}
           ORDER BY a.created_at DESC
           LIMIT $${limitPos}
           OFFSET $${offsetPos}`,
          params,
          snowflakeUser,
        );

        return reply.send({
          data_product_id: dataProductId,
          limit,
          offset,
          data: result.rows,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.status(409).send({
            error: 'CONTEXT_SCHEMA_MISSING',
            message: 'Governance tables are not available yet. Apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/governance/:dataProductId/legal-holds
   * List active/released legal-hold entries for a data product.
   */
  app.get(
    '/governance/:dataProductId/legal-holds',
    async (
      request: FastifyRequest<{ Params: { dataProductId: string } }>,
      reply,
    ) => {
      const paramResult = legalHoldParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const { dataProductId } = paramResult.data;
      const { snowflakeUser } = request.user;

      try {
        const result = await postgresService.query(
          `SELECT
             h.id,
             h.document_id,
             h.hold_status,
             h.hold_reason,
             h.hold_ref,
             h.created_by,
             h.created_at,
             h.released_by,
             h.released_at,
             ud.filename
           FROM doc_legal_holds h
           LEFT JOIN uploaded_documents ud ON ud.id = h.document_id
           WHERE h.data_product_id = $1::uuid
           ORDER BY h.created_at DESC`,
          [dataProductId],
          snowflakeUser,
        );

        return reply.send({
          data_product_id: dataProductId,
          data: result.rows,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.status(409).send({
            error: 'CONTEXT_SCHEMA_MISSING',
            message: 'Legal-hold tables are not available yet. Apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * POST /documents/governance/:dataProductId/legal-holds
   * Activate or release a legal hold for a document.
   */
  app.post(
    '/governance/:dataProductId/legal-holds',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Body: { document_id: string; action?: 'activate' | 'release'; hold_reason?: string; hold_ref?: string };
      }>,
      reply,
    ) => {
      const paramResult = legalHoldParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const bodyResult = legalHoldBodySchema.safeParse(request.body);
      if (!bodyResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid legal-hold payload',
          details: bodyResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { document_id: documentId, action, hold_reason: holdReason, hold_ref: holdRef } = bodyResult.data;
      const { snowflakeUser } = request.user;

      try {
        const docCheck = await postgresService.query(
          `SELECT id
           FROM uploaded_documents
           WHERE id = $1::uuid
             AND data_product_id = $2::uuid`,
          [documentId, dataProductId],
          snowflakeUser,
        );
        if (docCheck.rowCount === 0) {
          return reply.status(404).send({
            error: 'NOT_FOUND',
            message: 'Document not found for this data product',
          });
        }

        if (action === 'release') {
          const releaseResult = await postgresService.query(
            `UPDATE doc_legal_holds
             SET hold_status = 'released',
                 released_by = $3,
                 released_at = now()
             WHERE data_product_id = $1::uuid
               AND document_id = $2::uuid
               AND hold_status = 'active'
             RETURNING id`,
            [dataProductId, documentId, snowflakeUser],
            snowflakeUser,
          );

          await postgresService.query(
            `INSERT INTO doc_governance_audit
               (id, data_product_id, document_id, event_type, actor, details)
             VALUES
               ($1::uuid, $2::uuid, $3::uuid, 'legal_hold_released', $4, $5::jsonb)`,
            [
              crypto.randomUUID(),
              dataProductId,
              documentId,
              snowflakeUser,
              JSON.stringify({
                released_count: releaseResult.rowCount,
              }),
            ],
            snowflakeUser,
          );

          return reply.send({
            data_product_id: dataProductId,
            document_id: documentId,
            action: 'release',
            released_count: releaseResult.rowCount,
          });
        }

        const insertResult = await postgresService.query(
          `INSERT INTO doc_legal_holds
             (id, data_product_id, document_id, hold_status, hold_reason, hold_ref, created_by)
           VALUES
             ($1::uuid, $2::uuid, $3::uuid, 'active', $4, $5, $6)
           RETURNING id, hold_status, created_at`,
          [
            crypto.randomUUID(),
            dataProductId,
            documentId,
            holdReason ?? 'Legal hold activated by user',
            holdRef ?? null,
            snowflakeUser,
          ],
          snowflakeUser,
        );

        await postgresService.query(
          `INSERT INTO doc_governance_audit
             (id, data_product_id, document_id, event_type, actor, details)
           VALUES
             ($1::uuid, $2::uuid, $3::uuid, 'legal_hold_activated', $4, $5::jsonb)`,
          [
            crypto.randomUUID(),
            dataProductId,
            documentId,
            snowflakeUser,
            JSON.stringify({
              hold_ref: holdRef ?? null,
              hold_reason: holdReason ?? null,
            }),
          ],
          snowflakeUser,
        );

        return reply.status(201).send({
          data_product_id: dataProductId,
          document_id: documentId,
          action: 'activate',
          legal_hold: insertResult.rows[0],
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.status(409).send({
            error: 'CONTEXT_SCHEMA_MISSING',
            message: 'Legal-hold tables are not available yet. Apply migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * POST /documents/governance/:dataProductId/retention/run
   * Execute or dry-run retention sweep for a single data product.
   */
  app.post(
    '/governance/:dataProductId/retention/run',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Body: { retention_now?: string; dry_run?: boolean };
      }>,
      reply,
    ) => {
      const paramResult = retentionRunParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const bodyResult = retentionRunBodySchema.safeParse(request.body ?? {});
      if (!bodyResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid retention run payload',
          details: bodyResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId } = paramResult.data;
      const { retention_now: retentionNowRaw, dry_run: dryRun } = bodyResult.data;
      const { snowflakeUser } = request.user;
      const retentionNow = retentionNowRaw ? new Date(retentionNowRaw) : new Date();

      if (Number.isNaN(retentionNow.getTime())) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'retention_now must be a valid ISO datetime',
        });
      }

      try {
        if (dryRun) {
          const probe = await postgresService.query(
            `SELECT
               COUNT(*) FILTER (
                 WHERE deleted_at IS NULL
                   AND retention_until IS NOT NULL
                   AND retention_until <= $2::timestamptz
               ) AS expired_candidates,
               COUNT(*) FILTER (
                 WHERE deleted_at IS NULL
                   AND retention_until IS NOT NULL
                   AND retention_until <= $2::timestamptz
                   AND COALESCE(legal_hold, FALSE) = TRUE
               ) AS skipped_legal_hold
             FROM doc_registry
             WHERE data_product_id = $1::uuid`,
            [dataProductId, retentionNow.toISOString()],
            snowflakeUser,
          );

          const row = probe.rows[0] as
            | { expired_candidates?: number | string; skipped_legal_hold?: number | string }
            | undefined;

          return reply.send({
            data_product_id: dataProductId,
            dry_run: true,
            retention_now: retentionNow.toISOString(),
            result: {
              expired_candidates: Number(row?.expired_candidates ?? 0),
              skipped_legal_hold: Number(row?.skipped_legal_hold ?? 0),
              deleted_documents: 0,
            },
          });
        }

        const result = await postgresService.query(
          `SELECT apply_document_retention($1::uuid, $2::timestamptz, $3) AS result`,
          [dataProductId, retentionNow.toISOString(), snowflakeUser],
          snowflakeUser,
        );
        const payload = (result.rows[0] as { result?: Record<string, unknown> } | undefined)?.result ?? {};

        return reply.send({
          data_product_id: dataProductId,
          dry_run: false,
          retention_now: retentionNow.toISOString(),
          result: payload,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.status(409).send({
            error: 'CONTEXT_SCHEMA_MISSING',
            message: 'Retention controls are not available yet. Apply governance migration first.',
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /documents/:dataProductId
   * List all non-deleted uploaded documents for a data product.
   */
  app.get(
    '/:dataProductId',
    async (
      request: FastifyRequest<{ Params: { dataProductId: string } }>,
      reply,
    ) => {
      const paramResult = listDocumentsParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid data product id',
        });
      }

      const { dataProductId } = paramResult.data;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, data_product_id, filename, minio_path, file_size_bytes,
           content_type, extraction_status, extraction_error,
           uploaded_by, created_at, extracted_at,
           source_channel, user_note, doc_kind, summary,
           is_deleted, deleted_at, deleted_by, context_version_id
         FROM uploaded_documents
         WHERE data_product_id = $1::uuid
           AND is_deleted = false
         ORDER BY created_at DESC`,
        [dataProductId],
        snowflakeUser,
      );

      return reply.send({
        data: result.rows as UploadedDocumentRow[],
      });
    },
  );

  /**
   * GET /documents/:id/content
   * Get extracted text content of an uploaded document.
   */
  app.get(
    '/:id/content',
    async (
      request: FastifyRequest<{ Params: { id: string } }>,
      reply,
    ) => {
      const paramResult = documentContentParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid document id',
        });
      }

      const { id } = paramResult.data;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, filename, extracted_content, extraction_status
         FROM uploaded_documents
         WHERE id = $1::uuid
           AND is_deleted = false`,
        [id],
        snowflakeUser,
      );

      const doc = result.rows[0] as
        | {
            id: string;
            filename: string;
            extracted_content: string | null;
            extraction_status: string;
          }
        | undefined;

      if (!doc) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Document not found',
        });
      }

      if (doc.extraction_status !== 'completed' || doc.extracted_content === null) {
        return reply.status(422).send({
          error: 'EXTRACTION_PENDING',
          message: `Document extraction status: ${doc.extraction_status}`,
          extraction_status: doc.extraction_status,
        });
      }

      return reply.send({
        id: doc.id,
        filename: doc.filename,
        content: doc.extracted_content,
        extraction_status: doc.extraction_status,
      });
    },
  );

  /**
   * DELETE /documents/:id
   * Soft delete document and exclude its evidence from active context.
   */
  app.delete(
    '/:id',
    async (request: FastifyRequest<{ Params: { id: string } }>, reply) => {
      const paramResult = deleteDocumentParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid document id',
        });
      }

      const { id } = paramResult.data;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT id, data_product_id, minio_path, filename
         FROM uploaded_documents
         WHERE id = $1::uuid
           AND is_deleted = false`,
        [id],
        snowflakeUser,
      );

      const doc = result.rows[0] as
        | {
            id: string;
            data_product_id: string;
            minio_path: string;
            filename: string;
          }
        | undefined;

      if (!doc) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Document not found',
        });
      }

      let impactedSteps: string[] = [];
      try {
        const impacted = await postgresService.query(
          `SELECT DISTINCT step_name
           FROM context_step_selections
           WHERE document_id = $1::uuid
             AND state = 'active'`,
          [id],
          snowflakeUser,
        );

        impactedSteps = (impacted.rows as Array<{ step_name: string }>).map((row) => row.step_name);
      } catch (err) {
        if (!isRecoverableContextSchemaError(err)) {
          throw err;
        }
      }

      const contextVersion = await createContextVersion(
        doc.data_product_id,
        snowflakeUser,
        'document_deleted',
        {
          document_id: id,
          filename: doc.filename,
          impacted_steps: impactedSteps,
        },
      );

      await postgresService.query(
        `UPDATE uploaded_documents
         SET is_deleted = true,
             deleted_at = now(),
             deleted_by = $2,
             context_version_id = $3::uuid
         WHERE id = $1::uuid`,
        [id, snowflakeUser, contextVersion?.id ?? null],
        snowflakeUser,
      );

      try {
        await postgresService.query(
          `UPDATE context_step_selections
           SET state = 'excluded',
               selected_by = $2,
               context_version_id = $3::uuid,
               updated_at = now()
           WHERE document_id = $1::uuid`,
          [id, snowflakeUser, contextVersion?.id ?? null],
          snowflakeUser,
        );
      } catch (err) {
        if (!isRecoverableContextSchemaError(err)) {
          throw err;
        }
      }

      await markRegistryDeleted(doc.data_product_id, doc.id, snowflakeUser, snowflakeUser);

      try {
        await minioService.removeFile(DOCUMENTS_BUCKET, doc.minio_path);
      } catch (err) {
        app.log.warn(
          { err, documentId: id, minioPath: doc.minio_path },
          'Failed to remove document object from MinIO; metadata deletion still completed',
        );
      }

      const contextImpact = await buildContextImpactSummary(
        doc.data_product_id,
        snowflakeUser,
        impactedSteps,
        contextVersion ? Number(contextVersion.version) : null,
      );
      const recommendedActions = contextImpact.recommendedActions.length > 0
        ? contextImpact.recommendedActions
        : (
          impactedSteps.length > 0
            ? ['Use context controls to activate replacement evidence before reruns.']
            : []
        );

      return reply.send({
        status: 'deleted',
        document_id: id,
        impacted_steps: contextImpact.impactedSteps,
        stale_artifacts: contextImpact.staleArtifacts,
        context_version: contextVersion,
        recommended_actions: recommendedActions,
      });
    },
  );

  /**
   * GET /documents/:dataProductId/:documentId/status
   * Check extraction status for a specific document.
   * Used for polling after async upload.
   */
  app.get(
    '/:dataProductId/:documentId/status',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string; documentId: string };
      }>,
      reply,
    ) => {
      const paramResult = documentStatusParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid parameters',
          details: paramResult.error.flatten().fieldErrors,
        });
      }

      const { dataProductId, documentId } = paramResult.data;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT id, extraction_status, extraction_error, extracted_at, filename
         FROM uploaded_documents
         WHERE id = $1::uuid AND data_product_id = $2::uuid`,
        [documentId, dataProductId],
        snowflakeUser,
      );

      const doc = result.rows[0] as
        | {
            id: string;
            extraction_status: string;
            extraction_error: string | null;
            extracted_at: string | null;
            filename: string;
          }
        | undefined;

      if (!doc) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Document not found',
        });
      }

      return reply.send({
        document_id: doc.id,
        filename: doc.filename,
        extraction_status: doc.extraction_status,
        extraction_error: doc.extraction_error,
        extracted_at: doc.extracted_at,
      });
    },
  );
}
