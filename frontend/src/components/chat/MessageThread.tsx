'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  Collapse,
  CircularProgress,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
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
import ExpandLessOutlined from '@mui/icons-material/ExpandLessOutlined';
import ExpandMoreOutlined from '@mui/icons-material/ExpandMoreOutlined';
import InsertDriveFileOutlined from '@mui/icons-material/InsertDriveFileOutlined';
import RefreshOutlined from '@mui/icons-material/RefreshOutlined';
import type {
  ChatMessage,
  ArtifactType,
  PipelineProgress,
  ReasoningUpdate,
} from '@/stores/chatStore';
import type { AnswerContract } from '@/lib/answerContract';
import { api } from '@/lib/api';
import { useChatStore } from '@/stores/chatStoreProvider';
import { ArtifactCard } from './ArtifactCard';

interface MessageThreadProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  dataProductId?: string;
  onOpenArtifact?: (type: ArtifactType) => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onRetryMessage?: (messageId: string) => void;
}

const GOLD = '#D4A843';
const TRUST_CONTRACT_UI_ENABLED =
  process.env.NEXT_PUBLIC_TRUST_UX_ENABLED !== 'false';

const TRUST_LABELS: Record<string, string> = {
  answer_ready: 'Answer ready',
  answer_with_warnings: 'Answer needs review',
  abstained_missing_evidence: 'Need more evidence',
  abstained_conflicting_evidence: 'Conflicting evidence',
  blocked_access: 'Access blocked',
  failed_recoverable: 'Action required',
  failed_admin: 'Admin action required',
};

const SOURCE_LABELS: Record<string, string> = {
  structured: 'Structured source',
  document: 'Document source',
  hybrid: 'Hybrid source',
  unknown: 'Source unknown',
};

const EXACTNESS_LABELS: Record<string, string> = {
  validated_exact: 'Validated exact value',
  estimated: 'Estimated answer',
  insufficient_evidence: 'Insufficient evidence',
  not_applicable: 'Context answer',
};

const CONFIDENCE_LABELS: Record<string, string> = {
  high: 'High confidence',
  medium: 'Medium confidence',
  abstain: 'Abstained',
};

function formatRecencyLabel(contract: AnswerContract, observedAt?: string): string {
  const metadata = contract.metadata ?? {};
  const candidate =
    (typeof metadata.evidence_created_at === 'string' && metadata.evidence_created_at) ||
    (typeof metadata.observed_at === 'string' && metadata.observed_at) ||
    observedAt ||
    '';
  const parsed = candidate ? Date.parse(candidate) : Number.NaN;
  if (!Number.isFinite(parsed)) return 'Recency unknown';

  const ageMinutes = Math.max(0, Math.floor((Date.now() - parsed) / 60_000));
  if (ageMinutes < 60) return 'Updated <1h ago';
  const ageHours = Math.floor(ageMinutes / 60);
  if (ageHours < 24) return `Updated ${ageHours}h ago`;
  const ageDays = Math.floor(ageHours / 24);
  return `Updated ${ageDays}d ago`;
}

/* ------------------------------------------------------------------ */
/*  Message content filters                                            */
/* ------------------------------------------------------------------ */

/** Messages that should be completely hidden from the user. */
function isHiddenMessage(message: ChatMessage): boolean {
  const text = typeof message.content === 'string' ? message.content : '';
  // Internal discovery context injected for the LLM — never for user display
  if (text.includes('[INTERNAL CONTEXT')) return true;
  if (text.includes('[SUPERVISOR CONTEXT CONTRACT')) return true;
  // Orchestrator internal monologue that leaked through (mentions tool names, task(), etc.)
  if (message.role === 'assistant') {
    if (text.includes('`task`') || text.includes('task()')) return true;
    if (text.includes('subagent') || text.includes('sub-agent')) return true;
    if (text.includes('tool usage') || text.includes('tool_call')) return true;
    // Repeated "Wait" pattern from Gemini auto-chain failures
    if ((text.match(/\*\*Wait\*\*/g) ?? []).length >= 2) return true;
    if ((text.match(/Wait,/g) ?? []).length >= 2) return true;
    // Tool cancellation messages (Deep Agents internal noise)
    if (text.includes('was cancelled') && text.includes('tool call')) return true;
    if (text.includes('Tool call task with id')) return true;
  }
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

  // Strip any leaked supervisor/internal contract payloads.
  if (text.includes('[SUPERVISOR CONTEXT CONTRACT')) {
    const userMarker = '[USER MESSAGE]';
    const markerIdx = text.indexOf(userMarker);
    if (markerIdx !== -1) {
      text = text.slice(markerIdx + userMarker.length).trim();
    } else {
      text = '';
    }
    condensed = true;
  }

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

  // Always strip markdown formatting — agents should output plain text only,
  // but LLMs sometimes emit **bold**, *italic*, and # headers regardless.
  text = text.replace(/\*\*(.*?)\*\*/g, '$1');   // **bold** → bold
  text = text.replace(/\*(.*?)\*/g, '$1');        // *italic* → italic
  text = text.replace(/^#{1,6}\s+/gm, '');        // # headers → plain text

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
  // Transformation agent tools
  profile_source_table: 'Profiling source table',
  generate_dynamic_table_ddl: 'Generating transformation',
  execute_transformation_ddl: 'Applying transformation',
  validate_transformation: 'Validating transformation',
  register_transformed_layer: 'Registering data layer',
};

const TOOL_ARTIFACT_TYPES: Record<string, ArtifactType> = {
  save_brd: 'brd',
  save_semantic_view: 'yaml',
  create_semantic_view: 'yaml',
  save_data_description: 'data_description',
  build_erd_from_description: 'erd',
  save_quality_report: 'data_quality',
  register_gold_layer: 'lineage',
};

const PHASE_STATUS_LABELS: Record<string, string> = {
  idle: 'Waiting for your input',
  discovery: 'Profiling your source data',
  prepare: 'Preparing context from source data',
  requirements: 'Capturing business requirements',
  modeling: 'Designing transformation logic',
  generation: 'Generating semantic model',
  validation: 'Validating model outputs',
  publishing: 'Publishing the data product',
  explorer: 'Ready in explorer mode',
};

function getPhaseStatusLabel(currentPhase: string, recentToolNames: string[]): string {
  if (currentPhase === 'requirements') {
    if (recentToolNames.includes('save_brd')) {
      return 'Drafting business requirements document (BRD)';
    }
    return 'Capturing business requirements';
  }
  return PHASE_STATUS_LABELS[currentPhase] ?? 'Processing your request';
}

function getStallHint(currentPhase: string): string {
  if (currentPhase === 'discovery' || currentPhase === 'prepare') {
    return 'Profiling can take longer on large datasets. ekaiX is still working on this step.';
  }
  if (currentPhase === 'requirements') {
    return 'Requirement capture and BRD drafting can have short quiet periods while content is prepared.';
  }
  return 'This step is still running. ekaiX will post the next update automatically.';
}

function getToolDisplayName(toolName: string): string {
  return TOOL_DISPLAY_NAMES[toolName] ?? 'Working';
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
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
  dataProductId,
}: {
  message: ChatMessage;
  onOpenArtifact?: (type: ArtifactType) => void;
  onRetry?: (messageId: string) => void;
  isStreaming: boolean;
  dataProductId?: string;
}): React.ReactNode {
  const [copied, setCopied] = useState(false);

  // Collect artifact cards to show: explicit refs take priority, then tool-call inference.
  const artifacts = useChatStore((state) => state.artifacts);
  let inlineArtifacts: { type: ArtifactType; title: string }[] = [];

  if (message.artifactRefs && message.artifactRefs.length > 0) {
    // Explicit artifact references on this message (e.g. from pipeline completion)
    inlineArtifacts = message.artifactRefs.map((refType) => {
      const match = artifacts.find((a) => a.type === refType);
      return { type: refType, title: match?.title ?? refType.toUpperCase() };
    });
  } else {
    // Infer artifact cards from tool calls to avoid timestamp-based mis-association.
    const inferredTypes = (message.toolCalls ?? [])
      .map((call) => TOOL_ARTIFACT_TYPES[call.name])
      .filter((type): type is ArtifactType => Boolean(type))
      .filter((type, idx, arr) => arr.indexOf(type) === idx);

    inlineArtifacts = inferredTypes.map((type) => {
      const match = artifacts.find((a) => a.type === type);
      return { type, title: match?.title ?? type.toUpperCase() };
    });
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
      {/* Trust contract evidence card removed — backend-only feature, not for end users */}
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

function AnswerTrustCard({
  contract,
  dataProductId,
  showSender = true,
  observedAt,
}: {
  contract: AnswerContract | null;
  dataProductId?: string;
  showSender?: boolean;
  observedAt?: string;
}): React.ReactNode {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [evidenceDialogOpen, setEvidenceDialogOpen] = useState(false);
  const [evidenceDialogTitle, setEvidenceDialogTitle] = useState('');
  const [evidenceDialogBody, setEvidenceDialogBody] = useState<string>('');
  const [loadingReference, setLoadingReference] = useState<string | null>(null);

  const handleOpenEvidence = useCallback(
    async (citationType: string, referenceId: string) => {
      if (!dataProductId) return;
      const key = `${citationType}:${referenceId}`;
      setLoadingReference(key);
      try {
        const qs = new URLSearchParams({
          citation_type: citationType,
          reference_id: referenceId,
        });
        const payload = await api.get<Record<string, unknown>>(
          `/documents/semantic/${dataProductId}/evidence/link?${qs.toString()}`,
        );
        setEvidenceDialogTitle(`${citationType} • ${referenceId}`);
        setEvidenceDialogBody(JSON.stringify(payload, null, 2));
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to load evidence detail';
        setEvidenceDialogTitle(`${citationType} • ${referenceId}`);
        setEvidenceDialogBody(message);
      } finally {
        setLoadingReference(null);
        setEvidenceDialogOpen(true);
      }
    },
    [dataProductId],
  );

  if (!contract) return null;

  const isNegativeState =
    contract.trust_state.startsWith('abstained') ||
    contract.trust_state.startsWith('failed') ||
    contract.trust_state === 'blocked_access';
  const citationCount = contract.citations.length;
  const hasEvidence = citationCount > 0;
  const forceNeedsEvidence = !hasEvidence && !isNegativeState;
  const trustColor = isNegativeState || forceNeedsEvidence ? 'warning.main' : 'divider';
  const trustLabel = forceNeedsEvidence
    ? 'Need more evidence'
    : (TRUST_LABELS[contract.trust_state] ?? null);
  const sourceLabel = SOURCE_LABELS[contract.source_mode] ?? SOURCE_LABELS.unknown;
  const exactnessLabel = EXACTNESS_LABELS[contract.exactness_state] ?? EXACTNESS_LABELS.not_applicable;
  const confidenceLabel = CONFIDENCE_LABELS[contract.confidence_decision] ?? CONFIDENCE_LABELS.medium;
  const recencyLabel = formatRecencyLabel(contract, observedAt);
  const evidenceLine = hasEvidence
    ? `${citationCount} source${citationCount === 1 ? '' : 's'} linked`
    : 'No linked sources for this answer yet.';

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', width: '100%' }}>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, width: '100%' }}>
        {showSender && (
          <Typography variant="caption" sx={{ fontWeight: 700, color: GOLD }}>
            ekaiX
          </Typography>
        )}
        <Paper
          elevation={0}
          sx={{
            p: 1.5,
            bgcolor: 'background.paper',
            borderRadius: 2,
            border: 1,
            borderColor: trustColor,
          }}
        >
          {trustLabel && (
            <Chip
              size="small"
              label={trustLabel}
              sx={{ borderColor: trustColor, borderWidth: 1, borderStyle: 'solid', mb: 1 }}
            />
          )}

          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', mb: 1 }}>
            <Chip size="small" variant="outlined" label={sourceLabel} />
            <Chip size="small" variant="outlined" label={exactnessLabel} />
            <Chip size="small" variant="outlined" label={confidenceLabel} />
            <Chip size="small" variant="outlined" label={recencyLabel} />
          </Box>

          {contract.evidence_summary && (
            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mb: 0.5 }}>
              {contract.evidence_summary}
            </Typography>
          )}

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              {evidenceLine}
            </Typography>
            {(citationCount > 0 || contract.recovery_actions.length > 0 || contract.conflict_notes.length > 0) && (
              <Button
                size="small"
                variant="text"
                onClick={() => setDetailsOpen((prev) => !prev)}
                sx={{ minWidth: 0, px: 0.5, textTransform: 'none', fontSize: '0.72rem' }}
              >
                {detailsOpen ? 'Hide details' : 'View details'}
              </Button>
            )}
          </Box>

          <Collapse in={detailsOpen}>
            {contract.citations.length > 0 && (
              <Box sx={{ mt: 1 }}>
                {contract.citations.slice(0, 8).map((citation) => {
                  const key = `${citation.citation_type}:${citation.reference_id}`;
                  return (
                    <Box
                      key={key}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 1,
                        mb: 0.5,
                      }}
                    >
                      <Typography variant="caption" sx={{ color: 'text.secondary', pr: 1 }}>
                        • {citation.label ?? citation.reference_id}
                      </Typography>
                      <Button
                        size="small"
                        variant="outlined"
                        disabled={!dataProductId || loadingReference === key}
                        onClick={() =>
                          handleOpenEvidence(citation.citation_type, citation.reference_id)
                        }
                        sx={{ minWidth: 0, px: 1, textTransform: 'none', fontSize: '0.68rem' }}
                      >
                        {loadingReference === key ? 'Loading...' : 'Open source'}
                      </Button>
                    </Box>
                  );
                })}
              </Box>
            )}

            {contract.conflict_notes.length > 0 && (
              <Alert severity="warning" sx={{ mt: 1, py: 0.25 }}>
                <Typography variant="caption">
                  {contract.conflict_notes.join(' | ')}
                </Typography>
              </Alert>
            )}

            {contract.recovery_actions.length > 0 && (
              <Box sx={{ mt: 1 }}>
                <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mb: 0.5 }}>
                  Recovery actions:
                </Typography>
                {contract.recovery_actions.slice(0, 4).map((action, idx) => (
                  <Typography key={`${action.action}-${idx}`} variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
                    {idx + 1}. {action.description}
                  </Typography>
                ))}
              </Box>
            )}
          </Collapse>
        </Paper>
      </Box>
      <Dialog
        open={evidenceDialogOpen}
        onClose={() => setEvidenceDialogOpen(false)}
        fullWidth
        maxWidth="md"
      >
        <DialogTitle>{evidenceDialogTitle || 'Evidence detail'}</DialogTitle>
        <DialogContent>
          <Typography
            component="pre"
            sx={{
              m: 0,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: '0.78rem',
              lineHeight: 1.5,
            }}
          >
            {evidenceDialogBody}
          </Typography>
        </DialogContent>
      </Dialog>
    </Box>
  );
}

function LiveAgentStatus({
  messages,
  isStreaming,
  pipelineProgress,
  currentPhase,
  reasoningUpdate,
  reasoningLog,
}: {
  messages: ChatMessage[];
  isStreaming: boolean;
  pipelineProgress: PipelineProgress | null;
  currentPhase: string;
  reasoningUpdate: ReasoningUpdate | null;
  reasoningLog: ReasoningUpdate[];
}): React.ReactNode {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [lastUpdateAt, setLastUpdateAt] = useState<number>(Date.now());
  const [now, setNow] = useState<number>(Date.now());

  const lastMessage = messages[messages.length - 1];
  const lastContentLength =
    typeof lastMessage?.content === 'string'
      ? lastMessage.content.length
      : JSON.stringify(lastMessage?.content ?? '').length;
  const activityFingerprint = [
    messages.length,
    lastMessage?.id ?? 'none',
    lastContentLength,
    lastMessage?.toolCalls?.length ?? 0,
    pipelineProgress?.step ?? 'none',
    pipelineProgress?.detail ?? '',
    pipelineProgress?.status ?? 'none',
    reasoningUpdate?.message ?? '',
    reasoningUpdate?.timestamp ?? '',
    reasoningLog.length,
    reasoningLog[reasoningLog.length - 1]?.timestamp ?? '',
  ].join('|');

  useEffect(() => {
    if (isStreaming) {
      setStartedAt((prev) => prev ?? Date.now());
      return;
    }
    setStartedAt(null);
    setDetailsOpen(false);
  }, [isStreaming]);

  useEffect(() => {
    if (isStreaming || pipelineProgress) {
      setLastUpdateAt(Date.now());
    }
  }, [activityFingerprint, isStreaming, pipelineProgress]);

  useEffect(() => {
    if (!isStreaming) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isStreaming]);

  useEffect(() => {
    if (isStreaming && reasoningLog.length > 0) {
      setDetailsOpen(true);
    }
  }, [isStreaming, reasoningLog.length]);

  const elapsedText = startedAt ? formatElapsed(now - startedAt) : '0s';
  const staleSeconds = Math.max(0, Math.floor((now - lastUpdateAt) / 1000));
  const looksStalled = isStreaming && staleSeconds >= 120;
  const isPipelineComplete =
    pipelineProgress?.step === 'artifacts' && pipelineProgress?.status === 'completed';

  const recentToolNames = messages
    .slice()
    .reverse()
    .flatMap((msg) => (msg.toolCalls ?? []).map((call) => call.name))
    .filter((name, idx, arr) => arr.indexOf(name) === idx)
    .slice(0, 8);

  const recentToolActions = messages
    .slice()
    .reverse()
    .flatMap((msg) => (msg.toolCalls ?? []).map((call) => getToolDisplayName(call.name)))
    .filter((name, idx, arr) => arr.indexOf(name) === idx)
    .slice(0, 5);

  const phaseLabel = getPhaseStatusLabel(currentPhase, recentToolNames);
  const latestReasoning = reasoningLog[reasoningLog.length - 1] ?? reasoningUpdate;
  const assistantReasoning = latestReasoning?.message?.trim() ?? '';
  const reasoningLabel = latestReasoning?.source === 'llm' ? 'Agent thinking' : 'Agent update';
  const recentReasoning = reasoningLog.slice(-3);
  const systemActivity = pipelineProgress
    ? pipelineProgress.label
    : recentToolActions.length > 0
      ? recentToolActions.join(' -> ')
      : 'Coordinating the next step...';
  const summaryText = isPipelineComplete
    ? 'Analysis complete'
    : assistantReasoning || (
      pipelineProgress
      ? pipelineProgress.label
      : phaseLabel
    );

  if (!isStreaming && !pipelineProgress) {
    return null;
  }

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', maxWidth: '75%' }}>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, width: '100%', maxWidth: 620 }}>
        <Typography variant="caption" sx={{ fontWeight: 700, color: GOLD }}>
          ekaiX
        </Typography>
        <Paper
          elevation={0}
          sx={{
            p: 1.5,
            bgcolor: 'background.paper',
            borderRadius: 2,
            border: 1,
            borderColor: looksStalled ? 'warning.main' : 'divider',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              {isPipelineComplete ? (
                <CheckOutlined sx={{ fontSize: 16, color: 'success.main' }} />
              ) : (
                <CircularProgress size={16} sx={{ color: GOLD }} />
              )}
              <Box>
                <Typography variant="body2" sx={{ lineHeight: 1.4 }}>
                  {summaryText}
                  {!assistantReasoning &&
                    pipelineProgress?.detail &&
                    pipelineProgress.detail !== 'Done'
                    ? ` • ${pipelineProgress.detail}`
                    : ''}
                </Typography>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  {isStreaming ? `Running ${elapsedText}` : 'Completed'}
                  {pipelineProgress
                    ? ` • Step ${pipelineProgress.stepIndex + 1}/${pipelineProgress.totalSteps}`
                    : ''}
                  {looksStalled ? ` • still running (quiet for ${staleSeconds}s)` : ''}
                </Typography>
              </Box>
            </Box>
            <Button
              size="small"
              variant="text"
              onClick={() => setDetailsOpen((prev) => !prev)}
              endIcon={detailsOpen ? <ExpandLessOutlined /> : <ExpandMoreOutlined />}
              sx={{
                minWidth: 0,
                px: 0.75,
                textTransform: 'none',
                color: 'text.secondary',
                fontSize: '0.75rem',
              }}
            >
              {detailsOpen ? 'Hide details' : 'Show details'}
            </Button>
          </Box>

          <Collapse in={detailsOpen}>
            <Box
              sx={{
                mt: 1.25,
                pt: 1,
                borderTop: 1,
                borderColor: 'divider',
                display: 'flex',
                flexDirection: 'column',
                gap: 0.5,
              }}
            >
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Current step: {phaseLabel}
              </Typography>
              {assistantReasoning && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  {reasoningLabel}: {assistantReasoning}
                </Typography>
              )}
              {recentReasoning.length > 0 && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Thinking log: {recentReasoning.map((entry) => entry.message).join(' | ')}
                </Typography>
              )}
              {pipelineProgress && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Progress: {pipelineProgress.label}
                </Typography>
              )}
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Recent activity: {systemActivity}
              </Typography>
              {looksStalled && (
                <Typography variant="caption" sx={{ color: 'warning.main' }}>
                  {getStallHint(currentPhase)}
                </Typography>
              )}
            </Box>
          </Collapse>
        </Paper>
      </Box>
    </Box>
  );
}

function MessageBubble({
  message,
  onOpenArtifact,
  onEditMessage,
  onRetryMessage,
  isStreaming,
  dataProductId,
}: {
  message: ChatMessage;
  onOpenArtifact?: (type: ArtifactType) => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onRetryMessage?: (messageId: string) => void;
  isStreaming: boolean;
  dataProductId?: string;
}): React.ReactNode {
  switch (message.role) {
    case 'assistant':
      return (
        <AgentMessage
          message={message}
          onOpenArtifact={onOpenArtifact}
          onRetry={onRetryMessage}
          isStreaming={isStreaming}
          dataProductId={dataProductId}
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
  dataProductId,
  onOpenArtifact,
  onEditMessage,
  onRetryMessage,
}: MessageThreadProps): React.ReactNode {
  const bottomRef = useRef<HTMLDivElement>(null);
  const pipelineProgress = useChatStore((state) => state.pipelineProgress);
  const currentPhase = useChatStore((state) => state.currentPhase);
  const reasoningUpdate = useChatStore((state) => state.reasoningUpdate);
  const reasoningLog = useChatStore((state) => state.reasoningLog);
  const visibleMessages = messages.filter((m) => !isHiddenMessage(m));

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming, pipelineProgress, reasoningUpdate?.timestamp, reasoningLog.length]);

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
      {visibleMessages.length === 0 && !pipelineProgress && (
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

      {visibleMessages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          onOpenArtifact={onOpenArtifact}
          onEditMessage={onEditMessage}
          onRetryMessage={onRetryMessage}
          isStreaming={isStreaming}
          dataProductId={dataProductId}
        />
      ))}

      <LiveAgentStatus
        messages={visibleMessages}
        isStreaming={isStreaming}
        pipelineProgress={pipelineProgress}
        currentPhase={currentPhase}
        reasoningUpdate={reasoningUpdate}
        reasoningLog={reasoningLog}
      />

      <div ref={bottomRef} />
    </Box>
  );
}
