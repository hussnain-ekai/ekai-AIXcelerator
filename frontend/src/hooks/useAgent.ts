'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { connectSSE } from '@/lib/sse';
import type { SSEHandlers } from '@/lib/sse';
import { api } from '@/lib/api';
import { useChatStore } from '@/stores/chatStore';
import type { AgentPhase, ArtifactType, ChatMessageAttachment } from '@/stores/chatStore';

interface AgentResponse {
  session_id: string;
  message_id: string;
  status: 'processing' | 'completed' | 'error';
}

interface UseAgentOptions {
  dataProductId: string;
}

interface UseAgentReturn {
  sendMessage: (content: string, files?: File[]) => Promise<void>;
  retryMessage: (opts: { messageId?: string; editedContent?: string; originalContent?: string }) => Promise<void>;
  interrupt: () => Promise<void>;
  isConnected: boolean;
}

/** Convert a File to base64 string. */
async function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // Strip the data URL prefix (e.g. "data:image/png;base64,")
      const base64 = result.split(',')[1] ?? '';
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
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
    setPipelineRunning,
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
          // Only inject a transition message when the phase ACTUALLY changes.
          // Skip if we're re-entering the same phase (e.g. requirements Q&A → BRD).
          const prevPhase = useChatStore.getState().currentPhase;
          setPhase(to as AgentPhase);

          if (to === prevPhase) return;

          const PHASE_LABELS: Record<string, string> = {
            requirements: 'Moving on to capture your business requirements...',
            generation: 'Generating your semantic model...',
            validation: 'Validating the semantic model against your data...',
            publishing: 'Preparing to publish your model...',
          };
          const label = PHASE_LABELS[to];
          if (label) {
            finalizeLastMessage();
            addMessage({
              id: crypto.randomUUID(),
              role: 'assistant',
              content: label,
              timestamp: new Date().toISOString(),
            });
          }
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
            // Show "Analysis complete" state briefly before clearing
            setPipelineProgress({
              step: 'artifacts',
              label: 'Analysis complete',
              status: 'completed',
              detail: '',
              current: data.total_steps,
              total: data.total_steps,
              stepIndex: data.total_steps - 1,
              totalSteps: data.total_steps,
              overallPct: 100,
            });

            // After 1.2s, remove progress and inject discovery message with artifacts
            setTimeout(() => {
              setPipelineProgress(null);
              setPipelineRunning(false);
              // Dedup: skip if last message already has this content
              const msgs = useChatStore.getState().messages;
              const lastMsg = msgs[msgs.length - 1];
              const discoveryText = "I've reviewed your data tables, mapped the relationships between them, and checked the overall data quality.";
              if (!lastMsg || lastMsg.content !== discoveryText) {
                addMessage({
                  id: crypto.randomUUID(),
                  role: 'assistant',
                  content: discoveryText,
                  timestamp: new Date().toISOString(),
                  artifactRefs: ['erd', 'data_quality'],
                });
              }
            }, 1200);
          } else {
            // Mark pipeline as running on first progress event
            if (!useChatStore.getState().pipelineRunning) {
              setPipelineRunning(true);
            }
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
          setPipelineRunning(false);
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
          setPipelineRunning(false);
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
      setPipelineRunning,
    ],
  );

  const sendMessage = useCallback(
    async (content: string, files?: File[]) => {
      // Don't show internal trigger messages in the chat
      const isInternalTrigger = content === '__START_DISCOVERY__' || content === '__RERUN_DISCOVERY__';

      // Build attachment metadata for display in chat
      const chatAttachments: ChatMessageAttachment[] = [];
      if (files && files.length > 0) {
        for (const file of files) {
          const att: ChatMessageAttachment = {
            filename: file.name,
            contentType: file.type,
          };
          if (file.type.startsWith('image/')) {
            att.thumbnailUrl = URL.createObjectURL(file);
          }
          chatAttachments.push(att);
        }
      }

      if (!isInternalTrigger) {
        addMessage({
          id: crypto.randomUUID(),
          role: 'user',
          content,
          timestamp: new Date().toISOString(),
          attachments: chatAttachments.length > 0 ? chatAttachments : undefined,
        });
      }

      setStreaming(true);

      // Use existing session ID from store (may have been recovered from history)
      // or generate a new one for fresh sessions.
      // Always read from getState() — the closure's `sessionId` can be stale
      // after clearMessages() resets it in the same render cycle.
      const existingSessionId = useChatStore.getState().sessionId;
      const sid = existingSessionId ?? crypto.randomUUID();
      if (!existingSessionId) {
        setSessionId(sid);
      }

      // Encode files to base64 for the AI service
      const fileContents: { filename: string; content_type: string; base64_data: string }[] = [];
      if (files && files.length > 0) {
        for (const file of files) {
          const base64 = await fileToBase64(file);
          fileContents.push({
            filename: file.name,
            content_type: file.type,
            base64_data: base64,
          });
        }
      }

      const response = await api.post<AgentResponse>(
        `/agent/message`,
        {
          session_id: sid,
          message: content,
          data_product_id: dataProductId,
          ...(fileContents.length > 0 ? { file_contents: fileContents } : {}),
        },
      );

      connectToStream(response.session_id);
    },
    [dataProductId, addMessage, setStreaming, setSessionId, connectToStream],
  );

  const retryMessage = useCallback(
    async (opts: { messageId?: string; editedContent?: string; originalContent?: string }) => {
      const sid = useChatStore.getState().sessionId;
      if (!sid) return;

      setStreaming(true);

      const response = await api.post<AgentResponse>(
        `/agent/retry`,
        {
          session_id: sid,
          data_product_id: dataProductId,
          message_id: opts.messageId,
          edited_content: opts.editedContent,
          original_content: opts.originalContent,
        },
      );

      connectToStream(response.session_id);
    },
    [dataProductId, setStreaming, connectToStream],
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

  return { sendMessage, retryMessage, interrupt, isConnected };
}

export { useAgent };
export type { UseAgentOptions, UseAgentReturn };
