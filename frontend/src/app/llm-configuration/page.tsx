'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Collapse,
  MenuItem,
  Radio,
  RadioGroup,
  TextField,
  Typography,
} from '@mui/material';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import {
  useLlmConfig,
  useLlmSave,
  useLlmTest,
} from '@/hooks/useLlmConfig';
import type { LLMConfigBody, LLMDefaults, LLMProvider, LLMTestResponse } from '@/hooks/useLlmConfig';

const GOLD = '#D4A843';

// ---------------------------------------------------------------------------
// Provider definitions
// ---------------------------------------------------------------------------

interface ProviderDef {
  id: LLMProvider;
  title: string;
  description: string;
  badges: string[];
  models: string[];
  fields: FieldDef[];
}

interface FieldDef {
  key: string;
  label: string;
  type?: 'password' | 'text' | 'select' | 'textarea';
  options?: string[];
  defaultsKey?: string;
  rows?: number;
  required?: boolean;
}

const PROVIDERS: ProviderDef[] = [
  {
    id: 'snowflake-cortex',
    title: 'Snowflake Cortex',
    description:
      'Snowflake-managed AI models running within your account. No data leaves Snowflake.',
    badges: ['RECOMMENDED'],
    models: [
      'claude-sonnet-4-5', 'claude-opus-4-5', 'claude-haiku-4-5',
      'llama4-maverick', 'llama4-scout', 'llama3.3-70b',
      'mistral-large2', 'openai-gpt-4.1', 'openai-o4-mini',
    ],
    fields: [
      {
        key: 'cortex_model',
        label: 'Model',
        type: 'select',
        options: [
          'claude-sonnet-4-5', 'claude-opus-4-5', 'claude-haiku-4-5',
          'llama4-maverick', 'llama4-scout', 'llama3.3-70b',
          'mistral-large2', 'openai-gpt-4.1', 'openai-o4-mini',
        ],
      },
    ],
  },
  {
    id: 'vertex-ai',
    title: 'Google Vertex AI',
    description:
      'GCP Vertex AI with Gemini and Claude models. Requires service account JSON credentials.',
    badges: [],
    models: [
      // Gemini 3 (Preview - Latest 2026)
      'gemini-3-pro-preview', 'gemini-3-flash-preview',
      // Gemini 2.5 (GA)
      'gemini-2-5-pro', 'gemini-2-5-flash', 'gemini-2-5-flash-lite',
      // Gemini 2.0
      'gemini-2-0-flash', 'gemini-2-0-flash-lite',
      // Claude models on Vertex
      'claude-opus-4-6@default',
      'claude-sonnet-4-5@20250929', 'claude-opus-4-5@20251101',
      'claude-3-7-sonnet@20250219', 'claude-3-5-haiku@20241022',
    ],
    fields: [
      {
        key: 'vertex_credentials_json',
        label: 'Service Account Key *',
        type: 'textarea',
        rows: 6,
        required: true,
      },
      { key: 'vertex_project', label: 'Project ID *', required: true },
      { key: 'vertex_location', label: 'Region *', required: true },
      {
        key: 'vertex_model',
        label: 'Model',
        type: 'select',
        options: [
          // Gemini 3 (Preview - Latest 2026)
          'gemini-3-pro-preview', 'gemini-3-flash-preview',
          // Gemini 2.5 (GA)
          'gemini-2-5-pro', 'gemini-2-5-flash', 'gemini-2-5-flash-lite',
          // Gemini 2.0
          'gemini-2-0-flash', 'gemini-2-0-flash-lite',
          // Claude on Vertex
          'claude-opus-4-6@default',
          'claude-sonnet-4-5@20250929', 'claude-opus-4-5@20251101',
          'claude-3-5-haiku@20241022',
        ],
      },
    ],
  },
  {
    id: 'azure-openai',
    title: 'Azure OpenAI',
    description:
      'Azure-managed OpenAI models with your organization endpoint and API key.',
    badges: [],
    models: ['gpt-4.1-mini', 'gpt-4.1', 'gpt-5.2', 'o3', 'o4-mini'],
    fields: [
      {
        key: 'azure_openai_endpoint',
        label: 'Endpoint URL',
        defaultsKey: 'azure_openai_endpoint',
      },
      { key: 'azure_openai_api_key', label: 'API Key', type: 'password' },
      {
        key: 'azure_openai_deployment',
        label: 'Deployment Name',
        defaultsKey: 'azure_openai_deployment',
      },
      {
        key: 'azure_openai_api_version',
        label: 'API Version',
        defaultsKey: 'azure_openai_api_version',
      },
    ],
  },
  {
    id: 'anthropic',
    title: 'Anthropic',
    description: 'Direct API access to Claude models.',
    badges: [],
    models: [
      'claude-opus-4-6-20260210', 'claude-sonnet-4-5-20250929',
      'claude-opus-4-5-20251101', 'claude-haiku-4-5-20251001',
    ],
    fields: [
      { key: 'anthropic_api_key', label: 'API Key', type: 'password' },
      {
        key: 'anthropic_model',
        label: 'Model',
        type: 'select',
        options: [
          'claude-opus-4-6-20260210', 'claude-sonnet-4-5-20250929',
          'claude-opus-4-5-20251101', 'claude-haiku-4-5-20251001',
        ],
      },
    ],
  },
  {
    id: 'openai',
    title: 'OpenAI',
    description: 'Direct API access to OpenAI models.',
    badges: [],
    models: ['gpt-5.2', 'gpt-4.1', 'gpt-4.1-mini', 'o3', 'o4-mini'],
    fields: [
      { key: 'openai_api_key', label: 'API Key', type: 'password' },
      {
        key: 'openai_model',
        label: 'Model',
        type: 'select',
        options: ['gpt-5.2', 'gpt-4.1', 'gpt-4.1-mini', 'o3', 'o4-mini'],
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// Provider Card
// ---------------------------------------------------------------------------

function ProviderCard({
  provider,
  selected,
  isActive,
  fieldValues,
  defaults,
  onSelect,
  onFieldChange,
  onFileUpload,
}: {
  provider: ProviderDef;
  selected: boolean;
  isActive: boolean;
  fieldValues: Record<string, string>;
  defaults: LLMDefaults | null;
  onSelect: () => void;
  onFieldChange: (key: string, value: string) => void;
  onFileUpload?: (key: string, content: string, projectId?: string) => void;
}): React.ReactNode {
  return (
    <Card
      variant="outlined"
      onClick={onSelect}
      sx={{
        cursor: 'pointer',
        borderColor: selected ? GOLD : 'divider',
        borderWidth: selected ? 2 : 1,
        transition: 'border-color 0.2s',
        '&:hover': {
          borderColor: selected ? GOLD : 'text.secondary',
        },
      }}
    >
      <CardContent sx={{ display: 'flex', gap: 2 }}>
        <Radio
          checked={selected}
          onChange={onSelect}
          sx={{
            color: 'text.secondary',
            '&.Mui-checked': { color: GOLD },
            mt: -0.5,
          }}
        />
        <Box sx={{ flex: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Typography variant="subtitle1" fontWeight={600}>
              {provider.title}
            </Typography>
            {provider.badges.map((badge) => (
              <Chip
                key={badge}
                label={badge}
                size="small"
                sx={{
                  bgcolor: GOLD,
                  color: '#1A1A1E',
                  fontWeight: 700,
                  fontSize: '0.65rem',
                  height: 22,
                }}
              />
            ))}
            {isActive && (
              <Chip
                icon={<CheckCircleOutlineIcon sx={{ fontSize: 14 }} />}
                label="ACTIVE"
                size="small"
                color="success"
                variant="outlined"
                sx={{ height: 22, fontSize: '0.65rem', fontWeight: 700 }}
              />
            )}
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {provider.description}
          </Typography>

          {/* Expandable credential form */}
          <Collapse in={selected} unmountOnExit>
            <Box
              sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, mt: 1.5 }}
              onClick={(e) => e.stopPropagation()}
            >
              {/* Vertex AI — Upload JSON Key button */}
              {provider.id === 'vertex-ai' && (
                <Box sx={{ alignSelf: 'flex-start' }}>
                  <input
                    id="vertex-credentials-upload"
                    type="file"
                    accept=".json,application/json"
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file && onFileUpload) {
                        const reader = new FileReader();
                        reader.onload = (ev) => {
                          const content = ev.target?.result;
                          if (typeof content === 'string') {
                            try {
                              const parsed = JSON.parse(content);
                              // Handle nested structure: { gcp: { credentials: {...} } }
                              let credentials = parsed;
                              let projectId = parsed.project_id as string | undefined;
                              if (parsed.gcp?.credentials) {
                                credentials = parsed.gcp.credentials;
                                projectId = credentials.project_id;
                              }
                              const credentialsJson = JSON.stringify(credentials, null, 2);
                              onFileUpload('vertex_credentials_json', credentialsJson, projectId);
                            } catch {
                              // Invalid JSON — will show in textarea for user to see
                              onFileUpload('vertex_credentials_json', content);
                            }
                          }
                        };
                        reader.readAsText(file);
                      }
                      // Reset so same file can be re-selected
                      e.target.value = '';
                    }}
                  />
                  <label htmlFor="vertex-credentials-upload">
                    <Button
                      variant="outlined"
                      size="small"
                      component="span"
                      startIcon={<UploadFileIcon />}
                      sx={{
                        borderColor: GOLD,
                        color: GOLD,
                        textTransform: 'none',
                        '&:hover': { borderColor: GOLD, bgcolor: 'rgba(212,168,67,0.08)' },
                      }}
                    >
                      Upload JSON Key
                    </Button>
                  </label>
                </Box>
              )}

              {/* Azure — API key status */}
              {provider.id === 'azure-openai' && defaults && defaults.azure_openai_key_configured && (
                <Alert severity="success" icon={<CheckCircleOutlineIcon />} sx={{ py: 0.5 }}>
                  API key configured via environment variable
                </Alert>
              )}

              {provider.fields.map((field) => {
                const value = fieldValues[field.key] ?? '';
                if (field.type === 'select') {
                  return (
                    <TextField
                      key={field.key}
                      select
                      label={field.label}
                      value={value}
                      onChange={(e) => onFieldChange(field.key, e.target.value)}
                      size="small"
                      fullWidth
                      slotProps={{
                        inputLabel: { shrink: true },
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '&.Mui-focused fieldset': { borderColor: GOLD },
                        },
                        '& .MuiInputLabel-root.Mui-focused': { color: GOLD },
                      }}
                    >
                      {(field.options ?? []).map((opt) => (
                        <MenuItem key={opt} value={opt}>
                          {opt}
                        </MenuItem>
                      ))}
                    </TextField>
                  );
                }
                if (field.type === 'textarea') {
                  return (
                    <TextField
                      key={field.key}
                      label={field.label}
                      value={value}
                      onChange={(e) => onFieldChange(field.key, e.target.value)}
                      size="small"
                      fullWidth
                      multiline
                      rows={field.rows ?? 4}
                      slotProps={{
                        inputLabel: { shrink: true },
                        input: {
                          sx: {
                            fontFamily: 'monospace',
                            fontSize: '0.85rem',
                          },
                        },
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '&.Mui-focused fieldset': { borderColor: GOLD },
                        },
                        '& .MuiInputLabel-root.Mui-focused': { color: GOLD },
                      }}
                    />
                  );
                }
                return (
                  <TextField
                    key={field.key}
                    label={field.label}
                    type={field.type === 'password' ? 'password' : 'text'}
                    value={value}
                    onChange={(e) => onFieldChange(field.key, e.target.value)}
                    size="small"
                    fullWidth
                    slotProps={{
                      inputLabel: { shrink: true },
                    }}
                    sx={{
                      '& .MuiOutlinedInput-root': {
                        '&.Mui-focused fieldset': { borderColor: GOLD },
                      },
                      '& .MuiInputLabel-root.Mui-focused': { color: GOLD },
                    }}
                  />
                );
              })}
            </Box>
          </Collapse>
        </Box>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function LlmConfigurationPage(): React.ReactNode {
  const { data: configData, isLoading } = useLlmConfig();
  const saveMutation = useLlmSave();
  const testMutation = useLlmTest();

  const [selectedProvider, setSelectedProvider] = useState<LLMProvider>('snowflake-cortex');
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<LLMTestResponse | null>(null);
  const [initialized, setInitialized] = useState(false);

  // Pre-fill from saved config or defaults when data loads
  useEffect(() => {
    if (!configData || initialized) return;

    const saved = configData.saved;
    const defaults = configData.defaults;
    const active = configData.active;

    // Set provider from saved/active
    if (saved?.provider) {
      setSelectedProvider(saved.provider);
    } else if (active?.provider) {
      setSelectedProvider(active.provider as LLMProvider);
    }

    // Merge defaults and saved values into field values
    const merged: Record<string, string> = {};
    if (defaults) {
      for (const [k, v] of Object.entries(defaults)) {
        if (v) merged[k] = v;
      }
    }
    if (saved) {
      for (const [k, v] of Object.entries(saved)) {
        if (k !== 'provider' && v) merged[k] = String(v);
      }
    }
    setFieldValues(merged);
    setInitialized(true);
  }, [configData, initialized]);

  const activeProvider = configData?.active?.provider ?? null;
  const activeModel = configData?.active?.model ?? null;

  const handleFieldChange = useCallback(
    (key: string, value: string) => {
      setFieldValues((prev) => ({ ...prev, [key]: value }));
      setTestResult(null);
    },
    [],
  );

  const handleFileUpload = useCallback(
    (key: string, content: string, projectId?: string) => {
      setFieldValues((prev) => {
        const next = { ...prev, [key]: content };
        // Auto-fill project ID from service account JSON if not already set
        if (projectId && !prev['vertex_project']) {
          next['vertex_project'] = projectId;
        }
        return next;
      });
      setTestResult(null);
    },
    [],
  );

  const buildBody = useCallback((): LLMConfigBody => {
    const body: LLMConfigBody = { provider: selectedProvider };
    const providerDef = PROVIDERS.find((p) => p.id === selectedProvider);
    if (!providerDef) return body;

    for (const field of providerDef.fields) {
      const val = fieldValues[field.key];
      if (val) {
        (body as unknown as Record<string, string>)[field.key] = val;
      }
    }

    // For Vertex AI, ensure all required fields are included
    if (selectedProvider === 'vertex-ai') {
      if (fieldValues['vertex_credentials_json']) {
        body.vertex_credentials_json = fieldValues['vertex_credentials_json'];
      }
      if (fieldValues['vertex_project']) {
        body.vertex_project = fieldValues['vertex_project'];
      }
      if (fieldValues['vertex_location']) {
        body.vertex_location = fieldValues['vertex_location'];
      }
      if (fieldValues['vertex_model']) {
        body.vertex_model = fieldValues['vertex_model'];
      }
    }

    return body;
  }, [selectedProvider, fieldValues]);

  const handleTest = useCallback(() => {
    setTestResult(null);
    testMutation.mutate(buildBody(), {
      onSuccess: (result) => setTestResult(result),
    });
  }, [testMutation, buildBody]);

  const handleSave = useCallback(() => {
    saveMutation.mutate(buildBody(), {
      onSuccess: () => {
        setTestResult(null);
      },
    });
  }, [saveMutation, buildBody]);

  // Pre-fill defaults for provider when switching
  const handleProviderSelect = useCallback(
    (providerId: LLMProvider) => {
      setSelectedProvider(providerId);
      setTestResult(null);

      // Pre-fill defaults for the new provider
      const providerDef = PROVIDERS.find((p) => p.id === providerId);
      if (providerDef) {
        setFieldValues((prev) => {
          const next = { ...prev };
          for (const field of providerDef.fields) {
            // Pre-fill from configData.defaults if available
            if (field.defaultsKey && configData?.defaults) {
              const defaultVal = configData.defaults[field.defaultsKey as keyof typeof configData.defaults];
              if (defaultVal && typeof defaultVal === 'string' && !next[field.key]) {
                next[field.key] = defaultVal;
              }
            }
            // Pre-fill select fields with first option if empty
            if (field.type === 'select' && !next[field.key] && field.options && field.options.length > 0) {
              next[field.key] = field.options[0] as string;
            }
          }
          // Vertex AI: pre-fill default region
          if (providerId === 'vertex-ai' && !next['vertex_location']) {
            next['vertex_location'] = 'us-central1';
          }
          return next;
        });
      }
    },
    [configData],
  );

  const isBusy = saveMutation.isPending || testMutation.isPending;

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress sx={{ color: GOLD }} />
      </Box>
    );
  }

  return (
    <Box sx={{ maxWidth: 700 }}>
      <Typography variant="h4" component="h1" fontWeight={700} gutterBottom>
        LLM Configuration
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Select the AI model provider for your semantic modeling workflows.
      </Typography>

      {/* Active provider banner */}
      {activeProvider && (
        <Alert
          severity="success"
          icon={<CheckCircleOutlineIcon />}
          sx={{ mb: 3 }}
        >
          <strong>Active:</strong> {activeProvider} &mdash; {activeModel}
        </Alert>
      )}

      {/* Save success */}
      {saveMutation.isSuccess && (
        <Alert severity="success" sx={{ mb: 2 }}>
          LLM configuration saved and activated successfully.
        </Alert>
      )}
      {saveMutation.isError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to save: {saveMutation.error.message}
        </Alert>
      )}

      {/* Provider cards */}
      <RadioGroup value={selectedProvider}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {PROVIDERS.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              selected={selectedProvider === provider.id}
              isActive={activeProvider === provider.id}
              fieldValues={fieldValues}
              defaults={configData?.defaults ?? null}
              onSelect={() => handleProviderSelect(provider.id)}
              onFieldChange={handleFieldChange}
              onFileUpload={handleFileUpload}
            />
          ))}
        </Box>
      </RadioGroup>

      {/* Test result */}
      {testResult && (
        <Alert
          severity={testResult.status === 'ok' ? 'success' : 'error'}
          icon={
            testResult.status === 'ok' ? (
              <CheckCircleOutlineIcon />
            ) : (
              <ErrorOutlineIcon />
            )
          }
          sx={{ mt: 2 }}
        >
          {testResult.status === 'ok' ? (
            <>
              Connection successful ({testResult.response_time_ms}ms)
              {testResult.model_response && (
                <Typography variant="caption" display="block" sx={{ mt: 0.5, opacity: 0.8 }}>
                  {testResult.model_response}
                </Typography>
              )}
            </>
          ) : (
            <>Connection failed: {testResult.error}</>
          )}
        </Alert>
      )}

      {/* Action buttons */}
      <Box sx={{ display: 'flex', gap: 2, mt: 3 }}>
        <Button
          variant="outlined"
          onClick={handleTest}
          disabled={isBusy}
          sx={{
            borderColor: GOLD,
            color: GOLD,
            '&:hover': { borderColor: GOLD, bgcolor: 'rgba(212,168,67,0.08)' },
          }}
        >
          {testMutation.isPending ? (
            <CircularProgress size={20} sx={{ color: GOLD, mr: 1 }} />
          ) : null}
          Test Connection
        </Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={isBusy}
          sx={{
            bgcolor: GOLD,
            color: '#1A1A1E',
            fontWeight: 700,
            '&:hover': { bgcolor: '#c49a38' },
          }}
        >
          {saveMutation.isPending ? (
            <CircularProgress size={20} sx={{ color: '#1A1A1E', mr: 1 }} />
          ) : null}
          Save & Activate
        </Button>
      </Box>
    </Box>
  );
}
