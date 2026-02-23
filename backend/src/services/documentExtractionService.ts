import crypto from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs/promises';

import { config } from '../config.js';
import { snowflakeService } from './snowflakeService.js';

type ExtractionStatus = 'completed' | 'pending' | 'failed';

interface DocumentExtractionInput {
  dataProductId: string;
  documentId: string;
  filename: string;
  contentType: string | null;
  buffer: Buffer;
}

interface FileProfile {
  extension: string;
  normalizedMime: string;
  isTextLike: boolean;
  isLikelyPbix: boolean;
  supportsSnowflakeParse: boolean;
  supportsSnowflakeExtract: boolean;
}

interface SnowflakeExtractionAttempt {
  extractedText: string | null;
  extractionStatus: ExtractionStatus;
  extractionMethod: string;
  extractionWarnings: string[];
  extractionMetadata: Record<string, unknown>;
  summaryHint?: string;
}

interface DocumentExtractionResult {
  extractedText: string | null;
  extractionStatus: ExtractionStatus;
  extractionMethod: string;
  extractionWarnings: string[];
  extractionMetadata: Record<string, unknown>;
  summaryHint?: string;
}

const TEXT_EXTENSIONS = new Set([
  'txt',
  'md',
  'sql',
  'ddl',
  'dbml',
  'csv',
  'json',
  'yaml',
  'yml',
  'xml',
  'html',
  'htm',
  'log',
  'ini',
  'cfg',
]);

// As of Feb 2026, AI_PARSE_DOCUMENT coverage.
const SNOWFLAKE_PARSE_EXTENSIONS = new Set([
  'pdf',
  'pptx',
  'docx',
  'jpg',
  'jpeg',
  'png',
  'tif',
  'tiff',
  'html',
  'txt',
]);

// As of Feb 2026, AI_EXTRACT supports broader document/image set.
const SNOWFLAKE_EXTRACT_EXTENSIONS = new Set([
  'pdf',
  'doc',
  'docx',
  'ppt',
  'pptx',
  'txt',
  'html',
  'md',
  'eml',
  'jpg',
  'jpeg',
  'png',
  'tif',
  'tiff',
  'bmp',
  'gif',
  'webp',
]);

const SNOWFLAKE_PARSE_MIME_PREFIXES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'image/jpeg',
  'image/png',
  'image/tiff',
  'text/html',
  'text/plain',
] as const;

const SNOWFLAKE_EXTRACT_MIME_PREFIXES = [
  ...SNOWFLAKE_PARSE_MIME_PREFIXES,
  'application/msword',
  'application/vnd.ms-powerpoint',
  'text/markdown',
  'message/rfc822',
  'image/bmp',
  'image/gif',
  'image/webp',
] as const;

let stageRefPromise: Promise<string> | null = null;
const MAX_AI_FALLBACK_BYTES = 12 * 1024 * 1024;

interface AiServiceExtractionResponse {
  status?: 'completed' | 'pending' | 'failed';
  method?: string;
  extracted_text?: string | null;
  summary?: string | null;
  warnings?: string[];
  metadata?: Record<string, unknown>;
}

function getExtension(filename: string): string {
  const lower = filename.toLowerCase();
  if (!lower.includes('.')) return '';
  return lower.split('.').pop() ?? '';
}

function normalizeMime(contentType: string | null): string {
  const raw = (contentType ?? '').trim().toLowerCase();
  if (!raw) return 'application/octet-stream';
  return raw.split(';', 1)[0] ?? raw;
}

function resolveStagePreference(
  input: string,
): { stageRef: string; stageIdentifier: string | null; createIfMissing: boolean } {
  const trimmed = input.trim();
  if (!trimmed || trimmed === '~' || trimmed === '@~') {
    return { stageRef: '@~', stageIdentifier: null, createIfMissing: false };
  }

  const withoutAt = trimmed.startsWith('@') ? trimmed.slice(1) : trimmed;
  if (!withoutAt || !/^[A-Za-z0-9_.$]+$/.test(withoutAt)) {
    throw new Error('SNOWFLAKE_DOCUMENT_STAGE contains unsupported characters');
  }
  return {
    stageRef: `@${withoutAt}`,
    stageIdentifier: withoutAt,
    createIfMissing: true,
  };
}

function sanitizePathSegment(input: string): string {
  const normalized = input.trim();
  if (!normalized) return 'unknown';
  return normalized.replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 120);
}

function sanitizeFileName(filename: string): string {
  const basename = path.basename(filename || 'document');
  const cleaned = basename.replace(/[^A-Za-z0-9._-]/g, '_');
  if (!cleaned) return 'document';
  return cleaned.slice(0, 160);
}

function startsWithAny(value: string, prefixes: readonly string[]): boolean {
  return prefixes.some((prefix) => value.startsWith(prefix));
}

function detectFileProfile(filename: string, contentType: string | null): FileProfile {
  const extension = getExtension(filename);
  const normalizedMime = normalizeMime(contentType);
  const isTextLike =
    normalizedMime.startsWith('text/') ||
    normalizedMime === 'application/json' ||
    normalizedMime === 'application/xml' ||
    normalizedMime === 'application/csv' ||
    TEXT_EXTENSIONS.has(extension);
  const isLikelyPbix =
    extension === 'pbix' ||
    normalizedMime === 'application/vnd.ms-powerbi';
  const supportsSnowflakeParse =
    SNOWFLAKE_PARSE_EXTENSIONS.has(extension) ||
    startsWithAny(normalizedMime, SNOWFLAKE_PARSE_MIME_PREFIXES);
  const supportsSnowflakeExtract =
    SNOWFLAKE_EXTRACT_EXTENSIONS.has(extension) ||
    startsWithAny(normalizedMime, SNOWFLAKE_EXTRACT_MIME_PREFIXES);

  return {
    extension,
    normalizedMime,
    isTextLike,
    isLikelyPbix,
    supportsSnowflakeParse,
    supportsSnowflakeExtract,
  };
}

function escapeSqlLiteral(value: string): string {
  return value.replace(/'/g, "''");
}

function isMostlyReadableText(text: string): boolean {
  if (!text) return false;
  const sample = text.slice(0, 8000);
  let printable = 0;
  for (let i = 0; i < sample.length; i += 1) {
    const code = sample.charCodeAt(i);
    if (code === 9 || code === 10 || code === 13 || (code >= 32 && code <= 126)) {
      printable += 1;
    }
  }
  const ratio = printable / sample.length;
  return ratio >= 0.72;
}

function toJsonObject(value: unknown): Record<string, unknown> | null {
  if (!value) return null;
  if (typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return null;
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
      return null;
    } catch {
      return null;
    }
  }
  return null;
}

function getCaseInsensitiveValue(
  row: Record<string, unknown>,
  key: string,
): unknown {
  const target = key.toLowerCase();
  for (const entry of Object.entries(row)) {
    if (entry[0].toLowerCase() === target) return entry[1];
  }
  return undefined;
}

function flattenStructuredText(value: unknown, prefix = ''): string[] {
  if (value === null || value === undefined) return [];
  if (typeof value === 'string') {
    const normalized = value.replace(/\s+/g, ' ').trim();
    if (!normalized) return [];
    return prefix ? [`${prefix}: ${normalized}`] : [normalized];
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return prefix ? [`${prefix}: ${String(value)}`] : [String(value)];
  }
  if (Array.isArray(value)) {
    const collected: string[] = [];
    for (let i = 0; i < value.length; i += 1) {
      const label = prefix ? `${prefix}[${i}]` : `[${i}]`;
      collected.push(...flattenStructuredText(value[i], label));
      if (collected.length >= 80) break;
    }
    return collected;
  }
  if (typeof value === 'object') {
    const collected: string[] = [];
    for (const [k, v] of Object.entries(value)) {
      const label = prefix ? `${prefix}.${k}` : k;
      collected.push(...flattenStructuredText(v, label));
      if (collected.length >= 120) break;
    }
    return collected;
  }
  return [];
}

function extractTextFromParseDocument(
  parsed: Record<string, unknown>,
): string | null {
  const pages = parsed.pages;
  if (Array.isArray(pages)) {
    const content = pages
      .map((page) => {
        if (!page || typeof page !== 'object') return '';
        const value = (page as Record<string, unknown>).content;
        if (typeof value !== 'string') return '';
        return value.trim();
      })
      .filter((chunk) => chunk.length > 0);
    if (content.length > 0) return content.join('\n\n');
  }

  const directContent = parsed.content;
  if (typeof directContent === 'string' && directContent.trim().length > 0) {
    return directContent.trim();
  }

  const flattened = flattenStructuredText(parsed).slice(0, 120);
  if (flattened.length === 0) return null;
  return flattened.join('\n').slice(0, 500_000);
}

function extractTextFromAiExtract(
  extracted: Record<string, unknown>,
): string | null {
  const summary = extracted.summary;
  const docType = extracted.document_type;
  const tables = extracted.tables;
  const entities = extracted.entities;
  const rules = extracted.rules;

  const lines: string[] = [];
  if (typeof docType === 'string' && docType.trim().length > 0) {
    lines.push(`Document type: ${docType.trim()}`);
  }
  if (typeof summary === 'string' && summary.trim().length > 0) {
    lines.push(`Summary: ${summary.trim()}`);
  }
  lines.push(...flattenStructuredText(tables, 'tables').slice(0, 30));
  lines.push(...flattenStructuredText(entities, 'entities').slice(0, 40));
  lines.push(...flattenStructuredText(rules, 'rules').slice(0, 40));

  if (lines.length === 0) {
    lines.push(...flattenStructuredText(extracted).slice(0, 120));
  }

  if (lines.length === 0) return null;
  return lines.join('\n').slice(0, 500_000);
}

async function getSnowflakeDocumentStageRef(): Promise<string> {
  if (!stageRefPromise) {
    stageRefPromise = (async () => {
      const preference = resolveStagePreference(config.SNOWFLAKE_DOCUMENT_STAGE);
      if (!preference.createIfMissing || !preference.stageIdentifier) {
        return preference.stageRef;
      }

      try {
        await snowflakeService.executeQuery(
          `CREATE STAGE IF NOT EXISTS ${preference.stageIdentifier}`,
        );
        return preference.stageRef;
      } catch {
        // Fallback to per-user session stage when named stage creation is not possible.
        return '@~';
      }
    })();
  }
  return stageRefPromise;
}

async function stageBufferInSnowflake(
  input: DocumentExtractionInput,
): Promise<{ stageRef: string; stageRelativePath: string; cleanup: () => Promise<void> }> {
  const stageRef = await getSnowflakeDocumentStageRef();
  const safeFilename = sanitizeFileName(input.filename);
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'ekaix-doc-'));
  const tempFile = path.join(tempDir, safeFilename);
  await fs.writeFile(tempFile, input.buffer);

  const fileUri = `file://${tempFile.replace(/\\/g, '/')}`;
  const productSegment = sanitizePathSegment(input.dataProductId);
  const docSegment = sanitizePathSegment(input.documentId);
  const nonce = crypto.randomBytes(4).toString('hex');
  const stagePrefix = `${productSegment}/${docSegment}/${Date.now()}-${nonce}`;
  const stageRelativePath = `${stagePrefix}/${safeFilename}`;

  const putSql = `PUT '${escapeSqlLiteral(fileUri)}' ${stageRef}/${stagePrefix} AUTO_COMPRESS=FALSE OVERWRITE=TRUE`;
  await snowflakeService.executeQuery(putSql);

  const cleanup = async (): Promise<void> => {
    try {
      await snowflakeService.executeQuery(`REMOVE ${stageRef}/${stageRelativePath}`);
    } catch {
      // Best-effort cleanup only.
    }
    await fs.rm(tempDir, { recursive: true, force: true });
  };

  return { stageRef, stageRelativePath, cleanup };
}

async function runSnowflakeParseDocument(
  stageRef: string,
  stageRelativePath: string,
): Promise<SnowflakeExtractionAttempt | null> {
  const sql = `
    SELECT AI_PARSE_DOCUMENT(
      TO_FILE('${escapeSqlLiteral(stageRef)}', '${escapeSqlLiteral(stageRelativePath)}'),
      OBJECT_CONSTRUCT('mode', 'LAYOUT', 'page_split', TRUE)
    ) AS parsed_output
  `;
  const result = await snowflakeService.executeQuery(sql);
  const row = result.rows[0];
  if (!row) return null;

  const parsed = toJsonObject(getCaseInsensitiveValue(row, 'parsed_output'));
  if (!parsed) return null;

  const extractedText = extractTextFromParseDocument(parsed);
  const pageCount = Array.isArray(parsed.pages) ? parsed.pages.length : undefined;

  return {
    extractedText,
    extractionStatus: extractedText ? 'completed' : 'pending',
    extractionMethod: 'snowflake_ai_parse_document',
    extractionWarnings: extractedText ? [] : ['Snowflake parse returned no readable text.'],
    extractionMetadata: {
      provider: 'snowflake',
      method: 'AI_PARSE_DOCUMENT',
      page_count: pageCount ?? null,
      confidence_score: extractedText ? 0.92 : 0.55,
    },
    summaryHint:
      typeof parsed.summary === 'string' && parsed.summary.trim().length > 0
        ? parsed.summary.trim().slice(0, 280)
        : undefined,
  };
}

async function runSnowflakeAiExtract(
  stageRef: string,
  stageRelativePath: string,
): Promise<SnowflakeExtractionAttempt | null> {
  const sql = `
    SELECT AI_EXTRACT(
      TO_FILE('${escapeSqlLiteral(stageRef)}', '${escapeSqlLiteral(stageRelativePath)}'),
      OBJECT_CONSTRUCT(
        'document_type', 'Classify the document type.',
        'summary', 'Provide a concise business summary in 3-4 lines.',
        'tables', 'List tabular structures and key columns if present.',
        'entities', 'List business entities, metrics, identifiers, and relationships.',
        'rules', 'List explicit business rules, thresholds, filters, and caveats.'
      )
    ) AS extracted_output
  `;
  const result = await snowflakeService.executeQuery(sql);
  const row = result.rows[0];
  if (!row) return null;

  const extracted = toJsonObject(getCaseInsensitiveValue(row, 'extracted_output'));
  if (!extracted) return null;

  const extractedText = extractTextFromAiExtract(extracted);
  const summary =
    typeof extracted.summary === 'string' ? extracted.summary.trim().slice(0, 280) : undefined;

  return {
    extractedText,
    extractionStatus: extractedText ? 'completed' : 'pending',
    extractionMethod: 'snowflake_ai_extract',
    extractionWarnings: extractedText ? [] : ['Snowflake extract returned sparse output.'],
    extractionMetadata: {
      provider: 'snowflake',
      method: 'AI_EXTRACT',
      confidence_score: extractedText ? 0.84 : 0.5,
      output_keys: Object.keys(extracted),
    },
    summaryHint: summary,
  };
}

async function trySnowflakeExtraction(
  input: DocumentExtractionInput,
  profile: FileProfile,
): Promise<SnowflakeExtractionAttempt | null> {
  if (!profile.supportsSnowflakeParse && !profile.supportsSnowflakeExtract) {
    return null;
  }

  const staged = await stageBufferInSnowflake(input);
  try {
    if (profile.supportsSnowflakeParse) {
      const parsed = await runSnowflakeParseDocument(staged.stageRef, staged.stageRelativePath);
      if (parsed?.extractionStatus === 'completed') return parsed;
      if (parsed && profile.supportsSnowflakeExtract) {
        const extracted = await runSnowflakeAiExtract(staged.stageRef, staged.stageRelativePath);
        if (extracted?.extractionStatus === 'completed') {
          return {
            ...extracted,
            extractionWarnings: [...parsed.extractionWarnings, ...extracted.extractionWarnings],
            extractionMetadata: {
              ...extracted.extractionMetadata,
              parse_attempt_metadata: parsed.extractionMetadata,
            },
          };
        }
        if (extracted) {
          return {
            ...extracted,
            extractionWarnings: [...parsed.extractionWarnings, ...extracted.extractionWarnings],
          };
        }
      }
      if (parsed) return parsed;
    }

    if (profile.supportsSnowflakeExtract) {
      return await runSnowflakeAiExtract(staged.stageRef, staged.stageRelativePath);
    }
    return null;
  } finally {
    await staged.cleanup();
  }
}

function buildLocalTextFallback(
  input: DocumentExtractionInput,
  profile: FileProfile,
): DocumentExtractionResult | null {
  if (!profile.isTextLike) return null;
  const decoded = input.buffer.toString('utf-8').trim();
  if (!decoded || !isMostlyReadableText(decoded)) return null;

  return {
    extractedText: decoded.slice(0, 500_000),
    extractionStatus: 'completed',
    extractionMethod: 'local_text_fallback',
    extractionWarnings: [],
    extractionMetadata: {
      provider: 'local',
      method: 'utf8_decode',
      confidence_score: 0.65,
      bytes: input.buffer.length,
    },
  };
}

async function tryAiServiceExtraction(
  input: DocumentExtractionInput,
): Promise<DocumentExtractionResult> {
  if (input.buffer.length > MAX_AI_FALLBACK_BYTES) {
    return {
      extractedText: null,
      extractionStatus: 'pending',
      extractionMethod: 'ai_multimodal_fallback',
      extractionWarnings: [
        `Document exceeds AI fallback size limit (${MAX_AI_FALLBACK_BYTES} bytes).`,
      ],
      extractionMetadata: {
        provider: 'ai-service',
        method: 'documents.extract',
        max_bytes: MAX_AI_FALLBACK_BYTES,
        received_bytes: input.buffer.length,
      },
      summaryHint:
        'Document uploaded, but AI fallback extraction was skipped due to file size.',
    };
  }

  const payload = {
    filename: input.filename,
    content_type: normalizeMime(input.contentType),
    base64_data: input.buffer.toString('base64'),
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45_000);

  try {
    const response = await fetch(`${config.AI_SERVICE_URL}/documents/extract`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    if (!response.ok) {
      const body = await response.text();
      return {
        extractedText: null,
        extractionStatus: 'pending',
        extractionMethod: 'ai_multimodal_fallback',
        extractionWarnings: [
          `AI fallback endpoint returned HTTP ${response.status}: ${body.slice(0, 220)}`,
        ],
        extractionMetadata: {
          provider: 'ai-service',
          method: 'documents.extract',
          http_status: response.status,
        },
      };
    }

    const parsed = (await response.json()) as AiServiceExtractionResponse;
    const warnings = Array.isArray(parsed.warnings)
      ? parsed.warnings.filter((warning): warning is string => typeof warning === 'string')
      : [];
    const extractedText =
      typeof parsed.extracted_text === 'string' && parsed.extracted_text.trim().length > 0
        ? parsed.extracted_text.trim().slice(0, 500_000)
        : null;
    const summaryHint =
      typeof parsed.summary === 'string' && parsed.summary.trim().length > 0
        ? parsed.summary.trim().slice(0, 280)
        : undefined;

    const statusRaw = parsed.status ?? 'pending';
    const extractionStatus: ExtractionStatus =
      statusRaw === 'completed' ? 'completed' : statusRaw === 'failed' ? 'failed' : 'pending';

    return {
      extractedText,
      extractionStatus,
      extractionMethod: parsed.method || 'ai_multimodal_fallback',
      extractionWarnings: warnings,
      extractionMetadata: {
        provider: 'ai-service',
        method: parsed.method || 'documents.extract',
        ...(parsed.metadata ?? {}),
      },
      summaryHint,
    };
  } catch (err) {
    return {
      extractedText: null,
      extractionStatus: 'pending',
      extractionMethod: 'ai_multimodal_fallback',
      extractionWarnings: [
        err instanceof Error
          ? `AI fallback unavailable: ${err.message}`
          : 'AI fallback unavailable',
      ],
      extractionMetadata: {
        provider: 'ai-service',
        method: 'documents.extract',
      },
    };
  } finally {
    clearTimeout(timer);
  }
}

function buildUnsupportedFormatResult(
  profile: FileProfile,
): DocumentExtractionResult {
  if (profile.isLikelyPbix) {
    return {
      extractedText: null,
      extractionStatus: 'pending',
      extractionMethod: 'unsupported_pbix',
      extractionWarnings: [
        'PBIX requires model-metadata extraction (PBIP/XMLA) for full semantic understanding.',
      ],
      extractionMetadata: {
        provider: 'none',
        method: 'pbix_pending',
        confidence_score: 0.2,
      },
      summaryHint:
        'Power BI PBIX detected. Add PBIP/XMLA metadata export for full model extraction.',
    };
  }

  return {
    extractedText: null,
    extractionStatus: 'pending',
    extractionMethod: 'unsupported_binary',
    extractionWarnings: [
      'No compatible extractor was available for this format in the current runtime.',
    ],
    extractionMetadata: {
      provider: 'none',
      method: 'unsupported',
      confidence_score: 0.1,
    },
    summaryHint:
      'Binary document uploaded. Extraction is pending until a compatible parser is available.',
  };
}

export async function extractDocumentContent(
  input: DocumentExtractionInput,
): Promise<DocumentExtractionResult> {
  const profile = detectFileProfile(input.filename, input.contentType);
  const warnings: string[] = [];

  try {
    const snowflakeResult = await trySnowflakeExtraction(input, profile);
    if (snowflakeResult && snowflakeResult.extractionStatus === 'completed') {
      return {
        ...snowflakeResult,
        extractionMetadata: {
          ...snowflakeResult.extractionMetadata,
          profile,
        },
      };
    }
    if (snowflakeResult) {
      warnings.push(...snowflakeResult.extractionWarnings);
    }
  } catch (err) {
    warnings.push(
      err instanceof Error
        ? `Snowflake extraction unavailable: ${err.message}`
        : 'Snowflake extraction unavailable',
    );
  }

  const localText = buildLocalTextFallback(input, profile);
  if (localText) {
    return {
      ...localText,
      extractionWarnings: [...warnings, ...localText.extractionWarnings],
      extractionMetadata: {
        ...localText.extractionMetadata,
        profile,
      },
    };
  }

  const aiFallback = await tryAiServiceExtraction(input);
  if (aiFallback.extractionStatus === 'completed' || aiFallback.extractionStatus === 'pending') {
    const mergedWarnings = [...warnings, ...aiFallback.extractionWarnings];
    if (
      profile.isLikelyPbix &&
      aiFallback.extractionStatus !== 'completed' &&
      !mergedWarnings.some((warning) => warning.toLowerCase().includes('pbix'))
    ) {
      mergedWarnings.push(
        'PBIX extraction is partial without PBIP/XMLA metadata export; upload exported model metadata for stronger context grounding.',
      );
    }

    return {
      ...aiFallback,
      extractionWarnings: mergedWarnings,
      extractionMetadata: {
        ...aiFallback.extractionMetadata,
        profile,
      },
    };
  }
  warnings.push(...aiFallback.extractionWarnings);

  const unsupported = buildUnsupportedFormatResult(profile);
  return {
    ...unsupported,
    extractionWarnings: [...warnings, ...unsupported.extractionWarnings],
    extractionMetadata: {
      ...unsupported.extractionMetadata,
      profile,
    },
  };
}

export type { ExtractionStatus, DocumentExtractionResult, DocumentExtractionInput };
