'use client';

import { Box, Drawer, IconButton, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import DescriptionIcon from '@mui/icons-material/Description';
import SchemaIcon from '@mui/icons-material/Schema';
import CodeIcon from '@mui/icons-material/Code';
import AssessmentIcon from '@mui/icons-material/Assessment';
import TableChartIcon from '@mui/icons-material/TableChart';
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks';

const GOLD = '#D4A843';
const DRAWER_WIDTH = 380;

type ArtifactType = 'erd' | 'yaml' | 'brd' | 'data_quality' | 'data_preview' | 'data_description';
type ArtifactPhase = 'DISCOVERY' | 'REQUIREMENTS' | 'GENERATION' | 'VALIDATION';

interface Artifact {
  id: string;
  type: ArtifactType;
  title: string;
  description?: string;
  phase: ArtifactPhase;
  createdAt: string;
  version?: number;
}

interface ArtifactsPanelProps {
  open: boolean;
  onClose: () => void;
  artifacts: Artifact[];
  onArtifactClick: (artifact: Artifact) => void;
}

const ARTIFACT_ICONS: Record<ArtifactType, React.ReactNode> = {
  brd: <DescriptionIcon fontSize="small" />,
  erd: <SchemaIcon fontSize="small" />,
  yaml: <CodeIcon fontSize="small" />,
  data_quality: <AssessmentIcon fontSize="small" />,
  data_preview: <TableChartIcon fontSize="small" />,
  data_description: <LibraryBooksIcon fontSize="small" />,
};

const PHASE_ORDER: ArtifactPhase[] = [
  'DISCOVERY',
  'REQUIREMENTS',
  'GENERATION',
  'VALIDATION',
];

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function groupByPhase(artifacts: Artifact[]): Map<ArtifactPhase, Artifact[]> {
  const grouped = new Map<ArtifactPhase, Artifact[]>();
  for (const phase of PHASE_ORDER) {
    const items = artifacts.filter((a) => a.phase === phase);
    if (items.length > 0) {
      grouped.set(phase, items);
    }
  }
  return grouped;
}

export function ArtifactsPanel({
  open,
  onClose,
  artifacts,
  onArtifactClick,
}: ArtifactsPanelProps): React.ReactNode {
  const grouped = groupByPhase(artifacts);

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
          <Box>
            <Typography variant="h6" fontWeight={700}>
              Artifacts
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {artifacts.length} generated
            </Typography>
          </Box>
          <IconButton onClick={onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Content */}
        <Box sx={{ flex: 1, overflow: 'auto', px: 2.5, py: 2 }}>
          {artifacts.length === 0 && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: 'center', mt: 4 }}
            >
              No artifacts generated yet.
            </Typography>
          )}

          {Array.from(grouped.entries()).map(([phase, items]) => (
            <Box key={phase} sx={{ mb: 3 }}>
              <Typography
                variant="subtitle2"
                color="text.secondary"
                sx={{ mb: 1.5, textTransform: 'uppercase', letterSpacing: '0.05em' }}
              >
                {phase}
              </Typography>

              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                {items.map((artifact) => (
                  <Box
                    key={artifact.id}
                    onClick={() => onArtifactClick(artifact)}
                    sx={{
                      display: 'flex',
                      gap: 1.5,
                      p: 1.5,
                      borderRadius: 1,
                      bgcolor: 'background.paper',
                      border: 1,
                      borderColor: 'divider',
                      borderLeft: `3px solid ${GOLD}`,
                      cursor: 'pointer',
                      transition: 'background-color 0.15s',
                      '&:hover': {
                        bgcolor: 'action.hover',
                      },
                    }}
                  >
                    <Box sx={{ color: GOLD, mt: 0.25 }}>
                      {ARTIFACT_ICONS[artifact.type]}
                    </Box>
                    <Box sx={{ minWidth: 0, flex: 1 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" fontWeight={700} noWrap>
                          {artifact.title}
                        </Typography>
                        {artifact.version !== undefined && (
                          <Box
                            component="span"
                            sx={{
                              px: 0.75,
                              py: 0.125,
                              borderRadius: 0.5,
                              bgcolor: 'action.selected',
                              color: 'text.secondary',
                              fontSize: '0.65rem',
                              fontWeight: 600,
                              letterSpacing: '0.02em',
                            }}
                          >
                            v{artifact.version}
                          </Box>
                        )}
                      </Box>
                      {artifact.description && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: 'block', mt: 0.25 }}
                          noWrap
                        >
                          {artifact.description}
                        </Typography>
                      )}
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: 'block', mt: 0.5 }}
                      >
                        {formatTimestamp(artifact.createdAt)}
                      </Typography>
                    </Box>
                  </Box>
                ))}
              </Box>
            </Box>
          ))}
        </Box>
      </Box>
    </Drawer>
  );
}

export type { Artifact, ArtifactType, ArtifactPhase, ArtifactsPanelProps };
