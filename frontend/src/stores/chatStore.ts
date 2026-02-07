import { create } from 'zustand';

type MessageRole = 'user' | 'assistant' | 'system';
type AgentPhase = 'discovery' | 'requirements' | 'generation' | 'validation' | 'publishing' | 'explorer' | 'idle';
type ArtifactType = 'erd' | 'yaml' | 'brd' | 'data_quality' | 'data_preview';

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
  truncateAfter: (messageId: string) => void;
  editMessage: (messageId: string, newContent: string) => void;
  clearMessages: () => void;
  reset: () => void;
  hydrateFromHistory: (messages: ChatMessage[], sessionId: string, phase: AgentPhase) => void;
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
};

export const useChatStore = create<ChatState>()((set) => ({
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
      // Deduplicate: skip if same id or same type already exists
      const exists = state.artifacts.some(
        (a) => a.id === artifact.id || a.type === artifact.type,
      );
      if (exists) return state;
      return { artifacts: [...state.artifacts, artifact] };
    }),

  setActivePanel: (panel: ArtifactType | null) =>
    set({ activePanel: panel }),

  setPipelineProgress: (progress: PipelineProgress | null) =>
    set({ pipelineProgress: progress }),

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
    }),

  reset: () => set(INITIAL_STATE),

  hydrateFromHistory: (messages: ChatMessage[], sessionId: string, phase: AgentPhase) =>
    set({
      messages,
      sessionId,
      currentPhase: phase,
      isHydrated: true,
    }),

  setHydrated: (hydrated: boolean) =>
    set({ isHydrated: hydrated }),
}));

export type {
  ChatMessage,
  ChatMessageAttachment,
  ToolCall,
  ChatArtifact,
  ChatState,
  MessageRole,
  AgentPhase,
  ArtifactType,
  PipelineProgress,
};
