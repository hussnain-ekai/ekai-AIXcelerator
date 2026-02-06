import { useAuthStore } from '@/stores/authStore';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const MAX_RETRIES = Number(process.env.NEXT_PUBLIC_SSE_MAX_RETRIES ?? '5');
const BASE_DELAY_MS = Number(process.env.NEXT_PUBLIC_SSE_BASE_DELAY_MS ?? '1000');

interface PipelineProgressData {
  step: string;
  label: string;
  status: 'running' | 'completed' | 'error';
  detail: string;
  current: number;
  total: number;
  step_index: number;
  total_steps: number;
  overall_pct: number;
}

interface SSEHandlers {
  onToken: (text: string) => void;
  onMessageDone: () => void;
  onToolCall: (toolName: string, toolInput: Record<string, unknown>) => void;
  onToolResult: (toolName: string, result: string) => void;
  onPhaseChange: (fromPhase: string, toPhase: string) => void;
  onArtifact: (artifactId: string, artifactType: string) => void;
  onApprovalRequest: (action: string, description: string, options: string[]) => void;
  onPipelineProgress: (data: PipelineProgressData) => void;
  onError: (error: string, message: string) => void;
  onDone: () => void;
}

interface SSEEvent {
  type: string;
  data: Record<string, unknown>;
}

function parseSSELine(line: string): SSEEvent | null {
  if (!line.startsWith('data: ')) {
    return null;
  }

  const jsonStr = line.slice(6);
  if (jsonStr === '[DONE]') {
    return { type: 'done', data: {} };
  }

  const parsed = JSON.parse(jsonStr) as SSEEvent;
  return parsed;
}

function dispatchEvent(event: SSEEvent, handlers: SSEHandlers): void {
  switch (event.type) {
    case 'token':
      handlers.onToken((event.data.content as string) ?? (event.data.text as string) ?? '');
      break;
    case 'message_done':
      handlers.onMessageDone();
      break;
    case 'tool_call':
      handlers.onToolCall(
        (event.data.tool as string) ?? (event.data.tool_name as string) ?? '',
        (event.data.input as Record<string, unknown>) ?? (event.data.tool_input as Record<string, unknown>) ?? {},
      );
      break;
    case 'tool_result':
      handlers.onToolResult(
        (event.data.tool as string) ?? (event.data.tool_name as string) ?? '',
        (event.data.output as string) ?? (event.data.result as string) ?? '',
      );
      break;
    case 'phase_change':
      handlers.onPhaseChange(
        (event.data.from as string) ?? (event.data.from_phase as string) ?? '',
        (event.data.to as string) ?? (event.data.to_phase as string) ?? '',
      );
      break;
    case 'artifact':
      handlers.onArtifact(
        (event.data.artifact_id as string) ?? '',
        (event.data.artifact_type as string) ?? '',
      );
      break;
    case 'approval_request':
      handlers.onApprovalRequest(
        (event.data.action as string) ?? '',
        (event.data.description as string) ?? '',
        (event.data.options as string[]) ?? [],
      );
      break;
    case 'pipeline_progress':
      handlers.onPipelineProgress(event.data as unknown as PipelineProgressData);
      break;
    case 'error':
      handlers.onError(
        (event.data.error as string) ?? 'UNKNOWN',
        (event.data.message as string) ?? 'An unknown error occurred',
      );
      break;
    case 'done':
      handlers.onDone();
      break;
    default:
      break;
  }
}

function connectSSE(
  sessionId: string,
  handlers: SSEHandlers,
): () => void {
  let retries = 0;
  let abortController = new AbortController();
  let isCleanedUp = false;

  async function connect(): Promise<void> {
    if (isCleanedUp) return;

    const user = useAuthStore.getState().user;
    const effectiveUser =
      user ?? (process.env.NODE_ENV === 'development' ? 'dev@localhost' : null);
    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
    };
    if (effectiveUser) {
      headers['Sf-Context-Current-User'] = effectiveUser;
    }

    abortController = new AbortController();

    const response = await fetch(
      `${API_BASE_URL}/agent/stream/${sessionId}`,
      {
        method: 'GET',
        headers,
        signal: abortController.signal,
      },
    );

    if (!response.ok) {
      throw new Error(`SSE connection failed: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('Response body is not readable');
    }

    const decoder = new TextDecoder();
    let buffer = '';

    retries = 0;

    while (!isCleanedUp) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed === '') continue;

        const event = parseSSELine(trimmed);
        if (event) {
          dispatchEvent(event, handlers);
          if (event.type === 'done') {
            return;
          }
        }
      }
    }
  }

  function attemptReconnect(): void {
    if (isCleanedUp || retries >= MAX_RETRIES) {
      if (retries >= MAX_RETRIES) {
        handlers.onError('MAX_RETRIES', 'Connection lost after maximum retry attempts');
      }
      return;
    }

    retries += 1;
    const delay = BASE_DELAY_MS * Math.pow(2, retries - 1);
    setTimeout(() => {
      connect().catch(() => attemptReconnect());
    }, delay);
  }

  connect().catch(() => attemptReconnect());

  return function cleanup(): void {
    isCleanedUp = true;
    abortController.abort();
  };
}

export { connectSSE };
export type { SSEHandlers, SSEEvent, PipelineProgressData };
