import { describe, expect, it } from 'vitest';

import {
  normalizeApiResponseEnvelope,
  normalizeAnswerContractPayload,
  normalizeSseLine,
  normalizeStatusEventData,
} from './agent.js';

describe('agent contract normalization', () => {
  it('normalizes sparse contract payload to required defaults', () => {
    const normalized = normalizeAnswerContractPayload(
      {
        source_mode: 'document',
        confidence_decision: 'abstain',
      },
      'Need deterministic evidence',
    );

    expect(normalized).toMatchObject({
      source_mode: 'document',
      exactness_state: 'not_applicable',
      confidence_decision: 'abstain',
      trust_state: 'abstained_missing_evidence',
      evidence_summary: 'Need deterministic evidence',
    });
    expect(Array.isArray(normalized.citations)).toBe(true);
    expect(Array.isArray(normalized.recovery_actions)).toBe(true);
  });

  it('normalizes status event and injects answer_contract envelope', () => {
    const payload = normalizeStatusEventData({
      message: 'Working on next step',
      source_mode: 'structured',
      exactness_state: 'validated_exact',
      confidence_decision: 'high',
      citations: [{ citation_type: 'sql', reference_id: 'sql-1' }],
    });

    expect(payload.answer_contract).toBeDefined();
    expect(payload.source_mode).toBe('structured');
    expect(payload.exactness_state).toBe('validated_exact');
    expect(payload.confidence_decision).toBe('high');
  });

  it('normalizes API envelope for /agent/message and /agent/retry', () => {
    const normalized = normalizeApiResponseEnvelope({
      status: 'completed',
      message: 'Response generated',
      answer_contract: {
        source_mode: 'hybrid',
        exactness_state: 'validated_exact',
        confidence_decision: 'high',
        citations: [{ citation_type: 'document_fact', reference_id: 'fact-1' }],
      },
    });

    expect(normalized.answer_contract).toBeDefined();
    expect(normalized.source_mode).toBe('hybrid');
    expect(normalized.exactness_state).toBe('validated_exact');
    expect(normalized.confidence_decision).toBe('high');
  });

  it('normalizes status SSE data line and leaves other lines unchanged', () => {
    const statusLine =
      'data: {"type":"status","data":{"message":"Still running","confidence_decision":"abstain"}}';
    const normalized = normalizeSseLine(statusLine);

    expect(normalized).toContain('"type":"status"');
    expect(normalized).toContain('"answer_contract"');
    expect(normalized).toContain('"trust_state":"abstained_missing_evidence"');

    const tokenLine = 'data: {"type":"token","data":{"content":"hello"}}';
    expect(normalizeSseLine(tokenLine)).toBe(tokenLine);
  });
});
