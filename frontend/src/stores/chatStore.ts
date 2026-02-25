import { createStore } from 'zustand/vanilla';
import type { AnswerContract, AnswerTrustState } from '@/lib/answerContract';

type MessageRole = 'user' | 'assistant' | 'system';
type AgentPhase = 'discovery' | 'prepare' | 'requirements' | 'modeling' | 'generation' | 'validation' | 'publishing' | 'explorer' | 'idle';
type ArtifactType = 'erd' | 'yaml' | 'brd' | 'data_quality' | 'data_preview' | 'data_description' | 'data_catalog' | 'business_glossary' | 'metrics' | 'validation_rules' | 'lineage';
type DataTier = 'gold' | 'silver' | 'bronze' | null;

interface ChatMessageAttachment {
  filename: string;
  contentType: string;
  thumbnailUrl?: string;
}

interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  toolCalls?: ToolCall[];
  isStreaming?: boolean;
  /** Per-answer trust contract used for source/confidence/exactness rendering. */
  answerContract?: AnswerContract | null;
  /** Artifact types to render as clickable cards inline with this message. */
  artifactRefs?: ArtifactType[];
  /** File attachments on user messages. */
  attachments?: ChatMessageAttachment[];
}

interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  result?: string;
}

interface ChatArtifact {
  id: string;
  type: ArtifactType;
  title: string;
  dataProductId: string;
  createdAt: string;
  version?: number;
}

interface PipelineProgress {
  step: string;
  label: string;
  status: 'running' | 'completed' | 'error';
  detail: string;
  current: number;
  total: number;
  stepIndex: number;
  totalSteps: number;
  overallPct: number;
}

interface ReasoningUpdate {
  message: string;
  timestamp: string;
  source: 'llm' | 'fallback';
}

interface ChatState {
  messages: ChatMessage[];
  isStreaming: boolean;
  currentPhase: AgentPhase;
  sessionId: string | null;
  artifacts: ChatArtifact[];
  activePanel: ArtifactType | null;
  isHydrated: boolean;
  pipelineProgress: PipelineProgress | null;
  /** True while discovery pipeline is running — blocks artifact hydration from DB. */
  pipelineRunning: boolean;
  /** Data maturity tier from discovery pipeline — controls phase stepper display. */
  dataTier: DataTier;
  /** Short user-safe progress snippet streamed from assistant output. */
  reasoningUpdate: ReasoningUpdate | null;
  /** Rolling list of recent reasoning updates for live details panel. */
  reasoningLog: ReasoningUpdate[];
  /** Latest normalized answer contract for trust/exactness UX rendering. */
  latestAnswerContract: AnswerContract | null;
  /** Fast access trust state for top-level UI status badges. */
  answerTrustState: AnswerTrustState | null;
  addMessage: (message: ChatMessage) => void;
  updateLastAssistantMessage: (content: string) => void;
  finalizeLastMessage: () => void;
  addToolCallToLastMessage: (toolCall: ToolCall) => void;
  setStreaming: (streaming: boolean) => void;
  setPhase: (phase: AgentPhase) => void;
  setSessionId: (sessionId: string) => void;
  addArtifact: (artifact: ChatArtifact) => void;
  setActivePanel: (panel: ArtifactType | null) => void;
  setPipelineProgress: (progress: PipelineProgress | null) => void;
  setPipelineRunning: (running: boolean) => void;
  setDataTier: (tier: DataTier) => void;
  setReasoningUpdate: (message: string | null, source?: 'llm' | 'fallback') => void;
  clearReasoningLog: () => void;
  setAnswerContract: (contract: AnswerContract | null) => void;
  attachAnswerContractToLastAssistant: (contract: AnswerContract) => void;
  clearAnswerContract: () => void;
  attachArtifactToLastAssistant: (artifactType: ArtifactType) => void;
  truncateAfter: (messageId: string) => void;
  editMessage: (messageId: string, newContent: string) => void;
  clearMessages: () => void;
  reset: () => void;
  hydrateFromHistory: (messages: ChatMessage[], sessionId: string, phase: AgentPhase, dataTier?: DataTier) => void;
  setHydrated: (hydrated: boolean) => void;
}

const INITIAL_STATE = {
  messages: [] as ChatMessage[],
  isStreaming: false,
  currentPhase: 'idle' as AgentPhase,
  sessionId: null as string | null,
  artifacts: [] as ChatArtifact[],
  activePanel: null as ArtifactType | null,
  isHydrated: false,
  pipelineProgress: null as PipelineProgress | null,
  pipelineRunning: false,
  dataTier: null as DataTier,
  reasoningUpdate: null as ReasoningUpdate | null,
  reasoningLog: [] as ReasoningUpdate[],
  latestAnswerContract: null as AnswerContract | null,
  answerTrustState: null as AnswerTrustState | null,
};

function withObservedAt(contract: AnswerContract): AnswerContract {
  const observedAt =
    typeof contract.metadata?.observed_at === 'string'
      ? contract.metadata.observed_at
      : new Date().toISOString();
  return {
    ...contract,
    metadata: {
      ...contract.metadata,
      observed_at: observedAt,
    },
  };
}

export type ChatStore = ReturnType<typeof createChatStore>;

export function createChatStore() {
  return createStore<ChatState>()((set) => ({
    ...INITIAL_STATE,

    addMessage: (message: ChatMessage) =>
      set((state) => ({
        messages: [...state.messages, message],
      })),

    updateLastAssistantMessage: (content: string) =>
      set((state) => {
        const messages = [...state.messages];
        const lastIndex = messages.length - 1;
        const lastMessage = messages[lastIndex];

        if (lastMessage && lastMessage.role === 'assistant') {
          messages[lastIndex] = { ...lastMessage, content };
        }

        return { messages };
      }),

    finalizeLastMessage: () =>
      set((state) => {
        const messages = [...state.messages];
        const lastIndex = messages.length - 1;
        const lastMessage = messages[lastIndex];

        if (lastMessage && lastMessage.role === 'assistant' && lastMessage.isStreaming) {
          // Dedup: if this message duplicates the previous finalized assistant message, remove it
          for (let i = lastIndex - 1; i >= 0; i--) {
            const prev = messages[i];
            if (prev && prev.role === 'assistant' && !prev.isStreaming) {
              const prevText = prev.content.trim();
              const currText = lastMessage.content.trim();
              if (
                prevText.length > 50 &&
                currText.length > 50 &&
                prevText.slice(0, 200) === currText.slice(0, 200)
              ) {
                messages.splice(lastIndex, 1);
                return { messages };
              }
              break;
            }
          }
          messages[lastIndex] = { ...lastMessage, isStreaming: false };
        }

        return { messages };
      }),

    addToolCallToLastMessage: (toolCall: ToolCall) =>
      set((state) => {
        const messages = [...state.messages];
        const lastIndex = messages.length - 1;
        const lastMessage = messages[lastIndex];

        if (lastMessage && lastMessage.role === 'assistant') {
          const existingCalls = lastMessage.toolCalls ?? [];
          messages[lastIndex] = {
            ...lastMessage,
            toolCalls: [...existingCalls, toolCall],
          };
        }

        return { messages };
      }),

    setStreaming: (streaming: boolean) =>
      set({ isStreaming: streaming }),

    setPhase: (phase: AgentPhase) =>
      set({ currentPhase: phase }),

    setSessionId: (sessionId: string) =>
      set({ sessionId }),

    addArtifact: (artifact: ChatArtifact) =>
      set((state) => {
        // If same id exists, skip entirely
        if (state.artifacts.some((a) => a.id === artifact.id)) return state;
        // If same type exists, replace it with the newer version
        const existingIdx = state.artifacts.findIndex((a) => a.type === artifact.type);
        if (existingIdx !== -1) {
          const updated = [...state.artifacts];
          updated[existingIdx] = artifact;
          return { artifacts: updated };
        }
        return { artifacts: [...state.artifacts, artifact] };
      }),

    setActivePanel: (panel: ArtifactType | null) =>
      set({ activePanel: panel }),

    setPipelineProgress: (progress: PipelineProgress | null) =>
      set({ pipelineProgress: progress }),

    setPipelineRunning: (running: boolean) =>
      set({ pipelineRunning: running }),

    setDataTier: (tier: DataTier) =>
      set({ dataTier: tier }),

    setReasoningUpdate: (message: string | null, source: 'llm' | 'fallback' = 'fallback') =>
      set((state) => {
        if (!message) {
          return { reasoningUpdate: null };
        }
        const update: ReasoningUpdate = {
          message,
          timestamp: new Date().toISOString(),
          source,
        };
        const last = state.reasoningLog[state.reasoningLog.length - 1];
        const isDuplicate =
          !!last &&
          last?.message === update.message &&
          last?.source === update.source;
        const nextLog = isDuplicate
          ? state.reasoningLog
          : [...state.reasoningLog, update].slice(-8);

        return {
          reasoningUpdate: update,
          reasoningLog: nextLog,
        };
      }),

    clearReasoningLog: () =>
      set({
        reasoningUpdate: null,
        reasoningLog: [],
      }),

    setAnswerContract: (contract: AnswerContract | null) =>
      set(() => {
        const normalized = contract ? withObservedAt(contract) : null;
        return {
          latestAnswerContract: normalized,
          answerTrustState: normalized?.trust_state ?? null,
        };
      }),

    attachAnswerContractToLastAssistant: (contract: AnswerContract) =>
      set((state) => {
        const normalized = withObservedAt(contract);
        const messages = [...state.messages];
        for (let i = messages.length - 1; i >= 0; i -= 1) {
          const message = messages[i];
          if (message && message.role === 'assistant') {
            messages[i] = { ...message, answerContract: normalized };
            return {
              messages,
              latestAnswerContract: normalized,
              answerTrustState: normalized.trust_state,
            };
          }
        }
        return {
          latestAnswerContract: normalized,
          answerTrustState: normalized.trust_state,
        };
      }),

    clearAnswerContract: () =>
      set({
        latestAnswerContract: null,
        answerTrustState: null,
      }),

    attachArtifactToLastAssistant: (artifactType: ArtifactType) =>
      set((state) => {
        const messages = [...state.messages];
        // Find the last assistant message (streaming or finalized)
        for (let i = messages.length - 1; i >= 0; i--) {
          const msg = messages[i];
          if (msg && msg.role === 'assistant') {
            const existing = msg.artifactRefs ?? [];
            if (!existing.includes(artifactType)) {
              messages[i] = { ...msg, artifactRefs: [...existing, artifactType] };
            }
            return { messages };
          }
        }
        return state;
      }),

    truncateAfter: (messageId: string) =>
      set((state) => {
        const idx = state.messages.findIndex((m) => m.id === messageId);
        if (idx === -1) return state;
        return { messages: state.messages.slice(0, idx + 1) };
      }),

    editMessage: (messageId: string, newContent: string) =>
      set((state) => {
        const messages = state.messages.map((m) =>
          m.id === messageId ? { ...m, content: newContent } : m,
        );
        return { messages };
      }),

    clearMessages: () =>
      set({
        messages: [],
        artifacts: [],
        currentPhase: 'idle',
        sessionId: null,
        isStreaming: false,
        isHydrated: false,
        pipelineProgress: null,
        pipelineRunning: false,
        activePanel: null,
        dataTier: null,
        reasoningUpdate: null,
        reasoningLog: [],
        latestAnswerContract: null,
        answerTrustState: null,
      }),

    reset: () => set(INITIAL_STATE),

    hydrateFromHistory: (messages: ChatMessage[], sessionId: string, phase: AgentPhase, dataTier?: DataTier) =>
      set({
        messages,
        sessionId,
        currentPhase: phase,
        isStreaming: false,
        isHydrated: true,
        pipelineProgress: null,
        pipelineRunning: false,
        reasoningUpdate: null,
        reasoningLog: [],
        latestAnswerContract: null,
        answerTrustState: null,
        ...(dataTier !== undefined ? { dataTier } : {}),
      }),

    setHydrated: (hydrated: boolean) =>
      set({ isHydrated: hydrated }),
  }));
}

export type {
  ChatMessage,
  ChatMessageAttachment,
  ToolCall,
  ChatArtifact,
  ChatState,
  MessageRole,
  AgentPhase,
  ArtifactType,
  DataTier,
  PipelineProgress,
  ReasoningUpdate,
};
