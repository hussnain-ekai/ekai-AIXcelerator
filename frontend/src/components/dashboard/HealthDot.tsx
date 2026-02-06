'use client';

import { Box, Tooltip } from '@mui/material';

interface HealthDotProps {
  score: number | null;
}

function getHealthColor(score: number): string {
  if (score >= 70) return '#4CAF50';
  if (score >= 40) return '#D4A843';
  return '#F44336';
}

function getHealthLabel(score: number): string {
  if (score >= 70) return 'Healthy';
  if (score >= 40) return 'Needs Attention';
  return 'Critical';
}

const DOT_SX = {
  width: 10,
  height: 10,
  borderRadius: '50%',
} as const;

export function HealthDot({ score }: HealthDotProps): React.ReactNode {
  if (score === null) {
    return <Box sx={{ ...DOT_SX, bgcolor: '#616161' }} />;
  }

  const color = getHealthColor(score);
  const label = getHealthLabel(score);

  return (
    <Tooltip title={`${label} (${score}%)`}>
      <Box sx={{ ...DOT_SX, bgcolor: color }} />
    </Tooltip>
  );
}
