'use client';

import { Box, Typography } from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';
import type { AgentPhase } from '@/stores/chatStore';

interface PhaseStepperProps {
  currentPhase: AgentPhase;
}

interface PhaseStep {
  key: AgentPhase;
  label: string;
}

const PHASES: PhaseStep[] = [
  { key: 'discovery', label: 'Discovery' },
  { key: 'requirements', label: 'Requirements' },
  { key: 'generation', label: 'Generation' },
  { key: 'validation', label: 'Validation' },
  { key: 'publishing', label: 'Publishing' },
];

const GOLD = '#D4A843';
const GRAY = '#616161';
const GRAY_TEXT = '#9E9E9E';

type StepStatus = 'completed' | 'active' | 'future';

function getPhaseIndex(phase: AgentPhase): number {
  // 'explorer' comes after publishing — all 5 phases are complete
  if (phase === 'explorer') return PHASES.length;
  const index = PHASES.findIndex((p) => p.key === phase);
  return index >= 0 ? index : -1;
}

function getStepStatus(stepIndex: number, activeIndex: number): StepStatus {
  if (stepIndex < activeIndex) return 'completed';
  if (stepIndex === activeIndex) return 'active';
  return 'future';
}

function StepCircle({ status }: { status: StepStatus }): React.ReactNode {
  const size = 28;
  const transition = 'all 0.4s ease';

  if (status === 'completed') {
    return (
      <Box
        sx={{
          width: size,
          height: size,
          borderRadius: '50%',
          bgcolor: GOLD,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition,
        }}
      >
        <CheckIcon sx={{ fontSize: 16, color: '#1A1A1E' }} />
      </Box>
    );
  }

  if (status === 'active') {
    return (
      <Box
        sx={{
          width: size,
          height: size,
          borderRadius: '50%',
          bgcolor: GOLD,
          transition,
          boxShadow: `0 0 0 4px rgba(212, 168, 67, 0.25)`,
        }}
      />
    );
  }

  return (
    <Box
      sx={{
        width: size,
        height: size,
        borderRadius: '50%',
        bgcolor: 'transparent',
        border: 2,
        borderColor: GRAY,
        transition,
      }}
    />
  );
}

function ConnectorLine({ status }: { status: StepStatus }): React.ReactNode {
  return (
    <Box
      sx={{
        flex: 1,
        height: 2,
        bgcolor: status === 'completed' ? GOLD : GRAY,
        mx: 1,
        transition: 'background-color 0.4s ease',
      }}
    />
  );
}

export function PhaseStepper({ currentPhase }: PhaseStepperProps): React.ReactNode {
  const activeIndex = getPhaseIndex(currentPhase);

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', px: 3, py: 2 }}>
      {PHASES.map((phase, index) => {
        const status = getStepStatus(index, activeIndex);
        const isLast = index === PHASES.length - 1;
        const textColor = status === 'future' ? GRAY_TEXT : GOLD;

        return (
          <Box
            key={phase.key}
            sx={{
              display: 'flex',
              alignItems: 'center',
              flex: isLast ? 'none' : 1,
            }}
          >
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                minWidth: 80,
              }}
            >
              <StepCircle status={status} />
              <Typography
                variant="caption"
                sx={{ mt: 0.5, fontWeight: 600, color: textColor, transition: 'color 0.4s ease' }}
              >
                {phase.label}
              </Typography>
            </Box>
            {!isLast && <ConnectorLine status={getStepStatus(index, activeIndex)} />}
          </Box>
        );
      })}
    </Box>
  );
}
