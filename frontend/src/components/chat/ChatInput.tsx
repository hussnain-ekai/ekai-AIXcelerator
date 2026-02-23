'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import AttachFileRounded from '@mui/icons-material/AttachFileRounded';
import ArrowUpwardRounded from '@mui/icons-material/ArrowUpwardRounded';
import CloseOutlined from '@mui/icons-material/CloseOutlined';
import InsertDriveFileOutlined from '@mui/icons-material/InsertDriveFileOutlined';
import StopRounded from '@mui/icons-material/StopRounded';

// Accept all files — users upload DBML, SQL, SVG, data catalogs, and other uncommon
// formats that browsers don't recognize in restrictive accept lists.
// Size validation (MAX_FILE_SIZE) still applies.
const ACCEPTED_TYPES = '*/*';
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 MB
const MAX_FILES = 5;

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface ChatInputProps {
  onSend: (message: string, files?: File[]) => void;
  onStop?: () => void;
  disabled?: boolean;
  isStreaming?: boolean;
  pendingQueueCount?: number;
}

export function ChatInput({
  onSend,
  onStop,
  disabled = false,
  isStreaming = false,
  pendingQueueCount = 0,
}: ChatInputProps): React.ReactNode {
  const [value, setValue] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [focused, setFocused] = useState(false);
  const [fileWarning, setFileWarning] = useState<string>('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const addFiles = useCallback((files: FileList | File[]) => {
    const incoming = Array.from(files);
    const oversized = incoming.filter((f) => f.size > MAX_FILE_SIZE);
    const valid = incoming.filter((f) => f.size <= MAX_FILE_SIZE);

    setAttachedFiles((prev) => {
      const remainingSlots = Math.max(0, MAX_FILES - prev.length);
      const accepted = valid.slice(0, remainingSlots);
      const droppedForLimit = Math.max(0, valid.length - accepted.length);

      if (oversized.length > 0 || droppedForLimit > 0) {
        const reasons: string[] = [];
        if (oversized.length > 0) {
          reasons.push(`${oversized.length} file${oversized.length > 1 ? 's' : ''} too large`);
        }
        if (droppedForLimit > 0) {
          reasons.push(`${droppedForLimit} ignored (max ${MAX_FILES} files)`);
        }
        setFileWarning(reasons.join(' • '));
      } else {
        setFileWarning('');
      }

      return [...prev, ...accepted];
    });
  }, []);

  const removeFile = useCallback((index: number) => {
    setAttachedFiles((prev) => {
      const next = prev.filter((_, i) => i !== index);
      if (next.length === 0) setFileWarning('');
      return next;
    });
  }, []);

  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = '0px';
    const next = Math.min(el.scrollHeight, 220);
    el.style.height = `${Math.max(next, 44)}px`;
    el.style.overflowY = el.scrollHeight > 220 ? 'auto' : 'hidden';
  }, []);

  useEffect(() => {
    resizeTextarea();
  }, [value, resizeTextarea, attachedFiles.length]);

  const imagePreviews = useMemo(
    () =>
      attachedFiles.map((file) =>
        file.type.startsWith('image/') ? URL.createObjectURL(file) : null,
      ),
    [attachedFiles],
  );

  useEffect(
    () => () => {
      imagePreviews.forEach((url) => {
        if (url) URL.revokeObjectURL(url);
      });
    },
    [imagePreviews],
  );

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    const hasMessage = trimmed.length > 0;
    const hasAttachments = attachedFiles.length > 0;
    if ((!hasMessage && !hasAttachments) || disabled) return;
    const outbound = hasMessage ? trimmed : 'Please analyze the attached files.';
    onSend(outbound, attachedFiles.length > 0 ? attachedFiles : undefined);
    setValue('');
    setAttachedFiles([]);
    setFileWarning('');
  }, [value, disabled, onSend, attachedFiles]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) addFiles(e.target.files);
      e.target.value = '';
    },
    [addFiles],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files);
    },
    [addFiles],
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.files;
      if (items && items.length > 0) addFiles(items);
    },
    [addFiles],
  );

  const handleContainerClick = useCallback(() => {
    textareaRef.current?.focus();
  }, []);

  const canSend =
    (value.trim().length > 0 || attachedFiles.length > 0) && !disabled;
  const hasFiles = attachedFiles.length > 0;

  return (
    <Box
      sx={{
        px: { xs: 1.5, sm: 3 },
        py: { xs: 1.25, sm: 1.5 },
        bgcolor: 'background.default',
      }}
    >
      {/* Hidden file input — outside the container to prevent accidental triggers */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPTED_TYPES}
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />

      {/* Unified input container — ChatGPT-like shell */}
      <Box
        onClick={handleContainerClick}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        sx={{
          width: '100%',
          maxWidth: 980,
          mx: 'auto',
          display: 'flex',
          flexDirection: 'column',
          border: 1,
          borderColor: (theme) => {
            if (dragOver) return theme.palette.primary.main;
            if (focused) return theme.palette.text.primary;
            return theme.palette.divider;
          },
          borderRadius: 4,
          bgcolor: 'background.paper',
          transition: 'border-color 180ms ease, box-shadow 180ms ease',
          boxShadow: (theme) =>
            focused || dragOver
              ? `0 0 0 3px ${theme.palette.action.hover}`
              : '0 1px 2px rgba(16,24,40,0.04)',
          cursor: 'text',
          overflow: 'hidden',
        }}
      >
        {/* File previews/chips */}
        {hasFiles && (
          <Box
            sx={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 0.75,
              px: 1.5,
              pt: 1.25,
            }}
          >
            {attachedFiles.map((file, idx) => {
              const preview = imagePreviews[idx];
              return file.type.startsWith('image/') ? (
                <Box
                  key={idx}
                  sx={{
                    position: 'relative',
                    width: 68,
                    height: 68,
                    borderRadius: 2,
                    overflow: 'hidden',
                    border: 1,
                    borderColor: 'divider',
                    flexShrink: 0,
                    bgcolor: 'action.hover',
                  }}
                >
                  <Box
                    component="img"
                    src={preview ?? undefined}
                    alt={file.name}
                    sx={{
                      width: '100%',
                      height: '100%',
                      objectFit: 'cover',
                    }}
                  />
                  <IconButton
                    onClick={(e) => {
                      e.stopPropagation();
                      removeFile(idx);
                    }}
                    size="small"
                    sx={{
                      position: 'absolute',
                      top: 3,
                      right: 3,
                      p: 0.25,
                      bgcolor: 'rgba(0,0,0,0.66)',
                      color: '#fff',
                      '&:hover': { bgcolor: 'rgba(0,0,0,0.85)' },
                    }}
                  >
                    <CloseOutlined sx={{ fontSize: 12 }} />
                  </IconButton>
                </Box>
              ) : (
                <Box
                  key={idx}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 0.75,
                    px: 1.1,
                    py: 0.65,
                    borderRadius: 2,
                    border: 1,
                    borderColor: 'divider',
                    bgcolor: 'action.hover',
                    maxWidth: 260,
                  }}
                >
                  <InsertDriveFileOutlined
                    sx={{ fontSize: 18, color: 'text.secondary', flexShrink: 0 }}
                  />
                  <Box sx={{ minWidth: 0, flex: 1 }}>
                    <Typography
                      variant="caption"
                      noWrap
                      sx={{
                        display: 'block',
                        fontWeight: 600,
                        lineHeight: 1.3,
                        fontSize: '0.7rem',
                      }}
                    >
                      {file.name}
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{ color: 'text.secondary', fontSize: '0.65rem' }}
                    >
                      {formatFileSize(file.size)}
                    </Typography>
                  </Box>
                    <IconButton
                      onClick={(e) => {
                        e.stopPropagation();
                        removeFile(idx);
                      }}
                      size="small"
                      sx={{ p: 0.25, flexShrink: 0, color: 'text.secondary' }}
                    >
                      <CloseOutlined sx={{ fontSize: 14 }} />
                    </IconButton>
                  </Box>
              );
            })}
          </Box>
        )}

        {/* Textarea */}
        <Box
          component="textarea"
          ref={textareaRef}
          value={value}
          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
            setValue(e.target.value)
          }
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={
            dragOver
              ? 'Drop files here...'
              : isStreaming
                ? 'Type and press Enter to queue your next instruction...'
                : 'Reply to ekaiX...'
          }
          disabled={disabled}
          rows={1}
          sx={{
            width: '100%',
            border: 'none',
            outline: 'none',
            resize: 'none',
            bgcolor: 'transparent',
            color: 'text.primary',
            fontFamily: 'inherit',
            fontSize: { xs: '0.92rem', sm: '0.95rem' },
            lineHeight: 1.5,
            px: 1.5,
            pt: hasFiles ? 0.9 : 1.2,
            pb: 0.7,
            minHeight: 44,
            maxHeight: 220,
            overflow: 'auto',
            '&::placeholder': {
              color: 'text.secondary',
              opacity: 0.82,
            },
            '&:disabled': {
              color: 'text.disabled',
              cursor: 'not-allowed',
            },
          }}
        />

        {/* Bottom action row */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 1.1,
            pb: 1.05,
            pt: 0.2,
          }}
        >
          {/* Left: attachment action + count */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <Tooltip title="Attach files" placement="top">
              <span>
                <IconButton
                  onClick={(e) => {
                    e.stopPropagation();
                    fileInputRef.current?.click();
                  }}
                  disabled={disabled || attachedFiles.length >= MAX_FILES}
                  size="small"
                  sx={{
                    color: 'text.secondary',
                    borderRadius: 1.5,
                    px: 0.8,
                    '&:hover': {
                      bgcolor: 'action.hover',
                      color: 'text.primary',
                    },
                  }}
                >
                  <AttachFileRounded sx={{ fontSize: 18 }} />
                </IconButton>
              </span>
            </Tooltip>
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                fontSize: '0.68rem',
                letterSpacing: 0.1,
              }}
            >
              {attachedFiles.length}/{MAX_FILES}
            </Typography>
          </Box>

          {/* Right: send or stop button */}
          {isStreaming ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              {canSend && (
                <Tooltip title="Queue message" placement="top">
                  <span>
                    <IconButton
                      onClick={(e) => {
                        e.stopPropagation();
                        handleSend();
                      }}
                      disabled={!canSend}
                      size="small"
                      sx={{
                        border: 1,
                        borderColor: 'divider',
                        color: 'text.primary',
                        width: 32,
                        height: 32,
                        borderRadius: '50%',
                        '&:hover': { bgcolor: 'action.hover' },
                      }}
                    >
                      <ArrowUpwardRounded sx={{ fontSize: 18 }} />
                    </IconButton>
                  </span>
                </Tooltip>
              )}
              <Tooltip title="Stop generating" placement="top">
                <IconButton
                  onClick={(e) => {
                    e.stopPropagation();
                    onStop?.();
                  }}
                  size="small"
                  sx={{
                    bgcolor: 'text.primary',
                    color: 'background.default',
                    width: 32,
                    height: 32,
                    borderRadius: '50%',
                    '&:hover': { bgcolor: 'text.secondary' },
                  }}
                >
                  <StopRounded sx={{ fontSize: 18 }} />
                </IconButton>
              </Tooltip>
            </Box>
          ) : (
            <Tooltip title="Send message" placement="top">
              <span>
                <IconButton
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSend();
                  }}
                  disabled={!canSend}
                  size="small"
                  sx={{
                    bgcolor: canSend ? 'text.primary' : 'action.disabledBackground',
                    color: canSend ? 'background.default' : 'text.disabled',
                    width: 32,
                    height: 32,
                    borderRadius: '50%',
                    '&:hover': { bgcolor: canSend ? 'text.secondary' : undefined },
                    '&.Mui-disabled': {
                      bgcolor: 'action.disabledBackground',
                      color: 'text.disabled',
                    },
                  }}
                >
                  <ArrowUpwardRounded sx={{ fontSize: 18 }} />
                </IconButton>
              </span>
            </Tooltip>
          )}
        </Box>
      </Box>

      {/* Helper text / warnings */}
      <Typography
        variant="caption"
        sx={{
          maxWidth: 980,
          mx: 'auto',
          display: 'block',
          textAlign: 'center',
          mt: 0.75,
          color: fileWarning ? 'warning.main' : pendingQueueCount > 0 ? 'text.secondary' : 'text.disabled',
          fontSize: '0.66rem',
          minHeight: 14,
        }}
      >
        {fileWarning
          || (
            pendingQueueCount > 0
              ? `${pendingQueueCount} message${pendingQueueCount > 1 ? 's' : ''} queued. ekaiX will send ${pendingQueueCount > 1 ? 'them' : 'it'} automatically after the current run.`
              : 'ekaiX can make mistakes. Verify important information.'
          )}
      </Typography>
    </Box>
  );
}
