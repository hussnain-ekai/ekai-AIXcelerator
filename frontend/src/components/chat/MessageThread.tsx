'use client';

import { useEffect, useRef } from 'react';
import { Box, CircularProgress, Paper, Typography } from '@mui/material';
import type { ChatMessage, ArtifactType } from '@/stores/chatStore';
import { useChatStore } from '@/stores/chatStore';
import { ArtifactCard } from './ArtifactCard';
import { DiscoveryProgress } from './DiscoveryProgress';

interface MessageThreadProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  onOpenArtifact?: (type: ArtifactType) => void;
}

const GOLD = '#D4A843';

/* ------------------------------------------------------------------ */
/*  Message content filters                                            */
/* ------------------------------------------------------------------ */

/** Messages that should be completely hidden from the user. */
function isHiddenMessage(message: ChatMessage): boolean {
  const text = typeof message.content === 'string' ? message.content : '';
  // Internal discovery context injected for the LLM — never for user display
  if (text.includes('[INTERNAL CONTEXT')) return true;
  return false;
}

/**
 * Condense messages that contain large code blocks (e.g. full YAML dumps).
 * Returns the summary text before the first code fence, stripping markdown.
 * If no code fence, returns original content unchanged.
 */
function condenseContent(content: string): { text: string; hasCodeBlock: boolean } {
  const fenceIndex = content.indexOf('```');
  if (fenceIndex === -1 || content.length < 500) {
    return { text: content, hasCodeBlock: false };
  }

  // Extract text before the first code fence
  let summary = content.slice(0, fenceIndex).trim();

  // Strip markdown formatting: **bold**, ### headings
  summary = summary.replace(/\*\*(.*?)\*\*/g, '$1');
  summary = summary.replace(/^#{1,6}\s+/gm, '');

  // If there's meaningful text after the last code fence, append it
  const lastFenceEnd = content.lastIndexOf('```');
  if (lastFenceEnd > fenceIndex) {
    const afterCode = content.slice(lastFenceEnd + 3).trim();
    // Only append if it's not too long and adds value
    if (afterCode.length > 0 && afterCode.length < 500) {
      const cleaned = afterCode.replace(/\*\*(.*?)\*\*/g, '$1').replace(/^#{1,6}\s+/gm, '');
      if (cleaned.length > 0) {
        summary = summary + '\n\n' + cleaned;
      }
    }
  }

  return { text: summary || 'Artifact generated.', hasCodeBlock: true };
}

const TOOL_DISPLAY_NAMES: Record<string, string> = {
  profile_table: 'Analyzing data patterns',
  query_information_schema: 'Reading data structure',
  update_erd: 'Building data map',
  classify_entity: 'Classifying data',
  upload_artifact: 'Saving results',
  save_quality_report: 'Generating quality report',
  execute_rcr_query: 'Querying data',
  query_erd_graph: 'Reading data map',
  save_brd: 'Saving requirements',
  validate_sql: 'Validating model',
  save_semantic_view: 'Saving semantic model',
  load_workspace_state: 'Loading workspace',
  create_semantic_view: 'Creating semantic model',
  create_cortex_agent: 'Publishing agent',
  grant_agent_access: 'Setting up access',
  log_agent_action: 'Recording action',
};

function getToolDisplayName(toolName: string): string {
  return TOOL_DISPLAY_NAMES[toolName] ?? 'Working';
}

function AgentMessage({
  message,
  onOpenArtifact,
}: {
  message: ChatMessage;
  onOpenArtifact?: (type: ArtifactType) => void;
}): React.ReactNode {
  // Collect artifact cards to show: explicit refs take priority, then timestamp-based
  const artifacts = useChatStore((state) => state.artifacts);
  let inlineArtifacts: { type: ArtifactType; title: string }[] = [];

  if (message.artifactRefs && message.artifactRefs.length > 0) {
    // Explicit artifact references on this message (e.g. from pipeline completion)
    inlineArtifacts = message.artifactRefs.map((refType) => {
      const match = artifacts.find((a) => a.type === refType);
      return { type: refType, title: match?.title ?? refType.toUpperCase() };
    });
  } else {
    // Fallback: match artifacts created within 60 seconds of this message
    const messageTime = new Date(message.timestamp).getTime();
    inlineArtifacts = artifacts
      .filter((a) => {
        const t = new Date(a.createdAt).getTime();
        return t >= messageTime && t <= messageTime + 60_000;
      })
      .map((a) => ({ type: a.type, title: a.title }));
  }

  // Extract text from content (defensive handling for structured content)
  const rawText = typeof message.content === 'string'
    ? message.content
    : (message.content as {text?: string})?.text ?? JSON.stringify(message.content);

  // Condense messages with large code blocks (e.g. YAML dumps)
  const { text: contentText, hasCodeBlock } = condenseContent(rawText);

  // If a code block was stripped and the message has save_semantic_view tool call,
  // ensure a yaml artifact card is shown
  if (hasCodeBlock && inlineArtifacts.every((a) => a.type !== 'yaml')) {
    const hasYamlTool = message.toolCalls?.some(
      (tc) => tc.name === 'save_semantic_view' || tc.name === 'create_semantic_view',
    );
    if (hasYamlTool) {
      inlineArtifacts.push({ type: 'yaml', title: 'Semantic View YAML' });
    }
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', maxWidth: '75%' }}>
      <Typography
        variant="caption"
        sx={{ fontWeight: 700, color: GOLD, mb: 0.5 }}
      >
        ekaiX
      </Typography>
      <Paper
        elevation={0}
        sx={{
          p: 2,
          bgcolor: 'background.paper',
          borderRadius: 2,
          border: 1,
          borderColor: 'divider',
          width: '100%',
        }}
      >
        <Typography
          variant="body2"
          sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.7 }}
        >
          {contentText}
        </Typography>
        {message.toolCalls && message.toolCalls.length > 0 && (
          <Box sx={{ mt: 1.5, display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                fontStyle: 'italic',
              }}
            >
              {message.toolCalls.length === 1
                ? getToolDisplayName(message.toolCalls[0]?.name ?? '')
                : `${message.toolCalls.length} steps completed`}
            </Typography>
          </Box>
        )}
        {/* Inline artifact cards — clickable, opens right panel */}
        {inlineArtifacts.length > 0 && onOpenArtifact && (
          <Box sx={{ mt: 1.5 }}>
            {inlineArtifacts.map((a) => (
              <ArtifactCard
                key={a.type}
                type={a.type}
                title={a.title}
                onClick={() => onOpenArtifact(a.type)}
              />
            ))}
          </Box>
        )}
      </Paper>
    </Box>
  );
}

function UserMessage({ message }: { message: ChatMessage }): React.ReactNode {
  // Extract text from content (defensive handling for structured content)
  const contentText = typeof message.content === 'string'
    ? message.content
    : (message.content as {text?: string})?.text ?? JSON.stringify(message.content);

  return (
    <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
      <Paper
        elevation={0}
        sx={{
          p: 2,
          bgcolor: 'background.paper',
          borderRadius: 2,
          border: 1,
          borderColor: 'divider',
          maxWidth: '75%',
        }}
      >
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
          {contentText}
        </Typography>
      </Paper>
    </Box>
  );
}

function SystemMessage({ message }: { message: ChatMessage }): React.ReactNode {
  // Extract text from content (defensive handling for structured content)
  const contentText = typeof message.content === 'string'
    ? message.content
    : (message.content as {text?: string})?.text ?? JSON.stringify(message.content);

  return (
    <Box sx={{ display: 'flex', justifyContent: 'center' }}>
      <Typography
        variant="caption"
        sx={{
          color: 'error.main',
          px: 2,
          py: 0.5,
          borderRadius: 1,
          bgcolor: 'action.hover',
        }}
      >
        {contentText}
      </Typography>
    </Box>
  );
}

function MessageBubble({
  message,
  onOpenArtifact,
}: {
  message: ChatMessage;
  onOpenArtifact?: (type: ArtifactType) => void;
}): React.ReactNode {
  switch (message.role) {
    case 'assistant':
      return <AgentMessage message={message} onOpenArtifact={onOpenArtifact} />;
    case 'user':
      return <UserMessage message={message} />;
    case 'system':
      return <SystemMessage message={message} />;
    default:
      return null;
  }
}

export function MessageThread({
  messages,
  isStreaming,
  onOpenArtifact,
}: MessageThreadProps): React.ReactNode {
  const bottomRef = useRef<HTMLDivElement>(null);
  const pipelineProgress = useChatStore((state) => state.pipelineProgress);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming, pipelineProgress]);

  return (
    <Box
      sx={{
        flex: 1,
        overflow: 'auto',
        px: 3,
        py: 2,
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
      }}
    >
      {messages.length === 0 && !pipelineProgress && (
        <Box
          sx={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'text.secondary',
          }}
        >
          <Typography variant="h6" gutterBottom>
            Start a conversation
          </Typography>
          <Typography variant="body2">
            Send a message to begin working on your data product.
          </Typography>
        </Box>
      )}

      {messages.filter((m) => !isHiddenMessage(m)).map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          onOpenArtifact={onOpenArtifact}
        />
      ))}

      {/* Live pipeline progress — only while steps are running */}
      {pipelineProgress && <DiscoveryProgress progress={pipelineProgress} />}

      {/* Thinking spinner — shown when streaming and pipeline is not actively running */}
      {isStreaming && !pipelineProgress && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, pl: 1 }}>
          <CircularProgress size={16} sx={{ color: GOLD }} />
          <Typography variant="caption" color="text.secondary">
            ekaiX is thinking...
          </Typography>
        </Box>
      )}

      <div ref={bottomRef} />
    </Box>
  );
}
