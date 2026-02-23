import crypto from 'node:crypto';

import type { FastifyInstance, FastifyRequest } from 'fastify';
import multipart from '@fastify/multipart';

import { postgresService } from '../services/postgresService.js';
import { minioService } from '../services/minioService.js';
import {
  extractDocumentContent,
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
  extractDocumentParamSchema,
  listDocumentsParamSchema,
  missionStepSchema,
  semanticChunksParamSchema,
  semanticChunksQuerySchema,
  semanticEvidenceParamSchema,
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

export async function documentRoutes(app: FastifyInstance): Promise<void> {
  // Register multipart support for this plugin scope
  await app.register(multipart, {
    limits: {
      fileSize: MAX_FILE_SIZE_BYTES,
      files: 1,
    },
  });

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
  ): Promise<{ entities: number; facts: number }> {
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
        return { entities: 0, facts: 0 };
      }

      const confidence = Math.max(0, Math.min(1, deriveParseQualityScore(extraction) / 100));
      const metadataJson = JSON.stringify({
        extraction_method: extraction.extractionMethod,
        extraction_warnings: extraction.extractionWarnings,
      });

      let entityCount = 0;
      let factCount = 0;

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
        await postgresService.query(
          `INSERT INTO doc_facts
             (id, data_product_id, document_id, fact_type, subject_key, predicate,
              object_value, confidence, metadata)
           VALUES
             ($1::uuid, $2::uuid, $3::uuid, 'metric_hint', $4, 'describes_metric', $5, $6, $7::jsonb)`,
          [
            crypto.randomUUID(),
            dataProductId,
            documentId,
            metricHint.slice(0, 120),
            metricHint,
            confidence,
            metadataJson,
          ],
          snowflakeUser,
        );
        factCount += 1;
      }

      const monetaryFacts = extractMonetaryFacts(extraction.extractedText);
      for (const fact of monetaryFacts) {
        await postgresService.query(
          `INSERT INTO doc_facts
             (id, data_product_id, document_id, fact_type, subject_key, predicate, object_value,
              numeric_value, currency, confidence, metadata)
           VALUES
             ($1::uuid, $2::uuid, $3::uuid, 'monetary_amount', 'document_amount', 'reported_amount',
              $4, $5, $6, $7, $8::jsonb)`,
          [
            crypto.randomUUID(),
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
        factCount += 1;
      }

      return { entities: entityCount, facts: factCount };
    } catch (err) {
      if (isRecoverableContextSchemaError(err)) return { entities: 0, facts: 0 };
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
             metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('deleted_by', $3)
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

    await refreshDocumentEntitiesAndFacts(
      dataProductId,
      documentId,
      extraction,
      evidenceSummary,
      snowflakeUser,
    );

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
          return reply.status(202).send({
            status: 'failed',
            message: extractionError ?? 'Extraction failed',
            extraction_method: extraction.extractionMethod,
            warnings: extraction.extractionWarnings,
            context_version: contextVersion,
          });
        }

        if (extraction.extractionStatus === 'pending') {
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
          });
        }

        const maxVersion = Number(versions[0]?.version ?? 1);
        const toVersion = toVersionRaw ?? maxVersion;
        const fromVersion = fromVersionRaw ?? Math.max(1, toVersion - 1);

        const bounded = versions
          .filter((row) => Number(row.version) >= fromVersion && Number(row.version) <= toVersion)
          .sort((a, b) => Number(a.version) - Number(b.version));

        return reply.send({
          data_product_id: dataProductId,
          from_version: fromVersion,
          to_version: toVersion,
          changes: bounded,
        });
      } catch (err) {
        if (isRecoverableContextSchemaError(err)) {
          return reply.send({
            data_product_id: dataProductId,
            from_version: null,
            to_version: null,
            changes: [],
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

      const recommendedActions = impactedSteps.length > 0
        ? [
            'Review impacted mission steps and decide whether regeneration is required.',
            'Use context apply controls to activate alternative evidence before reruns.',
          ]
        : [];

      return reply.send({
        status: 'deleted',
        document_id: id,
        impacted_steps: impactedSteps,
        context_version: contextVersion,
        recommended_actions: recommendedActions,
      });
    },
  );
}
