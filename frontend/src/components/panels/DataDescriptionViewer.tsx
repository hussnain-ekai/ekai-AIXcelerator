'use client';

import { Box, Chip, IconButton, Skeleton, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import React from 'react';
import { ResizableDrawer } from './ResizableDrawer';

const GOLD = '#D4A843';
const DRAWER_WIDTH = 500;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface DataDescriptionResponse {
  id: string;
  data_product_id: string;
  version: number;
  description_json: { document?: string } | string;
  created_by: string;
  created_at: string;
}

interface DataDescriptionViewerProps {
  open: boolean;
  onClose: () => void;
  dataDescription: DataDescriptionResponse | null;
  isLoading?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Parser — structured text to renderable blocks                      */
/* ------------------------------------------------------------------ */

type Block =
  | { kind: 'title'; text: string }
  | { kind: 'section'; number: string; text: string }
  | { kind: 'subsection'; text: string }
  | { kind: 'bullet'; text: string; inferred: boolean }
  | { kind: 'paragraph'; text: string; inferred: boolean };

const BEGIN_END_RE = /^---\s*(BEGIN|END)\s+DATA DESCRIPTION\s*---$/i;
const SECTION_RE = /^\[(\d+)\]\s+(.+)/;
const SUBSECTION_RE = /^\[(\d+\.\d+)\]\s+(.+)/;
const DATA_PRODUCT_RE = /^DATA PRODUCT:\s*(.*)/i;
const DATA_PRODUCT_ID_RE = /^Data Product ID.*$/i;
const BULLET_RE = /^[*\-]\s+(.*)/;

function parseDataDescriptionText(raw: string): Block[] {
  const blocks: Block[] = [];
  const lines = raw.split('\n');

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.length === 0) continue;
    if (BEGIN_END_RE.test(trimmed)) continue;
    if (DATA_PRODUCT_ID_RE.test(trimmed)) continue;

    let m: RegExpMatchArray | null;

    if ((m = trimmed.match(DATA_PRODUCT_RE))) {
      blocks.push({ kind: 'title', text: (m[1] ?? '').trim() });
    } else if ((m = trimmed.match(SUBSECTION_RE))) {
      blocks.push({ kind: 'subsection', text: (m[2] ?? '').trim() });
    } else if ((m = trimmed.match(SECTION_RE))) {
      blocks.push({ kind: 'section', number: m[1] ?? '', text: (m[2] ?? '').trim() });
    } else if ((m = trimmed.match(BULLET_RE))) {
      const inner = (m[1] ?? '').trim();
      const isInferred = inner.includes('(Inferred)');
      blocks.push({ kind: 'bullet', text: inner, inferred: isInferred });
    } else {
      const isInferred = trimmed.includes('(Inferred)');
      blocks.push({ kind: 'paragraph', text: trimmed, inferred: isInferred });
    }
  }

  return blocks;
}

/* ------------------------------------------------------------------ */
/*  Inline renderer — highlights (FIELD_NAME) and (Inferred)           */
/* ------------------------------------------------------------------ */

const FIELD_REF_RE = /\(([A-Z][A-Z0-9_]+)\)/g;
const INFERRED_RE = /\(Inferred\)/g;

function renderFieldRefs(text: string, keyBase: number): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  FIELD_REF_RE.lastIndex = 0;

  while ((match = FIELD_REF_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(
      <Box
        key={keyBase + match.index}
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

  return parts;
}

function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let keyCounter = 0;

  // Split on (Inferred) first
  const inferredParts = text.split(INFERRED_RE);
  for (let i = 0; i < inferredParts.length; i++) {
    const segment = inferredParts[i] ?? '';
    if (segment.length > 0) {
      parts.push(...renderFieldRefs(segment, keyCounter));
      keyCounter += 100;
    }
    if (i < inferredParts.length - 1) {
      parts.push(
        <Chip
          key={`inferred-${keyCounter++}`}
          label="Inferred"
          size="small"
          sx={{
            height: 18,
            fontSize: '0.7em',
            fontWeight: 600,
            bgcolor: `${GOLD}22`,
            color: GOLD,
            border: `1px solid ${GOLD}44`,
            mx: 0.5,
            verticalAlign: 'middle',
          }}
        />,
      );
    }
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
        <Typography key={index} variant="h5" fontWeight={700} sx={{ mb: 1 }}>
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
/*  Extract document text from description_json                        */
/* ------------------------------------------------------------------ */

function extractDocument(dd: DataDescriptionResponse): string | null {
  const json = dd.description_json;
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

export function DataDescriptionViewer({
  open,
  onClose,
  dataDescription,
  isLoading = false,
}: DataDescriptionViewerProps): React.ReactNode {
  const document = dataDescription ? extractDocument(dataDescription) : null;
  const blocks = document ? parseDataDescriptionText(document) : [];

  return (
    <ResizableDrawer
      defaultWidth={DRAWER_WIDTH}
      open={open}
      onClose={onClose}
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
            Data Description
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
              No data description available.
            </Typography>
          ) : (
            blocks.map(renderBlock)
          )}
        </Box>
      </Box>
    </ResizableDrawer>
  );
}

export type { DataDescriptionResponse, DataDescriptionViewerProps };
