'use client';

import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Chip,
  IconButton,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import ExpandMoreOutlined from '@mui/icons-material/ExpandMoreOutlined';
import React from 'react';
import { ResizableDrawer } from './ResizableDrawer';

const GOLD = '#D4A843';
const DRAWER_WIDTH = 500;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface CatalogColumn {
  name: string;
  data_type: string;
  description: string;
  source_column?: string;
  role?: string;
}

interface CatalogTable {
  name: string;
  type: string;
  description: string;
  grain?: string;
  source_tables?: string[];
  row_count?: number;
  columns: CatalogColumn[];
}

interface CatalogJSON {
  tables: CatalogTable[];
}

interface DataCatalogResponse {
  id: string;
  data_product_id: string;
  version: number;
  catalog_json: CatalogJSON | string;
  created_at: string;
}

interface DataCatalogViewerProps {
  open: boolean;
  onClose: () => void;
  data: DataCatalogResponse | null;
  isLoading?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Safe JSON parser                                                   */
/* ------------------------------------------------------------------ */

function parseCatalog(data: DataCatalogResponse): CatalogJSON | null {
  const raw = data.catalog_json;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw) as CatalogJSON;
    } catch {
      return null;
    }
  }
  if (raw && typeof raw === 'object' && Array.isArray(raw.tables)) {
    return raw;
  }
  return null;
}

/* ------------------------------------------------------------------ */
/*  Type badge                                                         */
/* ------------------------------------------------------------------ */

function TypeBadge({ type }: { type: string }): React.ReactNode {
  const label = type.toUpperCase();
  const isFact = label === 'FACT';
  return (
    <Chip
      label={label}
      size="small"
      sx={{
        height: 20,
        fontSize: '0.7em',
        fontWeight: 700,
        bgcolor: isFact ? `${GOLD}22` : 'action.hover',
        color: isFact ? GOLD : 'text.secondary',
        border: `1px solid ${isFact ? `${GOLD}44` : 'transparent'}`,
        ml: 1,
      }}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function DataCatalogViewer({
  open,
  onClose,
  data,
  isLoading = false,
}: DataCatalogViewerProps): React.ReactNode {
  const catalog = data ? parseCatalog(data) : null;
  const tables = catalog?.tables ?? [];

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
              Data Catalog
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
              <Skeleton variant="text" width="60%" height={32} />
              <Skeleton variant="rectangular" height={120} sx={{ borderRadius: 1 }} />
              <Skeleton variant="text" width="50%" height={28} />
              <Skeleton variant="rectangular" height={100} sx={{ borderRadius: 1 }} />
            </Box>
          ) : tables.length === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: 'center', mt: 4 }}
            >
              No data catalog available.
            </Typography>
          ) : (
            tables.map((table, idx) => (
              <Accordion
                key={table.name}
                defaultExpanded={idx === 0}
                disableGutters
                sx={{
                  bgcolor: 'transparent',
                  '&:before': { display: 'none' },
                  border: 1,
                  borderColor: 'divider',
                  borderRadius: 1,
                  mb: 1.5,
                  overflow: 'hidden',
                }}
              >
                <AccordionSummary
                  expandIcon={<ExpandMoreOutlined sx={{ color: 'text.secondary' }} />}
                  sx={{
                    bgcolor: 'action.hover',
                    '& .MuiAccordionSummary-content': {
                      alignItems: 'center',
                    },
                  }}
                >
                  <Typography variant="subtitle2" fontWeight={700}>
                    {table.name}
                  </Typography>
                  <TypeBadge type={table.type} />
                </AccordionSummary>

                <AccordionDetails sx={{ px: 2, py: 1.5 }}>
                  {/* Description */}
                  <Typography variant="body2" sx={{ mb: 1.5, lineHeight: 1.6 }}>
                    {table.description}
                  </Typography>

                  {/* Metadata row */}
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 1.5 }}>
                    {table.grain && (
                      <Chip
                        label={`Grain: ${table.grain}`}
                        size="small"
                        variant="outlined"
                        sx={{ fontSize: '0.75em', borderColor: 'divider' }}
                      />
                    )}
                    {table.row_count != null && (
                      <Chip
                        label={`${table.row_count.toLocaleString()} rows`}
                        size="small"
                        variant="outlined"
                        sx={{ fontSize: '0.75em', borderColor: 'divider' }}
                      />
                    )}
                    {table.source_tables?.map((src) => (
                      <Chip
                        key={src}
                        label={src}
                        size="small"
                        sx={{
                          fontSize: '0.7em',
                          fontFamily: 'monospace',
                          bgcolor: 'action.hover',
                        }}
                      />
                    ))}
                  </Box>

                  {/* Columns table */}
                  {table.columns.length > 0 && (
                    <Box sx={{ overflowX: 'auto' }}>
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell sx={{ fontWeight: 700, color: GOLD, fontSize: '0.75em', py: 0.75 }}>
                              Name
                            </TableCell>
                            <TableCell sx={{ fontWeight: 700, color: GOLD, fontSize: '0.75em', py: 0.75 }}>
                              Type
                            </TableCell>
                            <TableCell sx={{ fontWeight: 700, color: GOLD, fontSize: '0.75em', py: 0.75 }}>
                              Role
                            </TableCell>
                            <TableCell sx={{ fontWeight: 700, color: GOLD, fontSize: '0.75em', py: 0.75 }}>
                              Description
                            </TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {table.columns.map((col) => (
                            <TableRow key={col.name}>
                              <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.8em', py: 0.5 }}>
                                {col.name}
                              </TableCell>
                              <TableCell sx={{ fontSize: '0.8em', py: 0.5, color: 'text.secondary' }}>
                                {col.data_type}
                              </TableCell>
                              <TableCell sx={{ fontSize: '0.8em', py: 0.5, color: 'text.secondary' }}>
                                {col.role ?? '\u2014'}
                              </TableCell>
                              <TableCell sx={{ fontSize: '0.8em', py: 0.5 }}>
                                {col.description}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </Box>
                  )}
                </AccordionDetails>
              </Accordion>
            ))
          )}
        </Box>
      </Box>
    </ResizableDrawer>
  );
}

export type { DataCatalogResponse, DataCatalogViewerProps };
