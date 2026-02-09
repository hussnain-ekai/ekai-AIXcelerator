'use client';

import { useEffect, useState } from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import type { PipelineProgress } from '@/stores/chatStore';

const GOLD = '#D4A843';

interface DiscoveryProgressProps {
  progress: PipelineProgress;
}

export function DiscoveryProgress({ progress }: DiscoveryProgressProps): React.ReactNode {
  const isComplete = progress.step === 'artifacts' && progress.status === 'completed';
  const stepNumber = progress.stepIndex + 1;
  const total = progress.totalSteps;

  // Fade out when complete
  const [opacity, setOpacity] = useState(1);
  useEffect(() => {
    if (isComplete) {
      // Brief visible "complete" state, then fade out
      const timer = setTimeout(() => setOpacity(0), 600);
      return () => clearTimeout(timer);
    } else {
      setOpacity(1);
    }
  }, [isComplete]);

  return (
    <Box sx={{
      display: 'flex',
      alignItems: 'flex-start',
      maxWidth: '75%',
      opacity,
      transition: 'opacity 0.5s ease',
    }}>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        <Typography
          variant="caption"
          sx={{ fontWeight: 700, color: GOLD }}
        >
          ekaiX
        </Typography>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            px: 2,
            py: 1.5,
            bgcolor: 'background.paper',
            borderRadius: 2,
            border: 1,
            borderColor: isComplete ? 'success.main' : 'divider',
            transition: 'border-color 0.3s ease',
          }}
        >
          {isComplete ? (
            <CheckCircleOutlineIcon sx={{ fontSize: 18, color: 'success.main' }} />
          ) : (
            <CircularProgress size={16} sx={{ color: GOLD }} />
          )}
          <Typography variant="body2" sx={{ color: 'text.primary' }}>
            {isComplete
              ? 'Analysis complete'
              : `Step ${stepNumber}/${total} — ${progress.label}`}
          </Typography>
          {!isComplete && progress.detail && progress.detail !== 'Done' && (
            <Typography variant="caption" sx={{ color: 'text.secondary', ml: 0.5 }}>
              {progress.detail}
            </Typography>
          )}
        </Box>
      </Box>
    </Box>
  );
}
