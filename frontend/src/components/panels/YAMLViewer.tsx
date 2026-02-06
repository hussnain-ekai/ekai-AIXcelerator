'use client';

import { useCallback, useState } from 'react';
import { Box, IconButton, Snackbar, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { ResizableDrawer } from './ResizableDrawer';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { atomDark } from 'react-syntax-highlighter/dist/cjs/styles/prism';

const GOLD = '#D4A843';
const DRAWER_WIDTH = 500;

interface YAMLViewerProps {
  open: boolean;
  onClose: () => void;
  yaml: string;
}

export function YAMLViewer({
  open,
  onClose,
  yaml,
}: YAMLViewerProps): React.ReactNode {
  const [snackbarOpen, setSnackbarOpen] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(yaml);
      setSnackbarOpen(true);
    } catch {
      // Clipboard API may not be available in some contexts
    }
  }, [yaml]);

  return (
    <ResizableDrawer
      defaultWidth={DRAWER_WIDTH}
      open={open}
      onClose={onClose}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
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
          <Typography variant="h6" fontWeight={700}>
            Semantic View YAML
          </Typography>
          <Box sx={{ display: 'flex', gap: 0.5 }}>
            <IconButton
              onClick={() => void handleCopy()}
              size="small"
              sx={{ color: GOLD }}
              title="Copy to clipboard"
            >
              <ContentCopyIcon fontSize="small" />
            </IconButton>
            <IconButton onClick={onClose} size="small">
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>
        </Box>

        {/* YAML content */}
        <Box sx={{ flex: 1, overflow: 'auto' }}>
          {yaml.length === 0 ? (
            <Box
              sx={{
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Typography variant="body2" color="text.secondary">
                No YAML content available.
              </Typography>
            </Box>
          ) : (
            <SyntaxHighlighter
              language="yaml"
              style={atomDark}
              customStyle={{
                margin: 0,
                padding: '16px 20px',
                fontSize: '0.8rem',
                lineHeight: 1.6,
                background: 'transparent',
                minHeight: '100%',
              }}
              showLineNumbers
              lineNumberStyle={{ color: '#616161', fontSize: '0.7rem' }}
            >
              {yaml}
            </SyntaxHighlighter>
          )}
        </Box>
      </Box>

      <Snackbar
        open={snackbarOpen}
        autoHideDuration={2000}
        onClose={() => setSnackbarOpen(false)}
        message="Copied to clipboard"
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      />
    </ResizableDrawer>
  );
}

export type { YAMLViewerProps };
