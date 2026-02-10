'use client';

import { Box, Button, Typography } from '@mui/material';
import RefreshOutlined from '@mui/icons-material/RefreshOutlined';

const GOLD = '#D4A843';

export default function DataProductError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.ReactNode {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: 'calc(100vh - 48px)',
        gap: 2,
        px: 3,
      }}
    >
      <Typography variant="h6" color="text.primary">
        Something went wrong
      </Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ maxWidth: 480, textAlign: 'center' }}
      >
        {error.message || 'An unexpected error occurred while loading this data product.'}
      </Typography>
      <Button
        variant="outlined"
        startIcon={<RefreshOutlined />}
        onClick={reset}
        sx={{ mt: 1, borderColor: GOLD, color: GOLD }}
      >
        Try again
      </Button>
    </Box>
  );
}
