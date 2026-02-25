import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractDocumentContent } from './documentExtractionService.js';
import { snowflakeService } from './snowflakeService.js';

function makeInput(
  filename: string,
  contentType: string,
  content: string | Buffer,
) {
  return {
    dataProductId: '11111111-1111-1111-1111-111111111111',
    documentId: '22222222-2222-2222-2222-222222222222',
    filename,
    contentType,
    buffer: Buffer.isBuffer(content) ? content : Buffer.from(content),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('extractDocumentContent regression matrix', () => {
  it('uses local text fallback for SQL-like text files', async () => {
    const input = makeInput(
      'schema.ddl.sql',
      'application/sql',
      'CREATE TABLE orders (id INT, amount NUMBER);',
    );

    const result = await extractDocumentContent(input);

    expect(result.extractionStatus).toBe('completed');
    expect(result.extractionMethod).toBe('local_text_fallback');
    expect(result.extractedText).toContain('CREATE TABLE orders');
    expect(result.extractionMetadata).toMatchObject({
      provider: 'local',
      method: 'utf8_decode',
      profile: {
        extension: 'sql',
        isTextLike: true,
      },
    });
  });

  it('adds PBIX-specific warning when fallback cannot extract deterministic metadata', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'pending',
          method: 'ai_multimodal_fallback',
          extracted_text: null,
          warnings: ['No parseable model metadata found.'],
          metadata: { provider: 'ai-service' },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );

    const input = makeInput('model.pbix', 'application/vnd.ms-powerbi', Buffer.from([0, 1, 2, 3]));
    const result = await extractDocumentContent(input);

    expect(result.extractionStatus).toBe('pending');
    expect(result.extractionMethod).toBe('ai_multimodal_fallback');
    expect(result.extractionWarnings.join(' ')).toContain('PBIX extraction is partial');
    expect(result.extractionMetadata).toMatchObject({
      provider: 'ai-service',
      profile: {
        extension: 'pbix',
        isLikelyPbix: true,
      },
    });
  });

  it('falls back to ai-service extraction when Snowflake extraction path is unavailable', async () => {
    vi.spyOn(snowflakeService, 'executeQuery').mockRejectedValue(new Error('snowflake unavailable'));
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'completed',
          method: 'ai_multimodal_fallback',
          extracted_text: 'Ibuprofen adverse reactions include nausea and GI bleeding risk.',
          summary: 'Drug label summary',
          warnings: [],
          metadata: { provider: 'ai-service' },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );

    const input = makeInput('ibuprofen-label.pdf', 'application/pdf', Buffer.from([5, 6, 7, 8]));
    const result = await extractDocumentContent(input);

    expect(result.extractionStatus).toBe('completed');
    expect(result.extractionMethod).toBe('ai_multimodal_fallback');
    expect(result.extractedText).toContain('Ibuprofen');
    expect(result.extractionWarnings.join(' ')).toContain('Snowflake extraction unavailable');
    expect(result.extractionMetadata).toMatchObject({
      provider: 'ai-service',
      profile: {
        extension: 'pdf',
        supportsSnowflakeParse: true,
      },
    });
  });

  it('returns unsupported-binary result when no extractor can parse payload', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'failed',
          method: 'ai_multimodal_fallback',
          extracted_text: null,
          warnings: ['unable to parse'],
          metadata: { provider: 'ai-service' },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );

    const input = makeInput('blob.bin', 'application/octet-stream', Buffer.from([9, 9, 9]));
    const result = await extractDocumentContent(input);

    expect(result.extractionStatus).toBe('pending');
    expect(result.extractionMethod).toBe('unsupported_binary');
    expect(result.extractionWarnings.join(' ')).toContain('No compatible extractor');
    expect(result.extractionMetadata).toMatchObject({
      provider: 'none',
      profile: {
        extension: 'bin',
        supportsSnowflakeParse: false,
        supportsSnowflakeExtract: false,
      },
    });
  });
});
