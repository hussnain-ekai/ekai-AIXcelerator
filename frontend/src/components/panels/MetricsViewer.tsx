'use client';

import {
  Box,
  Chip,
  IconButton,
  Skeleton,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import React from 'react';
import { ResizableDrawer } from './ResizableDrawer';

const GOLD = '#D4A843';
const DRAWER_WIDTH = 500;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Metric {
  name: string;
  description: string;
  formula: string;
  unit?: string;
  grain?: string;
  source_fact_table?: string;
  source_column?: string;
  brd_reference?: string;
}

interface MetricsJSON {
  metrics: Metric[];
}

interface MetricsResponse {
  id: string;
  data_product_id: string;
  version: number;
  metrics_json: MetricsJSON | string;
  created_at: string;
}

interface MetricsViewerProps {
  open: boolean;
  onClose: () => void;
  data: MetricsResponse | null;
  isLoading?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Safe JSON parser                                                   */
/* ------------------------------------------------------------------ */

function parseMetrics(data: MetricsResponse): MetricsJSON | null {
  const raw = data.metrics_json;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw) as MetricsJSON;
    } catch {
      return null;
    }
  }
  if (raw && typeof raw === 'object' && Array.isArray(raw.metrics)) {
    return raw;
  }
  return null;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function MetricsViewer({
  open,
  onClose,
  data,
  isLoading = false,
}: MetricsViewerProps): React.ReactNode {
  const parsed = data ? parseMetrics(data) : null;
  const metrics = parsed?.metrics ?? [];

  return (
    <ResizableDrawer defaultWidth={DRAWER_WIDTH} open={open} onClose={onClose}>
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
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Typography variant="h6" fontWeight={700}>
              Metrics &amp; KPIs
            </Typography>
            {data && (
              <Chip
                label={`v${data.version}`}
                size="small"
                sx={{
                  height: 20,
                  fontSize: '0.7em',
                  fontWeight: 600,
                  bgcolor: `${GOLD}22`,
                  color: GOLD,
                  border: `1px solid ${GOLD}44`,
                }}
              />
            )}
          </Box>
          <IconButton onClick={onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Scrollable content */}
        <Box sx={{ flex: 1, overflow: 'auto', px: 2.5, py: 2 }}>
          {isLoading ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
              <Skeleton variant="rectangular" height={140} sx={{ borderRadius: 1 }} />
              <Skeleton variant="rectangular" height={140} sx={{ borderRadius: 1 }} />
              <Skeleton variant="rectangular" height={140} sx={{ borderRadius: 1 }} />
            </Box>
          ) : metrics.length === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: 'center', mt: 4 }}
            >
              No metrics available.
            </Typography>
          ) : (
            metrics.map((metric) => (
              <Box
                key={metric.name}
                sx={{
                  border: 1,
                  borderColor: 'divider',
                  borderRadius: 1,
                  p: 2,
                  mb: 1.5,
                  bgcolor: 'action.hover',
                }}
              >
                {/* Metric name */}
                <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 0.75 }}>
                  {metric.name}
                </Typography>

                {/* Formula block */}
                <Box
                  sx={{
                    bgcolor: 'background.default',
                    border: 1,
                    borderColor: 'divider',
                    borderRadius: 1,
                    px: 1.5,
                    py: 1,
                    mb: 1.25,
                    fontFamily: 'monospace',
                    fontSize: '0.85em',
                    color: GOLD,
                    overflowX: 'auto',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {metric.formula}
                </Box>

                {/* Description */}
                <Typography variant="body2" sx={{ lineHeight: 1.6, mb: 1.25 }}>
                  {metric.description}
                </Typography>

                {/* Metadata chips */}
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
                  {metric.unit && (
                    <Chip
                      label={`Unit: ${metric.unit}`}
                      size="small"
                      variant="outlined"
                      sx={{ fontSize: '0.75em', borderColor: 'divider' }}
                    />
                  )}
                  {metric.grain && (
                    <Chip
                      label={`Grain: ${metric.grain}`}
                      size="small"
                      variant="outlined"
                      sx={{ fontSize: '0.75em', borderColor: 'divider' }}
                    />
                  )}
                  {metric.source_fact_table && (
                    <Chip
                      label={metric.source_fact_table}
                      size="small"
                      sx={{
                        fontSize: '0.7em',
                        fontFamily: 'monospace',
                        bgcolor: `${GOLD}11`,
                        color: GOLD,
                        border: `1px solid ${GOLD}33`,
                      }}
                    />
                  )}
                  {metric.source_column && (
                    <Chip
                      label={metric.source_column}
                      size="small"
                      sx={{
                        fontSize: '0.7em',
                        fontFamily: 'monospace',
                        bgcolor: 'background.default',
                        border: 1,
                        borderColor: 'divider',
                      }}
                    />
                  )}
                  {metric.brd_reference && (
                    <Chip
                      label={`BRD: ${metric.brd_reference}`}
                      size="small"
                      variant="outlined"
                      sx={{
                        fontSize: '0.75em',
                        borderColor: `${GOLD}44`,
                        color: 'text.secondary',
                      }}
                    />
                  )}
                </Box>
              </Box>
            ))
          )}
        </Box>
      </Box>
    </ResizableDrawer>
  );
}

export type { MetricsResponse, MetricsViewerProps };
