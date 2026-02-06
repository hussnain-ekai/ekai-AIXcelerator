'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Drawer,
  FormControlLabel,
  IconButton,
  List,
  ListItem,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import TableChartOutlinedIcon from '@mui/icons-material/TableChartOutlined';
import SaveIcon from '@mui/icons-material/Save';
import { useQueries } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useUpdateDataProduct } from '@/hooks/useDataProducts';
import type { DataProduct } from '@/hooks/useDataProducts';
import type { TableSummary } from '@/hooks/useDatabases';

interface ManageTablesPanelProps {
  open: boolean;
  onClose: () => void;
  dataProduct: DataProduct;
}

const GOLD = '#D4A843';

interface TablesResponse {
  tables: TableSummary[];
}

function formatRowCount(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M rows`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K rows`;
  return `${count} rows`;
}

export function ManageTablesPanel({
  open,
  onClose,
  dataProduct,
}: ManageTablesPanelProps): React.ReactNode {
  const [selectedTables, setSelectedTables] = useState<string[]>(
    dataProduct.tables ?? [],
  );
  const updateMutation = useUpdateDataProduct(dataProduct.id);

  const dbSchemas = dataProduct.schemas ?? [];
  const dbName = dataProduct.database_reference;

  // Fetch tables for each schema
  const tableQueries = useQueries({
    queries: dbSchemas.map((schemaName) => ({
      queryKey: ['tables', dbName, schemaName],
      queryFn: () =>
        api.get<TablesResponse>(
          `/databases/${dbName}/schemas/${schemaName}/tables`,
        ),
      enabled: dbName.length > 0 && schemaName.length > 0,
    })),
  });

  const isLoading = tableQueries.some((q) => q.isLoading);

  const allTables: TableSummary[] = useMemo(() => {
    const tables: TableSummary[] = [];
    for (const query of tableQueries) {
      if (query.data?.tables) {
        tables.push(...query.data.tables);
      }
    }
    return tables;
  }, [tableQueries]);

  // Sync selected tables when dataProduct changes
  useEffect(() => {
    setSelectedTables(dataProduct.tables ?? []);
  }, [dataProduct.tables]);

  const allSelected =
    allTables.length > 0 && selectedTables.length === allTables.length;
  const hasChanges =
    JSON.stringify([...selectedTables].sort()) !==
    JSON.stringify([...(dataProduct.tables ?? [])].sort());

  function handleToggle(fqn: string): void {
    setSelectedTables((prev) =>
      prev.includes(fqn) ? prev.filter((t) => t !== fqn) : [...prev, fqn],
    );
  }

  function handleSelectAll(): void {
    if (allSelected) {
      setSelectedTables([]);
    } else {
      setSelectedTables(allTables.map((t) => t.fqn));
    }
  }

  async function handleSave(): Promise<void> {
    await updateMutation.mutateAsync({ tables: selectedTables });
    onClose();
  }

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{ paper: { sx: { width: 420 } } }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 2,
          py: 1.5,
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Box>
          <Typography variant="h6" fontWeight={700}>
            Manage Tables
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {dataProduct.database_reference} &middot;{' '}
            {dbSchemas.join(', ')}
          </Typography>
        </Box>
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </Box>

      <Box sx={{ flex: 1, overflow: 'auto', p: 2 }}>
        {isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress sx={{ color: GOLD }} />
          </Box>
        ) : (
          <>
            {/* Selected tables as chips */}
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" gutterBottom>
                Tables ({selectedTables.length})
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                {selectedTables.map((fqn) => {
                  const parts = fqn.split('.');
                  const label = `${parts[1]}.${parts[2]}`;
                  return (
                    <Chip
                      key={fqn}
                      label={label}
                      size="small"
                      variant="outlined"
                      onDelete={() => handleToggle(fqn)}
                      sx={{
                        borderColor: GOLD,
                        color: GOLD,
                        '& .MuiChip-deleteIcon': {
                          color: GOLD,
                          '&:hover': { color: '#b8912e' },
                        },
                      }}
                    />
                  );
                })}
              </Box>
            </Box>

            {/* Table checklist */}
            <List
              dense
              sx={{
                border: 1,
                borderColor: 'divider',
                borderRadius: 1,
                maxHeight: 400,
                overflow: 'auto',
              }}
            >
              <ListItem
                sx={{
                  bgcolor: 'action.hover',
                  borderBottom: 1,
                  borderColor: 'divider',
                }}
              >
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={allSelected}
                      indeterminate={
                        selectedTables.length > 0 && !allSelected
                      }
                      onChange={handleSelectAll}
                      sx={{
                        '&.Mui-checked': { color: GOLD },
                        '&.MuiCheckbox-indeterminate': { color: GOLD },
                      }}
                    />
                  }
                  label={
                    <Typography variant="body2" fontWeight={600}>
                      Select All
                    </Typography>
                  }
                  sx={{ width: '100%', m: 0 }}
                />
              </ListItem>

              {allTables.map((table) => (
                <ListItem key={table.fqn} disablePadding sx={{ px: 1 }}>
                  <FormControlLabel
                    control={
                      <Checkbox
                        checked={selectedTables.includes(table.fqn)}
                        onChange={() => handleToggle(table.fqn)}
                        sx={{ '&.Mui-checked': { color: GOLD } }}
                      />
                    }
                    label={
                      <Box
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 1,
                        }}
                      >
                        <TableChartOutlinedIcon
                          sx={{ fontSize: 18, color: GOLD }}
                        />
                        <Box>
                          <Typography variant="body2">
                            {table.schema}.{table.name}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                          >
                            {formatRowCount(table.row_count)}
                          </Typography>
                        </Box>
                      </Box>
                    }
                    sx={{ width: '100%', m: 0 }}
                  />
                </ListItem>
              ))}
            </List>
          </>
        )}
      </Box>

      {/* Footer with Update Tables button */}
      <Box
        sx={{
          px: 2,
          py: 1.5,
          borderTop: 1,
          borderColor: 'divider',
          display: 'flex',
          justifyContent: 'flex-end',
        }}
      >
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={() => void handleSave()}
          disabled={
            !hasChanges ||
            selectedTables.length === 0 ||
            updateMutation.isPending
          }
        >
          {updateMutation.isPending ? 'Updating...' : 'Update Tables'}
        </Button>
      </Box>
    </Drawer>
  );
}
