'use client';

import { useEffect, useRef } from 'react';
import { api } from '@/lib/api';
import { useChatStore } from '@/stores/chatStore';
import type { ChatMessage, AgentPhase } from '@/stores/chatStore';
import type { DataProduct } from '@/hooks/useDataProducts';

interface HistoryResponse {
  session_id: string;
  messages: Array<{
    id?: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    timestamp?: string;
    tool_calls?: Array<{
      name: string;
      input: Record<string, unknown>;
      result?: string;
    }>;
  }>;
  phase?: string;
}

interface UseSessionRecoveryReturn {
  isHydrated: boolean;
}

/**
 * Hook to recover chat session from backend when navigating to a data product page.
 *
 * Extracts session_id from dataProduct.state.session_id and fetches history
 * from GET /agent/history/{sessionId}. Hydrates chatStore with recovered messages.
 */
function useSessionRecovery(dataProduct: DataProduct | undefined): UseSessionRecoveryReturn {
  const recoveryAttemptedRef = useRef<string | null>(null);
  const isHydrated = useChatStore((state) => state.isHydrated);
  const messages = useChatStore((state) => state.messages);
  const hydrateFromHistory = useChatStore((state) => state.hydrateFromHistory);
  const setHydrated = useChatStore((state) => state.setHydrated);
  const sessionId = useChatStore((state) => state.sessionId);

  const dataProductId = dataProduct?.id;
  const storedSessionId = dataProduct?.state?.session_id;

  // Reset hydration state when data product changes (navigation)
  useEffect(() => {
    if (dataProductId && recoveryAttemptedRef.current !== dataProductId) {
      // New data product - reset state for fresh recovery
      setHydrated(false);
    }
  }, [dataProductId, setHydrated]);

  useEffect(() => {
    // Only attempt recovery once per data product
    if (recoveryAttemptedRef.current === dataProductId) return;

    // Wait for data product to load
    if (!dataProductId) return;

    // If session matches and we already have messages, mark hydrated
    if (sessionId && sessionId === storedSessionId && messages.length > 0) {
      recoveryAttemptedRef.current = dataProductId;
      setHydrated(true);
      return;
    }

    // No session ID stored - mark as hydrated with empty state
    if (!storedSessionId) {
      recoveryAttemptedRef.current = dataProductId;
      setHydrated(true);
      return;
    }

    recoveryAttemptedRef.current = dataProductId;

    // Fetch history from backend
    const fetchHistory = async () => {
      try {
        console.log('[useSessionRecovery] Fetching history for session:', storedSessionId);
        const response = await api.get<HistoryResponse>(
          `/agent/history/${storedSessionId}`,
        );

        const historyMessages = response.messages;
        console.log('[useSessionRecovery] Got history with', historyMessages?.length ?? 0, 'messages');

        if (historyMessages && historyMessages.length > 0) {
          // Transform backend messages to ChatMessage format, filtering internal context.
          // Stagger timestamps far in the past to prevent false artifact-card matching:
          // MessageThread uses a 60s window to attach artifacts to messages by timestamp,
          // so hydrated messages must NOT share timestamps close to recent artifact createdAt.
          const baseTime = Date.now() - 86_400_000; // 24 hours ago
          const chatMessages: ChatMessage[] = historyMessages
            .filter((msg) => !msg.content.includes('[INTERNAL CONTEXT'))
            .map((msg, idx) => ({
              id: msg.id ?? `recovered-${idx}-${Date.now()}`,
              role: msg.role,
              content: msg.content,
              timestamp: msg.timestamp ?? new Date(baseTime + idx * 1000).toISOString(),
              toolCalls: msg.tool_calls?.map((tc) => ({
                name: tc.name,
                input: tc.input,
                result: tc.result,
              })),
            }));

          // Determine phase from response or data product
          const phase = (response.phase ?? dataProduct?.state?.current_phase ?? 'idle') as AgentPhase;

          hydrateFromHistory(chatMessages, storedSessionId, phase);
        } else {
          // No messages found, but still mark as hydrated
          setHydrated(true);
        }
      } catch (error) {
        // Failed to fetch history - log and mark as hydrated anyway
        // This allows auto-discovery to proceed for fresh sessions
        console.warn('[useSessionRecovery] Failed to recover session history:', error);
        setHydrated(true);
      }
    };

    void fetchHistory();
  }, [dataProductId, storedSessionId, sessionId, messages.length, hydrateFromHistory, setHydrated, dataProduct?.state?.current_phase]);

  return { isHydrated };
}

export { useSessionRecovery };
export type { UseSessionRecoveryReturn };
