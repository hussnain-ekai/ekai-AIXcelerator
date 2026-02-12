'use client';

import {
  Box,
  Chip,
  IconButton,
  Skeleton,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import React, { useMemo } from 'react';
import { ResizableDrawer } from './ResizableDrawer';

const GOLD = '#D4A843';
const DRAWER_WIDTH = 500;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface GlossaryTerm {
  term: string;
  definition: string;
  physical_mapping?: string;
  related_tables?: string[];
}

interface GlossaryJSON {
  terms: GlossaryTerm[];
}

interface BusinessGlossaryResponse {
  id: string;
  data_product_id: string;
  version: number;
  glossary_json: GlossaryJSON | string;
  created_at: string;
}

interface BusinessGlossaryViewerProps {
  open: boolean;
  onClose: () => void;
  data: BusinessGlossaryResponse | null;
  isLoading?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Safe JSON parser                                                   */
/* ------------------------------------------------------------------ */

function parseGlossary(data: BusinessGlossaryResponse): GlossaryJSON | null {
  const raw = data.glossary_json;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw) as GlossaryJSON;
    } catch {
      return null;
    }
  }
  if (raw && typeof raw === 'object' && Array.isArray(raw.terms)) {
    return raw;
  }
  return null;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function BusinessGlossaryViewer({
  open,
  onClose,
  data,
  isLoading = false,
}: BusinessGlossaryViewerProps): React.ReactNode {
  const glossary = data ? parseGlossary(data) : null;

  const sortedTerms = useMemo(() => {
    if (!glossary?.terms) return [];
    return [...glossary.terms].sort((a, b) =>
      a.term.localeCompare(b.term, undefined, { sensitivity: 'base' }),
    );
  }, [glossary]);

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
              Business Glossary
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
              <Skeleton variant="text" width="40%" height={28} />
              <Skeleton variant="rectangular" height={80} sx={{ borderRadius: 1 }} />
              <Skeleton variant="text" width="35%" height={28} />
              <Skeleton variant="rectangular" height={80} sx={{ borderRadius: 1 }} />
              <Skeleton variant="text" width="45%" height={28} />
              <Skeleton variant="rectangular" height={80} sx={{ borderRadius: 1 }} />
            </Box>
          ) : sortedTerms.length === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: 'center', mt: 4 }}
            >
              No glossary available.
            </Typography>
          ) : (
            sortedTerms.map((item) => (
              <Box
                key={item.term}
                sx={{
                  border: 1,
                  borderColor: 'divider',
                  borderRadius: 1,
                  p: 2,
                  mb: 1.5,
                  bgcolor: 'action.hover',
                }}
              >
                {/* Term name */}
                <Typography
                  variant="subtitle1"
                  fontWeight={700}
                  sx={{ color: GOLD, mb: 0.5 }}
                >
                  {item.term}
                </Typography>

                {/* Definition */}
                <Typography variant="body2" sx={{ lineHeight: 1.6, mb: 1 }}>
                  {item.definition}
                </Typography>

                {/* Physical mapping + related tables */}
                {(item.physical_mapping || (item.related_tables && item.related_tables.length > 0)) && (
                  <Box
                    sx={{
                      mt: 1,
                      pt: 1,
                      borderTop: 1,
                      borderColor: 'divider',
                    }}
                  >
                    {item.physical_mapping && (
                      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.5, mb: 0.5 }}>
                        <Typography
                          variant="caption"
                          sx={{ color: 'text.secondary', flexShrink: 0 }}
                        >
                          Physical mapping:
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{ fontFamily: 'monospace', color: GOLD }}
                        >
                          {item.physical_mapping}
                        </Typography>
                      </Box>
                    )}

                    {item.related_tables && item.related_tables.length > 0 && (
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
                        <Typography
                          variant="caption"
                          sx={{ color: 'text.secondary', flexShrink: 0 }}
                        >
                          Related tables:
                        </Typography>
                        {item.related_tables.map((tbl) => (
                          <Chip
                            key={tbl}
                            label={tbl}
                            size="small"
                            sx={{
                              height: 18,
                              fontSize: '0.7em',
                              fontFamily: 'monospace',
                              bgcolor: 'background.default',
                              border: 1,
                              borderColor: 'divider',
                            }}
                          />
                        ))}
                      </Box>
                    )}
                  </Box>
                )}
              </Box>
            ))
          )}
        </Box>
      </Box>
    </ResizableDrawer>
  );
}

export type { BusinessGlossaryResponse, BusinessGlossaryViewerProps };
