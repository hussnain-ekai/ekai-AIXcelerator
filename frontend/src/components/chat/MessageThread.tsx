'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box,
  Chip,
  CircularProgress,
  IconButton,
  Paper,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import CheckOutlined from '@mui/icons-material/CheckOutlined';
import CloseOutlined from '@mui/icons-material/CloseOutlined';
import ContentCopyOutlined from '@mui/icons-material/ContentCopyOutlined';
import EditOutlined from '@mui/icons-material/EditOutlined';
import InsertDriveFileOutlined from '@mui/icons-material/InsertDriveFileOutlined';
import RefreshOutlined from '@mui/icons-material/RefreshOutlined';
import type { ChatMessage, ArtifactType } from '@/stores/chatStore';
import { useChatStore } from '@/stores/chatStoreProvider';
import { ArtifactCard } from './ArtifactCard';
import { DiscoveryProgress } from './DiscoveryProgress';

interface MessageThreadProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  onOpenArtifact?: (type: ArtifactType) => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onRetryMessage?: (messageId: string) => void;
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
 * Condense messages that contain large code blocks (e.g. full YAML dumps)
 * or raw BRD text (between ---BEGIN BRD--- and ---END BRD--- markers).
 * Returns the summary text, stripping the large embedded content.
 */
function condenseContent(content: string): { text: string; hasCodeBlock: boolean } {
  let text = content;
  let condensed = false;

  // Strip BRD inline text — show only the summary before it
  const brdStart = text.indexOf('---BEGIN BRD---');
  if (brdStart !== -1) {
    const brdEnd = text.indexOf('---END BRD---');
    const before = text.slice(0, brdStart).trim();
    const after = brdEnd !== -1 ? text.slice(brdEnd + '---END BRD---'.length).trim() : '';
    text = before + (after ? '\n\n' + after : '');
    condensed = true;
  }

  // Strip code blocks (YAML dumps etc.)
  const fenceIndex = text.indexOf('```');
  if (fenceIndex !== -1 && text.length >= 500) {
    let summary = text.slice(0, fenceIndex).trim();
    summary = summary.replace(/\*\*(.*?)\*\*/g, '$1');
    summary = summary.replace(/^#{1,6}\s+/gm, '');

    const lastFenceEnd = text.lastIndexOf('```');
    if (lastFenceEnd > fenceIndex) {
      const afterCode = text.slice(lastFenceEnd + 3).trim();
      if (afterCode.length > 0 && afterCode.length < 500) {
        const cleaned = afterCode.replace(/\*\*(.*?)\*\*/g, '$1').replace(/^#{1,6}\s+/gm, '');
        if (cleaned.length > 0) {
          summary = summary + '\n\n' + cleaned;
        }
      }
    }

    text = summary || 'Artifact generated.';
    condensed = true;
  }

  return { text, hasCodeBlock: condensed };
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
  query_cortex_agent: 'Asking the published agent',
  fetch_documentation: 'Reading documentation',
  list_cortex_agents: 'Checking for published agents',
  validate_semantic_view_yaml: 'Validating model',
  get_latest_semantic_view: 'Loading semantic model',
  update_validation_status: 'Updating status',
  get_latest_brd: 'Loading requirements',
};

function getToolDisplayName(toolName: string): string {
  return TOOL_DISPLAY_NAMES[toolName] ?? 'Working';
}

/* ------------------------------------------------------------------ */
/*  Action button shared styles                                        */
/* ------------------------------------------------------------------ */

const ACTION_ICON_SX = {
  fontSize: 16,
  color: 'text.secondary',
} as const;

const ACTION_BTN_SX = {
  p: 0.5,
  '&:hover': { bgcolor: 'action.hover' },
} as const;

/* ------------------------------------------------------------------ */
/*  AgentMessage                                                       */
/* ------------------------------------------------------------------ */

function AgentMessage({
  message,
  onOpenArtifact,
  onRetry,
  isStreaming,
}: {
  message: ChatMessage;
  onOpenArtifact?: (type: ArtifactType) => void;
  onRetry?: (messageId: string) => void;
  isStreaming: boolean;
}): React.ReactNode {
  const [copied, setCopied] = useState(false);

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

  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(rawText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [rawText]);

  const handleRetry = useCallback(() => {
    onRetry?.(message.id);
  }, [onRetry, message.id]);

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-start',
        maxWidth: '75%',
        '& .message-actions': { opacity: 0, transition: 'opacity 150ms' },
        '&:hover .message-actions': { opacity: 1 },
      }}
    >
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
            {inlineArtifacts.map((a, idx) => (
              <ArtifactCard
                key={`${a.type}-${idx}`}
                type={a.type}
                title={a.title}
                onClick={() => onOpenArtifact(a.type)}
              />
            ))}
          </Box>
        )}
      </Paper>
      {/* Action buttons */}
      <Box
        className="message-actions"
        sx={{ display: 'flex', gap: 0.5, mt: 0.5, ml: 0.5 }}
      >
        <Tooltip title={copied ? 'Copied' : 'Copy'} placement="top">
          <IconButton onClick={handleCopy} sx={ACTION_BTN_SX} size="small">
            {copied
              ? <CheckOutlined sx={{ ...ACTION_ICON_SX, color: 'success.main' }} />
              : <ContentCopyOutlined sx={ACTION_ICON_SX} />
            }
          </IconButton>
        </Tooltip>
        <Tooltip title="Retry" placement="top">
          <span>
            <IconButton
              onClick={handleRetry}
              disabled={isStreaming}
              sx={ACTION_BTN_SX}
              size="small"
            >
              <RefreshOutlined sx={ACTION_ICON_SX} />
            </IconButton>
          </span>
        </Tooltip>
      </Box>
    </Box>
  );
}

/* ------------------------------------------------------------------ */
/*  UserMessage                                                        */
/* ------------------------------------------------------------------ */

function UserMessage({
  message,
  onEdit,
  onRetry,
  isStreaming,
}: {
  message: ChatMessage;
  onEdit?: (messageId: string, newContent: string) => void;
  onRetry?: (messageId: string) => void;
  isStreaming: boolean;
}): React.ReactNode {
  const [isEditing, setIsEditing] = useState(false);
  const [editText, setEditText] = useState('');

  // Extract text from content (defensive handling for structured content)
  const contentText = typeof message.content === 'string'
    ? message.content
    : (message.content as {text?: string})?.text ?? JSON.stringify(message.content);

  const handleStartEdit = useCallback(() => {
    setEditText(contentText);
    setIsEditing(true);
  }, [contentText]);

  const handleCancelEdit = useCallback(() => {
    setIsEditing(false);
    setEditText('');
  }, []);

  const handleSaveEdit = useCallback(() => {
    const trimmed = editText.trim();
    if (trimmed.length === 0 || trimmed === contentText) {
      setIsEditing(false);
      return;
    }
    onEdit?.(message.id, trimmed);
    setIsEditing(false);
  }, [editText, contentText, onEdit, message.id]);

  const handleRetry = useCallback(() => {
    onRetry?.(message.id);
  }, [onRetry, message.id]);

  const handleEditKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSaveEdit();
      }
      if (e.key === 'Escape') {
        handleCancelEdit();
      }
    },
    [handleSaveEdit, handleCancelEdit],
  );

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        '& .message-actions': { opacity: 0, transition: 'opacity 150ms' },
        '&:hover .message-actions': { opacity: 1 },
      }}
    >
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
        {isEditing ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            <TextField
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={handleEditKeyDown}
              multiline
              maxRows={6}
              size="small"
              fullWidth
              autoFocus
              sx={{ '& .MuiOutlinedInput-root': { borderRadius: 1 } }}
            />
            <Box sx={{ display: 'flex', gap: 0.5, justifyContent: 'flex-end' }}>
              <IconButton onClick={handleCancelEdit} size="small" sx={ACTION_BTN_SX}>
                <CloseOutlined sx={ACTION_ICON_SX} />
              </IconButton>
              <IconButton onClick={handleSaveEdit} size="small" sx={ACTION_BTN_SX}>
                <CheckOutlined sx={{ ...ACTION_ICON_SX, color: 'primary.main' }} />
              </IconButton>
            </Box>
          </Box>
        ) : (
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
            {contentText}
          </Typography>
        )}
        {/* Attachment chips */}
        {message.attachments && message.attachments.length > 0 && (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 1 }}>
            {message.attachments.map((att, idx) => (
              att.thumbnailUrl ? (
                <Box
                  key={idx}
                  component="img"
                  src={att.thumbnailUrl}
                  alt={att.filename}
                  sx={{
                    width: 64,
                    height: 64,
                    objectFit: 'cover',
                    borderRadius: 1,
                    border: 1,
                    borderColor: 'divider',
                  }}
                />
              ) : (
                <Chip
                  key={idx}
                  icon={<InsertDriveFileOutlined sx={{ fontSize: 16 }} />}
                  label={att.filename}
                  size="small"
                  variant="outlined"
                  sx={{ maxWidth: 200 }}
                />
              )
            ))}
          </Box>
        )}
      </Paper>
      {/* Action buttons */}
      {!isEditing && (
        <Box
          className="message-actions"
          sx={{ display: 'flex', gap: 0.5, mt: 0.5, mr: 0.5 }}
        >
          <Tooltip title="Edit" placement="top">
            <span>
              <IconButton
                onClick={handleStartEdit}
                disabled={isStreaming}
                sx={ACTION_BTN_SX}
                size="small"
              >
                <EditOutlined sx={ACTION_ICON_SX} />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Retry" placement="top">
            <span>
              <IconButton
                onClick={handleRetry}
                disabled={isStreaming}
                sx={ACTION_BTN_SX}
                size="small"
              >
                <RefreshOutlined sx={ACTION_ICON_SX} />
              </IconButton>
            </span>
          </Tooltip>
        </Box>
      )}
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
  onEditMessage,
  onRetryMessage,
  isStreaming,
}: {
  message: ChatMessage;
  onOpenArtifact?: (type: ArtifactType) => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onRetryMessage?: (messageId: string) => void;
  isStreaming: boolean;
}): React.ReactNode {
  switch (message.role) {
    case 'assistant':
      return (
        <AgentMessage
          message={message}
          onOpenArtifact={onOpenArtifact}
          onRetry={onRetryMessage}
          isStreaming={isStreaming}
        />
      );
    case 'user':
      return (
        <UserMessage
          message={message}
          onEdit={onEditMessage}
          onRetry={onRetryMessage}
          isStreaming={isStreaming}
        />
      );
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
  onEditMessage,
  onRetryMessage,
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
          onEditMessage={onEditMessage}
          onRetryMessage={onRetryMessage}
          isStreaming={isStreaming}
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
