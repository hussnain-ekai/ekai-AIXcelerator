'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  List,
  ListItem,
  MenuItem,
  Select,
  Typography,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import TableChartOutlinedIcon from '@mui/icons-material/TableChartOutlined';
import SaveIcon from '@mui/icons-material/Save';
import { useQueries } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useDatabases, useSchemas } from '@/hooks/useDatabases';
import { useUpdateDataProduct } from '@/hooks/useDataProducts';
import type { DataProduct } from '@/hooks/useDataProducts';
import type { TableSummary } from '@/hooks/useDatabases';

interface DataSourceSettingsPanelProps {
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

export function DataSourceSettingsPanel({
  open,
  onClose,
  dataProduct,
}: DataSourceSettingsPanelProps): React.ReactNode {
  const [selectedDatabase, setSelectedDatabase] = useState(
    dataProduct.database_reference,
  );
  const [selectedSchemas, setSelectedSchemas] = useState<string[]>(
    dataProduct.schemas ?? [],
  );
  const [selectedTables, setSelectedTables] = useState<string[]>(
    dataProduct.tables ?? [],
  );

  // Refs to track initial auto-selection (prevent infinite loops)
  const initialDatabaseRef = useRef(dataProduct.database_reference);
  const hasAutoSelectedSchemasRef = useRef(false);
  const hasAutoSelectedTablesRef = useRef(false);

  const updateMutation = useUpdateDataProduct(dataProduct.id);
  const { data: databasesData } = useDatabases();
  const { data: schemasData } = useSchemas(
    selectedDatabase.length > 0 ? selectedDatabase : null,
  );

  const databases = databasesData?.databases ?? [];
  const schemas = schemasData?.schemas ?? [];

  // Fetch tables for each selected schema
  const tableQueries = useQueries({
    queries: selectedSchemas.map((schemaName) => ({
      queryKey: ['tables', selectedDatabase, schemaName],
      queryFn: () =>
        api.get<TablesResponse>(
          `/databases/${selectedDatabase}/schemas/${schemaName}/tables`,
        ),
      enabled: selectedDatabase.length > 0 && schemaName.length > 0,
    })),
  });

  const isLoadingTables = tableQueries.some((q) => q.isLoading);

  const allTables: TableSummary[] = useMemo(() => {
    const tables: TableSummary[] = [];
    for (const query of tableQueries) {
      if (query.data?.tables) {
        tables.push(...query.data.tables);
      }
    }
    return tables;
  }, [tableQueries]);

  // Sync state when dataProduct changes
  useEffect(() => {
    setSelectedDatabase(dataProduct.database_reference);
    setSelectedSchemas(dataProduct.schemas ?? []);
    setSelectedTables(dataProduct.tables ?? []);
    // Reset auto-selection flags when data product changes
    initialDatabaseRef.current = dataProduct.database_reference;
    hasAutoSelectedSchemasRef.current = false;
    hasAutoSelectedTablesRef.current = false;
  }, [dataProduct]);

  // Pre-select non-PUBLIC schemas when database changes
  useEffect(() => {
    if (schemas.length === 0) return;
    // Only auto-select once when database changes from the original
    if (
      selectedDatabase !== initialDatabaseRef.current &&
      !hasAutoSelectedSchemasRef.current
    ) {
      const nonPublic = schemas
        .filter((s) => s.name !== 'PUBLIC')
        .map((s) => s.name);
      setSelectedSchemas(nonPublic);
      setSelectedTables([]); // Clear tables when database changes
      hasAutoSelectedSchemasRef.current = true;
      hasAutoSelectedTablesRef.current = false; // Reset tables flag
    }
  }, [schemas, selectedDatabase]);

  // Auto-select all tables when schemas change (only once per schema change)
  useEffect(() => {
    if (
      allTables.length > 0 &&
      !isLoadingTables &&
      !hasAutoSelectedTablesRef.current
    ) {
      // Check if schemas have changed from original
      const currentSchemaSet = new Set(selectedSchemas);
      const originalSchemaSet = new Set(dataProduct.schemas ?? []);
      const schemasChanged =
        currentSchemaSet.size !== originalSchemaSet.size ||
        [...currentSchemaSet].some((s) => !originalSchemaSet.has(s));

      if (schemasChanged) {
        setSelectedTables(allTables.map((t) => t.fqn));
        hasAutoSelectedTablesRef.current = true;
      }
    }
  }, [allTables, isLoadingTables, selectedSchemas, dataProduct.schemas]);

  const allTablesSelected =
    allTables.length > 0 && selectedTables.length === allTables.length;

  const hasChanges =
    selectedDatabase !== dataProduct.database_reference ||
    JSON.stringify([...selectedSchemas].sort()) !==
      JSON.stringify([...(dataProduct.schemas ?? [])].sort()) ||
    JSON.stringify([...selectedTables].sort()) !==
      JSON.stringify([...(dataProduct.tables ?? [])].sort());

  function handleDatabaseChange(event: SelectChangeEvent): void {
    setSelectedDatabase(event.target.value);
    setSelectedSchemas([]);
    setSelectedTables([]);
    // Reset auto-selection flags when user manually changes database
    hasAutoSelectedSchemasRef.current = false;
    hasAutoSelectedTablesRef.current = false;
  }

  function handleSchemaToggle(schemaName: string): void {
    setSelectedSchemas((prev) => {
      if (prev.includes(schemaName)) {
        // Remove schema and all its tables
        const newSchemas = prev.filter((s) => s !== schemaName);
        // Filter out tables from removed schema
        setSelectedTables((tables) =>
          tables.filter((fqn) => !fqn.startsWith(`${selectedDatabase}.${schemaName}.`)),
        );
        return newSchemas;
      }
      // Adding a schema - reset tables flag so new tables will be auto-selected
      hasAutoSelectedTablesRef.current = false;
      return [...prev, schemaName];
    });
  }

  function handleTableToggle(fqn: string): void {
    setSelectedTables((prev) =>
      prev.includes(fqn) ? prev.filter((t) => t !== fqn) : [...prev, fqn],
    );
  }

  function handleSelectAllTables(): void {
    if (allTablesSelected) {
      setSelectedTables([]);
    } else {
      setSelectedTables(allTables.map((t) => t.fqn));
    }
  }

  async function handleSave(): Promise<void> {
    await updateMutation.mutateAsync({
      database_reference: selectedDatabase,
      schemas: selectedSchemas,
      tables: selectedTables,
    });
    onClose();
  }

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{ paper: { sx: { width: 480 } } }}
    >
      {/* Header */}
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
            Data Source Settings
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Configure database, schemas, and tables
          </Typography>
        </Box>
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </Box>

      {/* Content */}
      <Box sx={{ flex: 1, overflow: 'auto', p: 2 }}>
        {/* Database Selection */}
        <Box sx={{ mb: 3 }}>
          <FormControl fullWidth size="small">
            <InputLabel>Database</InputLabel>
            <Select
              value={selectedDatabase}
              onChange={handleDatabaseChange}
              label="Database"
            >
              {databases.map((db) => (
                <MenuItem key={db.name} value={db.name}>
                  {db.name}
                  {db.schemas_count > 0 && (
                    <Typography
                      component="span"
                      variant="caption"
                      color="text.secondary"
                      sx={{ ml: 1 }}
                    >
                      ({db.schemas_count} schemas)
                    </Typography>
                  )}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>

        <Divider sx={{ mb: 2 }} />

        {/* Schema Selection */}
        {schemas.length > 0 && (
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" gutterBottom>
              Schemas ({selectedSchemas.length})
            </Typography>
            <List
              dense
              sx={{
                border: 1,
                borderColor: 'divider',
                borderRadius: 1,
                maxHeight: 200,
                overflow: 'auto',
              }}
            >
              {schemas.map((schema) => (
                <ListItem key={schema.name} disablePadding sx={{ px: 1 }}>
                  <FormControlLabel
                    control={
                      <Checkbox
                        checked={selectedSchemas.includes(schema.name)}
                        onChange={() => handleSchemaToggle(schema.name)}
                        sx={{ '&.Mui-checked': { color: GOLD } }}
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="body2">{schema.name}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {schema.tables_count} tables
                        </Typography>
                      </Box>
                    }
                    sx={{ width: '100%', m: 0 }}
                  />
                </ListItem>
              ))}
            </List>
          </Box>
        )}

        <Divider sx={{ mb: 2 }} />

        {/* Table Selection */}
        {selectedSchemas.length > 0 && (
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Tables ({selectedTables.length})
            </Typography>

            {isLoadingTables ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                <CircularProgress sx={{ color: GOLD }} size={24} />
              </Box>
            ) : (
              <List
                dense
                sx={{
                  border: 1,
                  borderColor: 'divider',
                  borderRadius: 1,
                  maxHeight: 320,
                  overflow: 'auto',
                }}
              >
                {/* Select All */}
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
                        checked={allTablesSelected}
                        indeterminate={
                          selectedTables.length > 0 && !allTablesSelected
                        }
                        onChange={handleSelectAllTables}
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

                {/* Table list */}
                {allTables.map((table) => (
                  <ListItem key={table.fqn} disablePadding sx={{ px: 1 }}>
                    <FormControlLabel
                      control={
                        <Checkbox
                          checked={selectedTables.includes(table.fqn)}
                          onChange={() => handleTableToggle(table.fqn)}
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
            )}
          </Box>
        )}
      </Box>

      {/* Footer */}
      <Box
        sx={{
          px: 2,
          py: 1.5,
          borderTop: 1,
          borderColor: 'divider',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <Typography variant="caption" color="text.secondary">
          {hasChanges ? 'Unsaved changes' : 'No changes'}
        </Typography>
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={() => void handleSave()}
          disabled={
            !hasChanges ||
            selectedSchemas.length === 0 ||
            selectedTables.length === 0 ||
            updateMutation.isPending
          }
        >
          {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
        </Button>
      </Box>
    </Drawer>
  );
}
