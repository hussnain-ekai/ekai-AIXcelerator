'use client';

import { useRouter } from 'next/navigation';
import {
  Box,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  Typography,
} from '@mui/material';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { HealthDot } from '@/components/dashboard/HealthDot';
import { CollaboratorAvatars } from '@/components/dashboard/CollaboratorAvatars';
import { formatRelativeTime } from '@/lib/utils';
import type { DataProduct } from '@/hooks/useDataProducts';

interface DataProductTableProps {
  products: DataProduct[];
  page: number;
  rowsPerPage: number;
  totalCount: number;
  onPageChange: (newPage: number) => void;
  onRowsPerPageChange: (newRowsPerPage: number) => void;
}

const COLUMN_HEADERS = [
  'Name',
  'Database',
  'Status',
  'Last Updated',
  'Owner',
  'Collaborators',
  'Health',
  'Actions',
] as const;

export function DataProductTable({
  products,
  page,
  rowsPerPage,
  totalCount,
  onPageChange,
  onRowsPerPageChange,
}: DataProductTableProps): React.ReactNode {
  const router = useRouter();

  function handleRowClick(id: string): void {
    router.push(`/data-products/${id}`);
  }

  function handlePageChange(_event: unknown, newPage: number): void {
    onPageChange(newPage);
  }

  function handleRowsPerPageChange(
    event: React.ChangeEvent<HTMLInputElement>,
  ): void {
    onRowsPerPageChange(parseInt(event.target.value, 10));
  }

  if (products.length === 0) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          py: 10,
        }}
      >
        <Typography variant="h6" color="text.secondary" gutterBottom>
          No data products yet
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Create your first data product to get started with semantic modeling.
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      <TableContainer>
        <Table>
          <TableHead>
            <TableRow>
              {COLUMN_HEADERS.map((header) => (
                <TableCell
                  key={header}
                  sx={{ fontWeight: 600, color: 'text.secondary' }}
                >
                  {header}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {products.map((product) => (
              <TableRow
                key={product.id}
                hover
                onClick={() => handleRowClick(product.id)}
                sx={{ cursor: 'pointer' }}
              >
                <TableCell>
                  <Typography variant="body2" fontWeight={600}>
                    {product.name}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Typography
                    variant="body2"
                    sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}
                  >
                    {product.database_reference}
                  </Typography>
                </TableCell>
                <TableCell>
                  <StatusBadge status={product.status} />
                </TableCell>
                <TableCell>
                  <Typography variant="body2" color="text.secondary">
                    {formatRelativeTime(product.updated_at)}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Typography variant="body2">{product.owner}</Typography>
                </TableCell>
                <TableCell>
                  <CollaboratorAvatars
                    collaborators={
                      product.share_count > 0
                        ? Array.from(
                            { length: product.share_count },
                            (_, i) => `Collaborator ${i + 1}`,
                          )
                        : []
                    }
                  />
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center' }}>
                    <HealthDot score={product.health_score} />
                  </Box>
                </TableCell>
                <TableCell>
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                    }}
                  >
                    <MoreVertIcon fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <TablePagination
        component="div"
        count={totalCount}
        page={page}
        rowsPerPage={rowsPerPage}
        onPageChange={handlePageChange}
        onRowsPerPageChange={handleRowsPerPageChange}
        rowsPerPageOptions={[10, 20, 50]}
      />
    </Box>
  );
}
