'use client';

import { useState } from 'react';
import { Box, Button, TextField, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import SearchIcon from '@mui/icons-material/Search';
import { DataProductTable } from '@/components/dashboard/DataProductTable';
import { CreateDataProductModal } from '@/components/dashboard/CreateDataProductModal';
import { useDataProducts } from '@/hooks/useDataProducts';

export default function DataProductsPage(): React.ReactNode {
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(20);
  const [search, setSearch] = useState('');
  const [modalOpen, setModalOpen] = useState(false);

  const { data, isLoading } = useDataProducts(page + 1, rowsPerPage);

  const products = data?.data ?? [];
  const totalCount = data?.meta.total ?? 0;

  const filteredProducts = search.trim().length > 0
    ? products.filter((p) =>
        p.name.toLowerCase().includes(search.toLowerCase()),
      )
    : products;

  function handlePageChange(newPage: number): void {
    setPage(newPage);
  }

  function handleRowsPerPageChange(newRowsPerPage: number): void {
    setRowsPerPage(newRowsPerPage);
    setPage(0);
  }

  return (
    <Box>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          mb: 3,
        }}
      >
        <Typography variant="h4" component="h1" fontWeight={700}>
          Manage Data Products
        </Typography>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <TextField
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search data products..."
            size="small"
            slotProps={{
              input: {
                startAdornment: (
                  <SearchIcon
                    sx={{ color: 'text.secondary', mr: 1, fontSize: 20 }}
                  />
                ),
              },
            }}
            sx={{ width: 280 }}
          />
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setModalOpen(true)}
          >
            Create Data Product
          </Button>
        </Box>
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 10 }}>
          <Typography color="text.secondary">Loading data products...</Typography>
        </Box>
      ) : (
        <DataProductTable
          products={filteredProducts}
          page={page}
          rowsPerPage={rowsPerPage}
          totalCount={search.trim().length > 0 ? filteredProducts.length : totalCount}
          onPageChange={handlePageChange}
          onRowsPerPageChange={handleRowsPerPageChange}
        />
      )}

      <CreateDataProductModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
      />
    </Box>
  );
}
