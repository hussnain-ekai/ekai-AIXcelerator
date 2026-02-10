'use client';

import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Box, Button, Typography } from '@mui/material';
import RefreshOutlined from '@mui/icons-material/RefreshOutlined';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallbackMessage?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Component-level error boundary. Catches render errors in children
 * and displays a retry button instead of crashing the whole page.
 */
export class ComponentErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            py: 4,
            px: 2,
            gap: 1.5,
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {this.props.fallbackMessage ?? 'This section failed to render.'}
          </Typography>
          <Button
            variant="outlined"
            size="small"
            startIcon={<RefreshOutlined />}
            onClick={this.handleRetry}
            sx={{ borderColor: 'divider' }}
          >
            Retry
          </Button>
        </Box>
      );
    }

    return this.props.children;
  }
}
