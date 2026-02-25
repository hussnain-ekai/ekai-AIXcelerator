import { describe, expect, it } from 'vitest';

import type { AnswerContract } from '@/lib/answerContract';

import { createChatStore } from './chatStore';

const SAMPLE_CONTRACT: AnswerContract = {
  source_mode: 'structured',
  exactness_state: 'validated_exact',
  confidence_decision: 'high',
  trust_state: 'answer_ready',
  evidence_summary: 'Query validated against deterministic sources.',
  conflict_notes: [],
  citations: [],
  recovery_actions: [],
  metadata: {},
};

describe('chatStore answer contract mapping', () => {
  it('attaches answer contract to the latest assistant message', () => {
    const store = createChatStore();

    store.getState().addMessage({
      id: 'user-1',
      role: 'user',
      content: 'Show me a status summary.',
      timestamp: '2026-02-24T00:00:00.000Z',
    });
    store.getState().addMessage({
      id: 'assistant-1',
      role: 'assistant',
      content: 'Working on it.',
      timestamp: '2026-02-24T00:00:01.000Z',
      isStreaming: true,
    });

    store.getState().attachAnswerContractToLastAssistant(SAMPLE_CONTRACT);

    const state = store.getState();
    const assistant = state.messages.find((message) => message.id === 'assistant-1');

    expect(assistant?.answerContract).toMatchObject({
      source_mode: 'structured',
      confidence_decision: 'high',
    });
    expect(state.latestAnswerContract).toMatchObject({
      source_mode: 'structured',
      confidence_decision: 'high',
    });
    expect(state.answerTrustState).toBe('answer_ready');
  });

  it('stores latest contract even when no assistant message exists yet', () => {
    const store = createChatStore();

    store.getState().attachAnswerContractToLastAssistant(SAMPLE_CONTRACT);

    const state = store.getState();
    expect(state.messages).toHaveLength(0);
    expect(state.latestAnswerContract).toMatchObject({
      source_mode: 'structured',
      confidence_decision: 'high',
    });
    expect(state.answerTrustState).toBe('answer_ready');
  });
});
