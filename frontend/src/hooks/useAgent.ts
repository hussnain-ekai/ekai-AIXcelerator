'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { connectSSE } from '@/lib/sse';
import type { SSEHandlers } from '@/lib/sse';
import { api } from '@/lib/api';
import { useChatStore } from '@/stores/chatStore';
import type { AgentPhase, ArtifactType } from '@/stores/chatStore';

interface AgentResponse {
  session_id: string;
  message_id: string;
  status: 'processing' | 'completed' | 'error';
}

interface UseAgentOptions {
  dataProductId: string;
}

interface UseAgentReturn {
  sendMessage: (content: string) => Promise<void>;
  interrupt: () => Promise<void>;
  isConnected: boolean;
}

function useAgent({ dataProductId }: UseAgentOptions): UseAgentReturn {
  const [isConnected, setIsConnected] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);
  const {
    sessionId,
    setSessionId,
    addMessage,
    updateLastAssistantMessage,
    finalizeLastMessage,
    addToolCallToLastMessage,
    setStreaming,
    setPhase,
    addArtifact,
    setPipelineProgress,
  } = useChatStore();

  const connectToStream = useCallback(
    (sid: string) => {
      if (cleanupRef.current) {
        cleanupRef.current();
      }

      const handlers: SSEHandlers = {
        onToken: (text: string) => {
          const lastMessage = useChatStore.getState().messages.at(-1);
          if (lastMessage?.role === 'assistant' && lastMessage.isStreaming) {
            updateLastAssistantMessage(lastMessage.content + text);
          } else {
            addMessage({
              id: crypto.randomUUID(),
              role: 'assistant',
              content: text,
              timestamp: new Date().toISOString(),
              isStreaming: true,
            });
          }
        },
        onMessageDone: () => {
          finalizeLastMessage();
        },
        onToolCall: (toolName: string) => {
          addToolCallToLastMessage({ name: toolName, input: {} });
        },
        onToolResult: (_toolName: string, _result: string) => {
          // Tool results are processed as part of the streaming response
        },
        onPhaseChange: (_from: string, to: string) => {
          setPhase(to as AgentPhase);
        },
        onArtifact: (artifactId: string, artifactType: string) => {
          addArtifact({
            id: artifactId,
            type: artifactType as ArtifactType,
            title: artifactType.toUpperCase(),
            dataProductId,
            createdAt: new Date().toISOString(),
          });
        },
        onApprovalRequest: (_action: string, description: string, _options: string[]) => {
          addMessage({
            id: crypto.randomUUID(),
            role: 'assistant',
            content: description,
            timestamp: new Date().toISOString(),
          });
        },
        onPipelineProgress: (data) => {
          const isComplete = data.step === 'artifacts' && data.status === 'completed';

          if (isComplete) {
            // Pipeline finished — inject a proper chat message with artifact cards
            setPipelineProgress(null);
            addMessage({
              id: crypto.randomUUID(),
              role: 'assistant',
              content: "I've reviewed your data tables, mapped the relationships between them, and checked the overall data quality.",
              timestamp: new Date().toISOString(),
              artifactRefs: ['erd', 'data_quality'],
            });
          } else {
            setPipelineProgress({
              step: data.step,
              label: data.label,
              status: data.status,
              detail: data.detail,
              current: data.current,
              total: data.total,
              stepIndex: data.step_index,
              totalSteps: data.total_steps,
              overallPct: data.overall_pct,
            });
          }
        },
        onError: (_code: string, message: string) => {
          setStreaming(false);
          setPipelineProgress(null);
          addMessage({
            id: crypto.randomUUID(),
            role: 'system',
            content: `Error: ${message}`,
            timestamp: new Date().toISOString(),
          });
        },
        onDone: () => {
          setStreaming(false);
          setIsConnected(false);
          setPipelineProgress(null);
        },
      };

      cleanupRef.current = connectSSE(sid, handlers);
      setIsConnected(true);
    },
    [
      dataProductId,
      addMessage,
      updateLastAssistantMessage,
      finalizeLastMessage,
      addToolCallToLastMessage,
      setStreaming,
      setPhase,
      addArtifact,
      setPipelineProgress,
    ],
  );

  const sendMessage = useCallback(
    async (content: string) => {
      // Don't show internal trigger messages in the chat
      const isInternalTrigger = content === '__START_DISCOVERY__';
      if (!isInternalTrigger) {
        addMessage({
          id: crypto.randomUUID(),
          role: 'user',
          content,
          timestamp: new Date().toISOString(),
        });
      }

      setStreaming(true);

      // Use existing session ID from store (may have been recovered from history)
      // or generate a new one for fresh sessions
      const existingSessionId = useChatStore.getState().sessionId;
      const sid = existingSessionId ?? sessionId ?? crypto.randomUUID();
      if (!sessionId) {
        setSessionId(sid);
      }

      const response = await api.post<AgentResponse>(
        `/agent/message`,
        {
          session_id: sid,
          message: content,
          data_product_id: dataProductId,
        },
      );

      connectToStream(response.session_id);
    },
    [dataProductId, sessionId, addMessage, setStreaming, setSessionId, connectToStream],
  );

  const interrupt = useCallback(async () => {
    if (sessionId) {
      await api.post(`/agent/interrupt/${sessionId}`);
      setStreaming(false);
    }
  }, [sessionId, setStreaming]);

  useEffect(() => {
    return () => {
      if (cleanupRef.current) {
        cleanupRef.current();
      }
    };
  }, []);

  return { sendMessage, interrupt, isConnected };
}

export { useAgent };
export type { UseAgentOptions, UseAgentReturn };
