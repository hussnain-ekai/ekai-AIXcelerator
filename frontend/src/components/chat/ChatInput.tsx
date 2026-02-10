'use client';

import { useCallback, useRef, useState } from 'react';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import AddOutlined from '@mui/icons-material/AddOutlined';
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
}

export function ChatInput({
  onSend,
  onStop,
  disabled = false,
  isStreaming = false,
}: ChatInputProps): React.ReactNode {
  const [value, setValue] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [focused, setFocused] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const addFiles = useCallback((files: FileList | File[]) => {
    const newFiles = Array.from(files).filter((f) => f.size <= MAX_FILE_SIZE);
    setAttachedFiles((prev) => [...prev, ...newFiles].slice(0, MAX_FILES));
  }, []);

  const removeFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (trimmed.length === 0 || disabled) return;
    onSend(trimmed, attachedFiles.length > 0 ? attachedFiles : undefined);
    setValue('');
    setAttachedFiles([]);
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

  const canSend = value.trim().length > 0 && !disabled;
  const hasFiles = attachedFiles.length > 0;

  return (
    <Box sx={{ px: 3, py: 1.5, bgcolor: 'background.default' }}>
      {/* Hidden file input — outside the container to prevent accidental triggers */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />

      {/* Unified input container — Claude AI style */}
      <Box
        onClick={handleContainerClick}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        sx={{
          display: 'flex',
          flexDirection: 'column',
          border: 1,
          borderColor: dragOver
            ? 'primary.main'
            : focused
              ? 'primary.main'
              : 'divider',
          borderRadius: 3,
          bgcolor: 'background.paper',
          transition: 'border-color 200ms',
          cursor: 'text',
          overflow: 'hidden',
        }}
      >
        {/* File previews — inside the container */}
        {hasFiles && (
          <Box
            sx={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 1,
              px: 1.5,
              pt: 1.5,
            }}
          >
            {attachedFiles.map((file, idx) =>
              file.type.startsWith('image/') ? (
                <Box
                  key={idx}
                  sx={{
                    position: 'relative',
                    width: 64,
                    height: 64,
                    borderRadius: 1.5,
                    overflow: 'hidden',
                    border: 1,
                    borderColor: 'divider',
                    flexShrink: 0,
                  }}
                >
                  <Box
                    component="img"
                    src={URL.createObjectURL(file)}
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
                      top: 2,
                      right: 2,
                      p: 0.25,
                      bgcolor: 'rgba(0,0,0,0.6)',
                      color: '#fff',
                      '&:hover': { bgcolor: 'rgba(0,0,0,0.8)' },
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
                    px: 1.25,
                    py: 0.75,
                    borderRadius: 1.5,
                    border: 1,
                    borderColor: 'divider',
                    bgcolor: 'action.hover',
                    maxWidth: 200,
                  }}
                >
                  <InsertDriveFileOutlined
                    sx={{ fontSize: 18, color: 'text.secondary', flexShrink: 0 }}
                  />
                  <Box sx={{ minWidth: 0, flex: 1 }}>
                    <Typography
                      variant="caption"
                      noWrap
                      sx={{ display: 'block', fontWeight: 500, lineHeight: 1.3 }}
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
                    sx={{ p: 0.25, flexShrink: 0 }}
                  >
                    <CloseOutlined sx={{ fontSize: 14, color: 'text.secondary' }} />
                  </IconButton>
                </Box>
              ),
            )}
          </Box>
        )}

        {/* Textarea — borderless, inside the container */}
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
            dragOver ? 'Drop files here...' : 'Reply to ekaiX...'
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
            fontSize: '0.875rem',
            lineHeight: 1.5,
            px: 1.5,
            pt: hasFiles ? 1 : 1.5,
            pb: 0.5,
            minHeight: 24,
            maxHeight: 120,
            overflow: 'auto',
            '&::placeholder': {
              color: 'text.secondary',
              opacity: 0.7,
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
            px: 1,
            pb: 1,
            pt: 0.25,
          }}
        >
          {/* Left: attachment button */}
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
                  border: 1,
                  borderColor: 'divider',
                  borderRadius: '50%',
                  width: 30,
                  height: 30,
                  '&:hover': {
                    bgcolor: 'action.hover',
                    borderColor: 'text.secondary',
                  },
                }}
              >
                <AddOutlined sx={{ fontSize: 18 }} />
              </IconButton>
            </span>
          </Tooltip>

          {/* Right: send or stop button */}
          {isStreaming ? (
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
                  width: 30,
                  height: 30,
                  borderRadius: '50%',
                  '&:hover': { bgcolor: 'text.secondary' },
                }}
              >
                <StopRounded sx={{ fontSize: 18 }} />
              </IconButton>
            </Tooltip>
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
                    width: 30,
                    height: 30,
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

      {/* Subtle helper text */}
      <Typography
        variant="caption"
        sx={{
          display: 'block',
          textAlign: 'center',
          mt: 0.75,
          color: 'text.disabled',
          fontSize: '0.65rem',
        }}
      >
        ekaiX can make mistakes. Verify important information.
      </Typography>
    </Box>
  );
}
