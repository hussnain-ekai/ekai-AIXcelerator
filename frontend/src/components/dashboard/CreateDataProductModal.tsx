'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Select,
  TextField,
  Typography,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material';
import TableChartOutlinedIcon from '@mui/icons-material/TableChartOutlined';
import { useDatabases, useSchemas, useTables } from '@/hooks/useDatabases';
import type { TableSummary } from '@/hooks/useDatabases';
import { useCreateDataProduct } from '@/hooks/useDataProducts';
import { useQueries } from '@tanstack/react-query';
import { api } from '@/lib/api';

interface CreateDataProductModalProps {
  open: boolean;
  onClose: () => void;
}

type Step = 0 | 1 | 2;

const GOLD = '#D4A843';
const GRAY = '#616161';
const STEPS: Step[] = [0, 1, 2];

function StepIndicator({ currentStep }: { currentStep: Step }): React.ReactNode {
  return (
    <Box sx={{ display: 'flex', gap: 1, justifyContent: 'center', mb: 2 }}>
      {STEPS.map((s) => (
        <Box
          key={s}
          sx={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            bgcolor: currentStep === s ? GOLD : currentStep > s ? GOLD : GRAY,
          }}
        />
      ))}
    </Box>
  );
}

function formatRowCount(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M rows`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K rows`;
  return `${count} rows`;
}

interface TablesResponse {
  tables: TableSummary[];
}

export function CreateDataProductModal({
  open,
  onClose,
}: CreateDataProductModalProps): React.ReactNode {
  const router = useRouter();
  const [step, setStep] = useState<Step>(0);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [selectedDatabase, setSelectedDatabase] = useState('');
  const [selectedSchemas, setSelectedSchemas] = useState<string[]>([]);
  const [selectedTables, setSelectedTables] = useState<string[]>([]);

  const { data: databasesData } = useDatabases();
  const { data: schemasData } = useSchemas(
    selectedDatabase.length > 0 ? selectedDatabase : null,
  );
  const createMutation = useCreateDataProduct();

  const databases = databasesData?.databases ?? [];
  const schemas = schemasData?.schemas ?? [];

  // Fetch tables for each selected schema in parallel
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

  // Combine all tables from all selected schemas
  const allTables: TableSummary[] = useMemo(() => {
    const tables: TableSummary[] = [];
    for (const query of tableQueries) {
      if (query.data?.tables) {
        tables.push(...query.data.tables);
      }
    }
    return tables;
  }, [tableQueries]);

  // Pre-select non-PUBLIC schemas when schemas load for a new database selection
  useEffect(() => {
    if (schemas.length === 0) return;
    const nonPublic = schemas
      .filter((s) => s.name !== 'PUBLIC')
      .map((s) => s.name);
    if (nonPublic.length > 0) {
      setSelectedSchemas(nonPublic);
    }
  }, [schemas]);

  // Auto-select all tables when they load (moving to step 2)
  useEffect(() => {
    if (allTables.length > 0 && step === 2) {
      setSelectedTables((prev) => {
        // Only auto-select if user hasn't manually changed anything yet
        if (prev.length === 0) {
          return allTables.map((t) => t.fqn);
        }
        return prev;
      });
    }
  }, [allTables, step]);

  function handleDatabaseChange(event: SelectChangeEvent): void {
    setSelectedDatabase(event.target.value);
    setSelectedSchemas([]);
    setSelectedTables([]);
  }

  function handleSchemaToggle(schemaName: string): void {
    setSelectedSchemas((prev) => {
      if (prev.includes(schemaName)) {
        return prev.filter((s) => s !== schemaName);
      }
      return [...prev, schemaName];
    });
    // Clear table selection when schemas change — they'll be re-populated in step 3
    setSelectedTables([]);
  }

  function handleTableToggle(fqn: string): void {
    setSelectedTables((prev) => {
      if (prev.includes(fqn)) {
        return prev.filter((t) => t !== fqn);
      }
      return [...prev, fqn];
    });
  }

  function handleSelectAllTables(): void {
    if (selectedTables.length === allTables.length) {
      setSelectedTables([]);
    } else {
      setSelectedTables(allTables.map((t) => t.fqn));
    }
  }

  function handleClose(): void {
    setStep(0);
    setName('');
    setDescription('');
    setSelectedDatabase('');
    setSelectedSchemas([]);
    setSelectedTables([]);
    onClose();
  }

  function handleNextToStep1(): void {
    if (name.trim().length > 0) {
      setStep(1);
    }
  }

  function handleNextToStep2(): void {
    if (selectedDatabase.length > 0 && selectedSchemas.length > 0) {
      // Auto-select all tables when entering step 2
      setSelectedTables(allTables.map((t) => t.fqn));
      setStep(2);
    }
  }

  function handleBackToStep0(): void {
    setStep(0);
  }

  function handleBackToStep1(): void {
    setStep(1);
  }

  async function handleCreate(): Promise<void> {
    const result = await createMutation.mutateAsync({
      name: name.trim(),
      description: description.trim() || undefined,
      database_reference: selectedDatabase,
      schemas: selectedSchemas,
      tables: selectedTables,
    });
    handleClose();
    router.push(`/data-products/${result.id}`);
  }

  const isStep1Valid = name.trim().length > 0;
  const isStep2Valid =
    selectedDatabase.length > 0 && selectedSchemas.length > 0;
  const isStep3Valid = selectedTables.length > 0;

  const stepTitles: Record<Step, string> = {
    0: 'Create Data Product',
    1: 'Select Data Source',
    2: 'Select Tables',
  };

  const stepSubtitles: Record<Step, string> = {
    0: 'Start building your semantic model',
    1: 'Choose a database and schemas',
    2: 'Please select entities to link with Data Connection.',
  };

  const allSelected = allTables.length > 0 && selectedTables.length === allTables.length;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: { borderRadius: 2 } } }}
    >
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="h6" fontWeight={700}>
          {stepTitles[step]}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {stepSubtitles[step]}
        </Typography>
        <StepIndicator currentStep={step} />
      </DialogTitle>

      <DialogContent>
        {/* Step 0: Name & Description */}
        {step === 0 && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            <TextField
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              fullWidth
              autoFocus
              placeholder="e.g., Sales Analytics Model"
            />
            <TextField
              label="Description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              fullWidth
              multiline
              rows={3}
              placeholder="Describe the purpose of this data product..."
            />
          </Box>
        )}

        {/* Step 1: Database & Schemas */}
        {step === 1 && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            <FormControl fullWidth>
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

            {schemas.length > 0 && (
              <Box>
                <Typography variant="subtitle2" gutterBottom>
                  Select Schemas
                </Typography>
                <List
                  dense
                  sx={{
                    maxHeight: 240,
                    overflow: 'auto',
                    border: 1,
                    borderColor: 'divider',
                    borderRadius: 1,
                  }}
                >
                  {schemas.map((schema) => (
                    <ListItem key={schema.name} disablePadding sx={{ px: 1 }}>
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={selectedSchemas.includes(schema.name)}
                            onChange={() => handleSchemaToggle(schema.name)}
                            sx={{
                              '&.Mui-checked': { color: GOLD },
                            }}
                          />
                        }
                        label={
                          <Box>
                            <Typography variant="body2">
                              {schema.name}
                            </Typography>
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
          </Box>
        )}

        {/* Step 2: Select Tables */}
        {step === 2 && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            {isLoadingTables ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                <CircularProgress sx={{ color: GOLD }} />
              </Box>
            ) : (
              <>
                {/* Select All header */}
                <List
                  dense
                  sx={{
                    maxHeight: 320,
                    overflow: 'auto',
                    border: 1,
                    borderColor: 'divider',
                    borderRadius: 1,
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

                  {allTables.map((table) => (
                    <ListItem
                      key={table.fqn}
                      disablePadding
                      sx={{ px: 1 }}
                    >
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={selectedTables.includes(table.fqn)}
                            onChange={() => handleTableToggle(table.fqn)}
                            sx={{
                              '&.Mui-checked': { color: GOLD },
                            }}
                          />
                        }
                        label={
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <TableChartOutlinedIcon
                              sx={{ fontSize: 18, color: GOLD }}
                            />
                            <Box>
                              <Typography variant="body2">
                                {table.schema}.{table.name}
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
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

                {/* Selected tables as chips */}
                {selectedTables.length > 0 && (
                  <Box>
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
                            onDelete={() => handleTableToggle(fqn)}
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
                )}
              </>
            )}
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {step === 0 && (
          <>
            <Button onClick={handleClose} color="inherit">
              Cancel
            </Button>
            <Button
              onClick={handleNextToStep1}
              variant="contained"
              disabled={!isStep1Valid}
            >
              Next
            </Button>
          </>
        )}
        {step === 1 && (
          <>
            <Button onClick={handleBackToStep0} color="inherit">
              Back
            </Button>
            <Button
              onClick={handleNextToStep2}
              variant="contained"
              disabled={!isStep2Valid}
            >
              Next
            </Button>
          </>
        )}
        {step === 2 && (
          <>
            <Button onClick={handleBackToStep1} color="inherit">
              Back
            </Button>
            <Button
              onClick={() => void handleCreate()}
              variant="contained"
              disabled={!isStep3Valid || createMutation.isPending}
            >
              {createMutation.isPending ? 'Creating...' : 'Create'}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
}
