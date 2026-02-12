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

interface ValidationRule {
  name: string;
  type?: string;
  category?: string;
  table: string;
  description?: string;
  sql_check?: string;
  check_sql?: string;
  severity: string;
  expected?: string;
  expected_result?: string;
  failure_action?: string;
}

interface RulesJSON {
  rules: ValidationRule[];
}

interface ValidationRulesResponse {
  id: string;
  data_product_id: string;
  version: number;
  rules_json: RulesJSON | string;
  created_at: string;
}

interface ValidationRulesViewerProps {
  open: boolean;
  onClose: () => void;
  data: ValidationRulesResponse | null;
  isLoading?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Safe JSON parser                                                   */
/* ------------------------------------------------------------------ */

function parseRules(data: ValidationRulesResponse): RulesJSON | null {
  const raw = data.rules_json;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw) as RulesJSON;
    } catch {
      return null;
    }
  }
  if (raw && typeof raw === 'object' && Array.isArray(raw.rules)) {
    return raw;
  }
  return null;
}

/* ------------------------------------------------------------------ */
/*  Severity config                                                    */
/* ------------------------------------------------------------------ */

const SEVERITY_ORDER: Record<string, number> = {
  CRITICAL: 0,
  WARNING: 1,
  INFO: 2,
};

const SEVERITY_COLORS: Record<string, { bg: string; fg: string; border: string }> = {
  CRITICAL: { bg: '#EF444422', fg: '#EF4444', border: '#EF444444' },
  WARNING: { bg: `${GOLD}22`, fg: GOLD, border: `${GOLD}44` },
  INFO: { bg: '#3B82F622', fg: '#3B82F6', border: '#3B82F644' },
};

function getSeverityStyle(severity: string): { bg: string; fg: string; border: string } {
  return SEVERITY_COLORS[severity.toUpperCase()] ?? SEVERITY_COLORS.INFO;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function ValidationRulesViewer({
  open,
  onClose,
  data,
  isLoading = false,
}: ValidationRulesViewerProps): React.ReactNode {
  const parsed = data ? parseRules(data) : null;

  const grouped = useMemo(() => {
    if (!parsed?.rules) return new Map<string, ValidationRule[]>();
    const map = new Map<string, ValidationRule[]>();
    for (const rule of parsed.rules) {
      const key = rule.severity.toUpperCase();
      const list = map.get(key) ?? [];
      list.push(rule);
      map.set(key, list);
    }
    // Sort groups by severity order
    const sorted = new Map<string, ValidationRule[]>();
    const keys = [...map.keys()].sort(
      (a, b) => (SEVERITY_ORDER[a] ?? 99) - (SEVERITY_ORDER[b] ?? 99),
    );
    for (const k of keys) {
      const rules = map.get(k);
      if (rules) sorted.set(k, rules);
    }
    return sorted;
  }, [parsed]);

  const totalRules = parsed?.rules.length ?? 0;

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
              Validation Rules
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
              <Skeleton variant="rectangular" height={60} sx={{ borderRadius: 1 }} />
              <Skeleton variant="rectangular" height={60} sx={{ borderRadius: 1 }} />
              <Skeleton variant="text" width="30%" height={28} />
              <Skeleton variant="rectangular" height={60} sx={{ borderRadius: 1 }} />
            </Box>
          ) : totalRules === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: 'center', mt: 4 }}
            >
              No validation rules available.
            </Typography>
          ) : (
            <>
              {/* Summary line */}
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {totalRules} rule{totalRules !== 1 ? 's' : ''} defined
              </Typography>

              {[...grouped.entries()].map(([severity, rules]) => {
                const style = getSeverityStyle(severity);
                return (
                  <Box key={severity} sx={{ mb: 2.5 }}>
                    {/* Severity group header */}
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        mb: 1,
                        pl: 1.5,
                        borderLeft: 3,
                        borderColor: style.fg,
                      }}
                    >
                      <Typography variant="subtitle2" fontWeight={700}>
                        {severity}
                      </Typography>
                      <Chip
                        label={rules.length}
                        size="small"
                        sx={{
                          height: 18,
                          fontSize: '0.7em',
                          fontWeight: 700,
                          bgcolor: style.bg,
                          color: style.fg,
                          border: `1px solid ${style.border}`,
                        }}
                      />
                    </Box>

                    {/* Rules */}
                    {rules.map((rule) => (
                      <Box
                        key={rule.name}
                        sx={{
                          border: 1,
                          borderColor: 'divider',
                          borderRadius: 1,
                          p: 1.5,
                          mb: 1,
                          bgcolor: 'action.hover',
                        }}
                      >
                        {/* Rule header row */}
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75 }}>
                          {/* Severity dot */}
                          <Box
                            sx={{
                              width: 8,
                              height: 8,
                              borderRadius: '50%',
                              bgcolor: style.fg,
                              flexShrink: 0,
                            }}
                          />
                          <Typography variant="subtitle2" fontWeight={700} sx={{ flex: 1 }}>
                            {rule.name}
                          </Typography>
                          <Chip
                            label={(rule.type ?? rule.category ?? '').replace(/_/g, ' ')}
                            size="small"
                            sx={{
                              height: 18,
                              fontSize: '0.65em',
                              fontWeight: 600,
                              textTransform: 'uppercase',
                              bgcolor: 'background.default',
                              border: 1,
                              borderColor: 'divider',
                            }}
                          />
                        </Box>

                        {/* Table name */}
                        <Typography
                          variant="caption"
                          sx={{
                            fontFamily: 'monospace',
                            color: GOLD,
                            display: 'block',
                            mb: 0.5,
                          }}
                        >
                          {rule.table}
                        </Typography>

                        {/* Description */}
                        {rule.description && (
                          <Typography variant="body2" sx={{ lineHeight: 1.5, mb: 0.75 }}>
                            {rule.description}
                          </Typography>
                        )}

                        {/* SQL check */}
                        {(rule.sql_check ?? rule.check_sql) && (
                          <Box
                            sx={{
                              bgcolor: 'background.default',
                              border: 1,
                              borderColor: 'divider',
                              borderRadius: 1,
                              px: 1.5,
                              py: 0.75,
                              mb: 0.75,
                              fontFamily: 'monospace',
                              fontSize: '0.8em',
                              color: 'text.secondary',
                              overflowX: 'auto',
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                            }}
                          >
                            {rule.sql_check ?? rule.check_sql}
                          </Box>
                        )}

                        {/* Expected value */}
                        {(rule.expected ?? rule.expected_result) && (
                          <Typography variant="caption" color="text.secondary">
                            Expected: {rule.expected ?? rule.expected_result}
                          </Typography>
                        )}
                      </Box>
                    ))}
                  </Box>
                );
              })}
            </>
          )}
        </Box>
      </Box>
    </ResizableDrawer>
  );
}

export type { ValidationRulesResponse, ValidationRulesViewerProps };
