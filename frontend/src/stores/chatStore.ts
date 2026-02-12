import { createStore } from 'zustand/vanilla';

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
};

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
        isHydrated: false,
        pipelineProgress: null,
        dataTier: null,
      }),

    reset: () => set(INITIAL_STATE),

    hydrateFromHistory: (messages: ChatMessage[], sessionId: string, phase: AgentPhase, dataTier?: DataTier) =>
      set({
        messages,
        sessionId,
        currentPhase: phase,
        isHydrated: true,
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
};
