'use client';

import { useRef } from 'react';
import {
  Box,
  Button,
  Chip,
  Drawer,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined';
import UploadFileOutlinedIcon from '@mui/icons-material/UploadFileOutlined';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AutorenewOutlinedIcon from '@mui/icons-material/AutorenewOutlined';
import type { ContextSelectionState, UploadedDocument } from '@/hooks/useDocuments';

const DRAWER_WIDTH = 460;

interface DocumentContextLookup {
  evidenceId: string;
  state: ContextSelectionState;
}

interface DocumentRegistryLookup {
  versionId: number;
  parseQualityScore: number | null;
  extractionMethod: string | null;
  updatedAt: string;
  diagnostics: Record<string, unknown>;
}

interface DocumentsPanelProps {
  open: boolean;
  onClose: () => void;
  documents: UploadedDocument[];
  onUploadFiles: (files: File[]) => void;
  isUploading?: boolean;
  uploadNotice?: string | null;
  uploadNoticeSeverity?: 'info' | 'error';
  currentStep?: string;
  contextByDocumentId?: Record<string, DocumentContextLookup>;
  registryByDocumentId?: Record<string, DocumentRegistryLookup>;
  onSetDocumentState?: (documentId: string, state: ContextSelectionState) => void;
  onDeleteDocument?: (document: UploadedDocument) => void;
  onReextractDocument?: (document: UploadedDocument) => void;
  isUpdatingContext?: boolean;
  isDeleting?: boolean;
  isReextracting?: boolean;
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatFileSize(size: number | null): string {
  if (!size || size <= 0) return 'Unknown size';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function statusColor(status: string): 'default' | 'success' | 'warning' | 'error' | 'info' {
  const normalized = status.toLowerCase();
  if (normalized === 'completed') return 'success';
  if (normalized === 'failed' || normalized === 'error') return 'error';
  if (normalized === 'pending' || normalized === 'queued') return 'warning';
  if (normalized === 'processing' || normalized === 'running') return 'info';
  return 'default';
}

function contextStateColor(
  state: ContextSelectionState,
): 'success' | 'warning' | 'default' | 'error' {
  if (state === 'active') return 'success';
  if (state === 'candidate') return 'warning';
  if (state === 'excluded') return 'error';
  return 'default';
}

function humanizeContextState(state: ContextSelectionState): string {
  if (state === 'active') return 'Active';
  if (state === 'candidate') return 'Candidate';
  if (state === 'reference') return 'Reference';
  return 'Excluded';
}

export function DocumentsPanel({
  open,
  onClose,
  documents,
  onUploadFiles,
  isUploading = false,
  uploadNotice,
  uploadNoticeSeverity = 'info',
  currentStep,
  contextByDocumentId = {},
  registryByDocumentId = {},
  onSetDocumentState,
  onDeleteDocument,
  onReextractDocument,
  isUpdatingContext = false,
  isDeleting = false,
  isReextracting = false,
}: DocumentsPanelProps): React.ReactNode {
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      variant="temporary"
      slotProps={{
        paper: {
          sx: {
            width: DRAWER_WIDTH,
            bgcolor: 'background.default',
            borderLeft: 1,
            borderColor: 'divider',
          },
        },
      }}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 2.5,
            py: 2,
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          <Box>
            <Typography variant="h6" fontWeight={700}>
              Documents
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {documents.length} uploaded
              {currentStep ? ` • Context step: ${currentStep}` : ''}
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              style={{ display: 'none' }}
              onChange={(event) => {
                const files = event.target.files ? Array.from(event.target.files) : [];
                if (files.length > 0) onUploadFiles(files);
                event.target.value = '';
              }}
            />
            <Button
              variant="outlined"
              size="small"
              startIcon={<UploadFileOutlinedIcon />}
              disabled={isUploading}
              onClick={() => fileInputRef.current?.click()}
            >
              Upload
            </Button>
            <IconButton onClick={onClose} size="small">
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>
        </Box>

        <Box sx={{ flex: 1, overflow: 'auto', px: 2.5, py: 2 }}>
          {uploadNotice && (
            <Typography
              variant="caption"
              sx={{
                display: 'block',
                mb: 1.5,
                color: uploadNoticeSeverity === 'error' ? 'error.main' : 'text.secondary',
              }}
            >
              {uploadNotice}
            </Typography>
          )}
          {documents.length === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: 'center', mt: 4 }}
            >
              No documents uploaded yet.
            </Typography>
          ) : (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
              {documents.map((doc) => {
                const contextState = contextByDocumentId[doc.id]?.state;
                const registry = registryByDocumentId[doc.id];
                const extractionStatus = (doc.extraction_status || '').toLowerCase();
                const canRetryExtraction =
                  extractionStatus === 'pending' || extractionStatus === 'failed';

                return (
                  <Box
                    key={doc.id}
                    sx={{
                      display: 'flex',
                      gap: 1.25,
                      p: 1.5,
                      borderRadius: 1,
                      bgcolor: 'background.paper',
                      border: 1,
                      borderColor: 'divider',
                    }}
                  >
                    <DescriptionOutlinedIcon
                      sx={{ fontSize: 18, color: 'text.secondary', mt: 0.25 }}
                    />
                    <Box sx={{ minWidth: 0, flex: 1 }}>
                      <Typography variant="body2" fontWeight={700} noWrap>
                        {doc.filename}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: 'block', mt: 0.25 }}
                      >
                        {formatFileSize(doc.file_size_bytes)} • {formatTimestamp(doc.created_at)}
                      </Typography>

                      {doc.summary && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: 'block', mt: 0.5, lineHeight: 1.4 }}
                        >
                          {doc.summary}
                        </Typography>
                      )}

                      <Box
                        sx={{
                          mt: 0.75,
                          display: 'flex',
                          gap: 0.75,
                          alignItems: 'center',
                          flexWrap: 'wrap',
                        }}
                      >
                        <Chip
                          size="small"
                          label={doc.extraction_status || 'uploaded'}
                          color={statusColor(doc.extraction_status || 'uploaded')}
                          variant="outlined"
                        />

                        {doc.doc_kind && (
                          <Chip
                            size="small"
                            label={doc.doc_kind}
                            variant="outlined"
                            color="default"
                          />
                        )}

                        {contextState && (
                          <Chip
                            size="small"
                            label={`Step: ${humanizeContextState(contextState)}`}
                            color={contextStateColor(contextState)}
                            variant="outlined"
                          />
                        )}

                        {registry && (
                          <Chip
                            size="small"
                            label={`v${registry.versionId}`}
                            variant="outlined"
                            color="default"
                          />
                        )}

                        {registry && registry.parseQualityScore !== null && (
                          <Chip
                            size="small"
                            label={`Quality ${Math.round(registry.parseQualityScore)}%`}
                            variant="outlined"
                            color={registry.parseQualityScore >= 80 ? 'success' : registry.parseQualityScore >= 60 ? 'warning' : 'error'}
                          />
                        )}

                        {registry?.extractionMethod && (
                          <Chip
                            size="small"
                            label={registry.extractionMethod}
                            variant="outlined"
                            color="info"
                          />
                        )}

                        {doc.extraction_error && (
                          <Typography variant="caption" color="error.main" noWrap>
                            {doc.extraction_error}
                          </Typography>
                        )}
                      </Box>

                      {onSetDocumentState && contextState && (
                        <Box sx={{ mt: 1, display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                          <Button
                            size="small"
                            variant={contextState === 'active' ? 'contained' : 'outlined'}
                            disabled={isUpdatingContext}
                            onClick={() => onSetDocumentState(doc.id, 'active')}
                          >
                            Use in Step
                          </Button>
                          <Button
                            size="small"
                            variant={contextState === 'reference' ? 'contained' : 'outlined'}
                            disabled={isUpdatingContext}
                            onClick={() => onSetDocumentState(doc.id, 'reference')}
                          >
                            Reference
                          </Button>
                          <Button
                            size="small"
                            color="warning"
                            variant={contextState === 'excluded' ? 'contained' : 'outlined'}
                            disabled={isUpdatingContext}
                            onClick={() => onSetDocumentState(doc.id, 'excluded')}
                          >
                            Exclude
                          </Button>
                        </Box>
                      )}
                    </Box>

                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
                      {onReextractDocument && canRetryExtraction && (
                        <Tooltip title="Retry extraction">
                          <span>
                            <IconButton
                              size="small"
                              color="primary"
                              disabled={isReextracting}
                              onClick={() => onReextractDocument(doc)}
                              sx={{ alignSelf: 'flex-start' }}
                            >
                              <AutorenewOutlinedIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                      )}
                      {onDeleteDocument && (
                        <Tooltip title="Delete document">
                          <span>
                            <IconButton
                              size="small"
                              color="error"
                              disabled={isDeleting}
                              onClick={() => onDeleteDocument(doc)}
                              sx={{ alignSelf: 'flex-start' }}
                            >
                              <DeleteOutlineIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                      )}
                    </Box>
                  </Box>
                );
              })}
            </Box>
          )}
        </Box>
      </Box>
    </Drawer>
  );
}

export type { DocumentsPanelProps, DocumentContextLookup };
