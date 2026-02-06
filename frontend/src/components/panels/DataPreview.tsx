'use client';

import {
  Box,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { ResizableDrawer } from './ResizableDrawer';
import DownloadIcon from '@mui/icons-material/Download';

const GOLD = '#D4A843';
const DRAWER_WIDTH = 600;

interface DataPreviewProps {
  open: boolean;
  onClose: () => void;
  data: { columns: string[]; rows: Record<string, unknown>[] } | null;
}

function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function exportToCsv(columns: string[], rows: Record<string, unknown>[]): void {
  const header = columns.join(',');
  const body = rows
    .map((row) =>
      columns
        .map((col) => {
          const val = formatCellValue(row[col]);
          // Escape values containing commas or quotes
          if (val.includes(',') || val.includes('"') || val.includes('\n')) {
            return `"${val.replace(/"/g, '""')}"`;
          }
          return val;
        })
        .join(','),
    )
    .join('\n');

  const csvContent = `${header}\n${body}`;
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.href = url;
  link.download = 'data-preview.csv';
  link.click();

  URL.revokeObjectURL(url);
}

export function DataPreview({
  open,
  onClose,
  data,
}: DataPreviewProps): React.ReactNode {
  const columns = data?.columns ?? [];
  const rows = data?.rows ?? [];

  function handleExport(): void {
    if (data) {
      exportToCsv(data.columns, data.rows);
    }
  }

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
            Data Preview
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Table content */}
        <Box sx={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          {data === null ? (
            <Box
              sx={{
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Typography variant="body2" color="text.secondary">
                No data available.
              </Typography>
            </Box>
          ) : (
            <TableContainer>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    {columns.map((col) => (
                      <TableCell
                        key={col}
                        sx={{
                          fontWeight: 700,
                          bgcolor: 'background.paper',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {col}
                      </TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {rows.map((row, rowIdx) => (
                    <TableRow key={rowIdx} hover>
                      {columns.map((col) => (
                        <TableCell
                          key={`${rowIdx}-${col}`}
                          sx={{ whiteSpace: 'nowrap', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis' }}
                        >
                          <Typography variant="body2" noWrap>
                            {formatCellValue(row[col])}
                          </Typography>
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Box>

        {/* Footer */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 2.5,
            py: 1.5,
            borderTop: 1,
            borderColor: 'divider',
          }}
        >
          <Typography variant="caption" color="text.secondary">
            {rows.length} row{rows.length !== 1 ? 's' : ''}
          </Typography>
          {data !== null && rows.length > 0 && (
            <IconButton
              onClick={handleExport}
              size="small"
              sx={{ color: GOLD }}
              title="Export as CSV"
            >
              <DownloadIcon fontSize="small" />
            </IconButton>
          )}
        </Box>
      </Box>
    </ResizableDrawer>
  );
}

export type { DataPreviewProps };
