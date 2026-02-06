'use client';

import { Box, Drawer, IconButton, Skeleton, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import React from 'react';

const GOLD = '#D4A843';
const DRAWER_WIDTH = 500;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface BRDResponse {
  id: string;
  data_product_id: string;
  version: number;
  brd_json: { document?: string } | string;
  is_complete: boolean;
  created_by: string;
  created_at: string;
}

interface BRDViewerProps {
  open: boolean;
  onClose: () => void;
  brd: BRDResponse | null;
  isLoading?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Parser — structured text → renderable blocks                       */
/* ------------------------------------------------------------------ */

type Block =
  | { kind: 'title'; text: string }
  | { kind: 'description'; text: string }
  | { kind: 'section'; text: string }
  | { kind: 'subsection'; text: string }
  | { kind: 'bullet'; text: string }
  | { kind: 'keyvalue'; label: string; value: string }
  | { kind: 'paragraph'; text: string };

const BEGIN_END_RE = /^---\s*(BEGIN|END)\s+BRD\s*---$/i;
const SECTION_RE = /^SECTION\s+\d+:\s*(.+)/i;
const SUBSECTION_RE = /^\d+\.\d+\s+(.+)/;
const BULLET_RE = /^[*\-]\s+(.*)/;
const KV_RE = /^(Metric|Dimension|Source|Calculation|Type|Join|Fields|Filter|Aggregation|Grain|Relationship|Cardinality):\s*(.*)/i;
const DATA_PRODUCT_RE = /^DATA PRODUCT:\s*(.*)/i;
const DESCRIPTION_RE = /^DESCRIPTION:\s*(.*)/i;

function parseBRDText(raw: string): Block[] {
  const blocks: Block[] = [];
  const lines = raw.split('\n');

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.length === 0) continue;
    if (BEGIN_END_RE.test(trimmed)) continue;

    let m: RegExpMatchArray | null;

    if ((m = trimmed.match(DATA_PRODUCT_RE))) {
      blocks.push({ kind: 'title', text: (m[1] ?? '').trim() });
    } else if ((m = trimmed.match(DESCRIPTION_RE))) {
      blocks.push({ kind: 'description', text: (m[1] ?? '').trim() });
    } else if ((m = trimmed.match(SECTION_RE))) {
      blocks.push({ kind: 'section', text: (m[1] ?? '').trim() });
    } else if ((m = trimmed.match(SUBSECTION_RE))) {
      blocks.push({ kind: 'subsection', text: (m[1] ?? '').trim() });
    } else if ((m = trimmed.match(BULLET_RE))) {
      const inner = (m[1] ?? '').trim();
      const kvInner = inner.match(KV_RE);
      if (kvInner) {
        blocks.push({ kind: 'keyvalue', label: kvInner[1] ?? '', value: (kvInner[2] ?? '').trim() });
      } else {
        blocks.push({ kind: 'bullet', text: inner });
      }
    } else if ((m = trimmed.match(KV_RE))) {
      blocks.push({ kind: 'keyvalue', label: m[1] ?? '', value: (m[2] ?? '').trim() });
    } else {
      blocks.push({ kind: 'paragraph', text: trimmed });
    }
  }

  return blocks;
}

/* ------------------------------------------------------------------ */
/*  Inline renderer — highlights (FIELD_NAME) patterns                 */
/* ------------------------------------------------------------------ */

const FIELD_REF_RE = /\(([A-Z][A-Z0-9_]+)\)/g;

function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  // Reset regex state
  FIELD_REF_RE.lastIndex = 0;

  while ((match = FIELD_REF_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(
      <Box
        key={match.index}
        component="span"
        sx={{
          fontFamily: 'monospace',
          fontSize: '0.8em',
          bgcolor: 'action.hover',
          px: 0.5,
          py: 0.25,
          borderRadius: 0.5,
          color: GOLD,
        }}
      >
        {match[1]}
      </Box>,
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? <>{parts}</> : text;
}

/* ------------------------------------------------------------------ */
/*  Block renderers                                                    */
/* ------------------------------------------------------------------ */

function renderBlock(block: Block, index: number): React.ReactNode {
  switch (block.kind) {
    case 'title':
      return (
        <Typography key={index} variant="h5" fontWeight={700} sx={{ mb: 0.5 }}>
          {block.text}
        </Typography>
      );
    case 'description':
      return (
        <Typography
          key={index}
          variant="body2"
          color="text.secondary"
          sx={{ mb: 2.5 }}
        >
          {block.text}
        </Typography>
      );
    case 'section':
      return (
        <Typography
          key={index}
          variant="h6"
          fontWeight={700}
          sx={{
            mt: 3,
            mb: 1,
            pl: 1.5,
            borderLeft: 3,
            borderColor: GOLD,
          }}
        >
          {block.text}
        </Typography>
      );
    case 'subsection':
      return (
        <Typography
          key={index}
          variant="subtitle2"
          fontWeight={600}
          sx={{ mt: 2, mb: 0.5 }}
        >
          {block.text}
        </Typography>
      );
    case 'bullet':
      return (
        <Box key={index} sx={{ display: 'flex', gap: 1, pl: 1, mb: 0.5 }}>
          <Box
            component="span"
            sx={{ color: GOLD, fontWeight: 700, flexShrink: 0 }}
          >
            &bull;
          </Box>
          <Typography variant="body2" sx={{ lineHeight: 1.6 }}>
            {renderInline(block.text)}
          </Typography>
        </Box>
      );
    case 'keyvalue':
      return (
        <Box key={index} sx={{ display: 'flex', gap: 1, pl: 1, mb: 0.5 }}>
          <Box
            component="span"
            sx={{ color: GOLD, fontWeight: 700, flexShrink: 0 }}
          >
            &bull;
          </Box>
          <Typography variant="body2" sx={{ lineHeight: 1.6 }}>
            <Box component="span" sx={{ fontWeight: 700 }}>
              {block.label}:
            </Box>{' '}
            {renderInline(block.value)}
          </Typography>
        </Box>
      );
    case 'paragraph':
      return (
        <Typography
          key={index}
          variant="body2"
          sx={{ mb: 0.75, lineHeight: 1.6 }}
        >
          {renderInline(block.text)}
        </Typography>
      );
  }
}

/* ------------------------------------------------------------------ */
/*  Extract document text from brd_json                                */
/* ------------------------------------------------------------------ */

function extractDocument(brd: BRDResponse): string | null {
  const json = brd.brd_json;
  if (typeof json === 'string') {
    try {
      const parsed = JSON.parse(json) as Record<string, unknown>;
      if (typeof parsed.document === 'string') return parsed.document;
      return json;
    } catch {
      return json;
    }
  }
  if (json && typeof json === 'object' && typeof json.document === 'string') {
    return json.document;
  }
  return null;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function BRDViewer({
  open,
  onClose,
  brd,
  isLoading = false,
}: BRDViewerProps): React.ReactNode {
  const document = brd ? extractDocument(brd) : null;
  const blocks = document ? parseBRDText(document) : [];

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
          <Typography variant="h6" fontWeight={700}>
            Business Requirements
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Scrollable content */}
        <Box sx={{ flex: 1, overflow: 'auto', px: 2.5, py: 2 }}>
          {isLoading ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
              <Skeleton variant="text" width="60%" height={32} />
              <Skeleton variant="text" width="80%" />
              <Skeleton variant="rectangular" height={120} sx={{ borderRadius: 1 }} />
              <Skeleton variant="text" width="50%" height={28} />
              <Skeleton variant="rectangular" height={80} sx={{ borderRadius: 1 }} />
              <Skeleton variant="text" width="55%" height={28} />
              <Skeleton variant="rectangular" height={100} sx={{ borderRadius: 1 }} />
            </Box>
          ) : blocks.length === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: 'center', mt: 4 }}
            >
              No BRD available.
            </Typography>
          ) : (
            blocks.map(renderBlock)
          )}
        </Box>
      </Box>
    </Drawer>
  );
}

export type { BRDResponse, BRDViewerProps };
