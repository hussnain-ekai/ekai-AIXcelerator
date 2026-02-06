'use client';

import { useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  Link,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';

const GOLD = '#D4A843';
const GREEN = '#4CAF50';
const RED = '#F44336';

interface DataQualityModalProps {
  open: boolean;
  onClose: () => void;
  score: number;
  onContinue: () => void;
  onViewReport: () => void;
}

type ScoreTier = 'good' | 'warning' | 'critical';

function getScoreTier(score: number): ScoreTier {
  if (score >= 70) return 'good';
  if (score >= 40) return 'warning';
  return 'critical';
}

function getScoreColor(tier: ScoreTier): string {
  switch (tier) {
    case 'good':
      return GREEN;
    case 'warning':
      return GOLD;
    case 'critical':
      return RED;
  }
}

function getScoreMessage(tier: ScoreTier): string {
  switch (tier) {
    case 'good':
      return 'Your data quality is good. You can proceed with confidence.';
    case 'warning':
      return 'Some quality issues detected. These may affect the accuracy of generated semantic models.';
    case 'critical':
      return 'Critical data quality issues found. The data does not meet the minimum quality threshold to proceed.';
  }
}

export function DataQualityModal({
  open,
  onClose,
  score,
  onContinue,
  onViewReport,
}: DataQualityModalProps): React.ReactNode {
  const [acknowledged, setAcknowledged] = useState(false);

  const tier = getScoreTier(score);
  const color = getScoreColor(tier);
  const message = getScoreMessage(tier);

  const canContinue =
    tier === 'good' || (tier === 'warning' && acknowledged);

  function handleClose(): void {
    setAcknowledged(false);
    onClose();
  }

  function handleContinue(): void {
    setAcknowledged(false);
    onContinue();
  }

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      slotProps={{
        paper: {
          sx: {
            borderRadius: 2,
            maxWidth: 500,
          },
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          pb: 1,
          fontWeight: 700,
        }}
      >
        Data Quality Assessment
        <IconButton onClick={handleClose} size="small">
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2.5 }}>
        {/* Circular score indicator */}
        <Box
          sx={{
            width: 140,
            height: 140,
            borderRadius: '50%',
            border: `6px solid ${color}`,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            mt: 1,
          }}
        >
          <Typography
            variant="h3"
            fontWeight={700}
            sx={{ color, lineHeight: 1 }}
          >
            {score}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
            out of 100
          </Typography>
        </Box>

        {/* Message */}
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ textAlign: 'center', maxWidth: 360 }}
        >
          {message}
        </Typography>

        {/* Warning acknowledgment checkbox */}
        {tier === 'warning' && (
          <FormControlLabel
            control={
              <Checkbox
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                sx={{ '&.Mui-checked': { color: GOLD } }}
              />
            }
            label={
              <Typography variant="body2" color="text.secondary">
                I understand that data quality issues may affect the accuracy of
                the semantic model
              </Typography>
            }
            sx={{ mx: 0, alignItems: 'flex-start' }}
          />
        )}

        {/* View full report link */}
        <Link
          component="button"
          variant="body2"
          underline="hover"
          onClick={onViewReport}
          sx={{ color: GOLD, fontWeight: 600 }}
        >
          View Full Report
        </Link>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {tier === 'critical' ? (
          <>
            <Button onClick={handleClose} color="inherit">
              Go Back
            </Button>
            <Button
              variant="outlined"
              sx={{
                borderColor: GOLD,
                color: GOLD,
                '&:hover': { borderColor: GOLD, bgcolor: 'action.hover' },
              }}
              onClick={handleClose}
            >
              Contact your data team
            </Button>
          </>
        ) : (
          <>
            <Button onClick={handleClose} color="inherit">
              Cancel
            </Button>
            <Button
              variant="contained"
              disabled={!canContinue}
              onClick={handleContinue}
            >
              Continue
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
}

export type { DataQualityModalProps };
