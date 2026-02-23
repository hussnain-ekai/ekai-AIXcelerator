'use client';

import { useRef } from 'react';
import {
  Box,
  Button,
  Chip,
  Drawer,
  IconButton,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined';
import UploadFileOutlinedIcon from '@mui/icons-material/UploadFileOutlined';
import type { UploadedDocument } from '@/hooks/useDocuments';

const DRAWER_WIDTH = 420;

interface DocumentsPanelProps {
  open: boolean;
  onClose: () => void;
  documents: UploadedDocument[];
  onUploadFiles: (files: File[]) => void;
  isUploading?: boolean;
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

export function DocumentsPanel({
  open,
  onClose,
  documents,
  onUploadFiles,
  isUploading = false,
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
              {documents.map((doc) => (
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
                    <Box sx={{ mt: 0.75, display: 'flex', gap: 0.75, alignItems: 'center', flexWrap: 'wrap' }}>
                      <Chip
                        size="small"
                        label={doc.extraction_status || 'uploaded'}
                        color={statusColor(doc.extraction_status || 'uploaded')}
                        variant="outlined"
                      />
                      {doc.extraction_error && (
                        <Typography variant="caption" color="error.main" noWrap>
                          {doc.extraction_error}
                        </Typography>
                      )}
                    </Box>
                  </Box>
                </Box>
              ))}
            </Box>
          )}
        </Box>
      </Box>
    </Drawer>
  );
}

export type { DocumentsPanelProps };
