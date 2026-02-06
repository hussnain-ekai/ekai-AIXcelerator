'use client';

import { Chip } from '@mui/material';

interface StatusConfig {
  color: string;
  label: string;
}

const STATUS_CONFIG: Record<string, StatusConfig> = {
  published: { color: '#4CAF50', label: 'Published' },
  discovery: { color: '#D4A843', label: 'Discovery' },
  requirements: { color: '#D4A843', label: 'Requirements' },
  generation: { color: '#D4A843', label: 'Generation' },
  validation: { color: '#D4A843', label: 'Validation' },
  archived: { color: '#9E9E9E', label: 'Archived' },
};

const DEFAULT_CONFIG: StatusConfig = { color: '#9E9E9E', label: 'Unknown' };

interface StatusBadgeProps {
  status: string;
}

export function StatusBadge({ status }: StatusBadgeProps): React.ReactNode {
  const cfg = STATUS_CONFIG[status] ?? { ...DEFAULT_CONFIG, label: status };

  return (
    <Chip
      label={cfg.label}
      variant="outlined"
      size="small"
      sx={{
        borderColor: cfg.color,
        color: cfg.color,
        fontWeight: 600,
        fontSize: '0.75rem',
      }}
    />
  );
}
