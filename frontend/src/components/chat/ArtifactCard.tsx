'use client';

import { Box, ButtonBase, Typography } from '@mui/material';
import SchemaIcon from '@mui/icons-material/Schema';
import AssessmentIcon from '@mui/icons-material/Assessment';
import CodeIcon from '@mui/icons-material/Code';
import DescriptionIcon from '@mui/icons-material/Description';
import TableChartIcon from '@mui/icons-material/TableChart';
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks';
import type { ArtifactType } from '@/stores/chatStore';

interface ArtifactCardProps {
  type: ArtifactType;
  title: string;
  description?: string;
  onClick: () => void;
}

const GOLD = '#D4A843';

const ARTIFACT_META: Record<ArtifactType, { icon: typeof SchemaIcon; label: string; description: string }> = {
  erd: { icon: SchemaIcon, label: 'ERD Diagram', description: 'Entity-relationship diagram of discovered tables' },
  data_quality: { icon: AssessmentIcon, label: 'Data Quality Report', description: 'Source data health assessment' },
  yaml: { icon: CodeIcon, label: 'Semantic View YAML', description: 'Generated semantic model definition' },
  brd: { icon: DescriptionIcon, label: 'Business Requirements', description: 'Structured requirements document' },
  data_preview: { icon: TableChartIcon, label: 'Data Preview', description: 'Sample query results' },
  data_description: { icon: LibraryBooksIcon, label: 'Data Description', description: 'Business context and domain analysis' },
};

export function ArtifactCard({ type, title, description, onClick }: ArtifactCardProps): React.ReactNode {
  const meta = ARTIFACT_META[type];
  if (!meta) return null; // Skip unknown artifact types
  const Icon = meta.icon;
  const displayTitle = title !== type.toUpperCase() ? title : meta.label;
  const displayDesc = description ?? meta.description;

  return (
    <ButtonBase
      onClick={onClick}
      sx={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 1.5,
        p: 1.5,
        mt: 1,
        borderRadius: 1.5,
        border: 1,
        borderColor: 'divider',
        borderLeft: `3px solid ${GOLD}`,
        bgcolor: 'background.paper',
        textAlign: 'left',
        width: '100%',
        transition: 'background-color 0.15s',
        '&:hover': { bgcolor: 'action.hover' },
      }}
    >
      <Icon sx={{ color: GOLD, fontSize: 20, mt: 0.25 }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.4 }}>
          {displayTitle}
        </Typography>
        <Typography variant="caption" sx={{ color: 'text.secondary', lineHeight: 1.3 }}>
          {displayDesc}
        </Typography>
      </Box>
    </ButtonBase>
  );
}

export type { ArtifactCardProps };
