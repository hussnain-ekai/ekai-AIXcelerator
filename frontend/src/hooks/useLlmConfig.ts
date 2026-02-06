import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

type LLMProvider = 'snowflake-cortex' | 'vertex-ai' | 'azure-openai' | 'anthropic' | 'openai';

interface LLMActiveStatus {
  provider: string;
  model: string;
  is_override: boolean;
}

interface LLMDefaults {
  azure_openai_endpoint: string;
  azure_openai_deployment: string;
  azure_openai_api_version: string;
  azure_openai_key_configured: boolean;
}

interface LLMConfigData {
  saved: LLMConfigBody | null;
  active: LLMActiveStatus | null;
  defaults: LLMDefaults;
}

interface LLMConfigBody {
  provider: LLMProvider;
  model?: string;
  cortex_model?: string;
  // Vertex AI
  vertex_credentials_json?: string;
  vertex_project?: string;
  vertex_location?: string;
  vertex_model?: string;
  // Anthropic
  anthropic_api_key?: string;
  anthropic_model?: string;
  // OpenAI
  openai_api_key?: string;
  openai_model?: string;
  // Azure OpenAI
  azure_openai_api_key?: string;
  azure_openai_endpoint?: string;
  azure_openai_deployment?: string;
  azure_openai_api_version?: string;
}

interface LLMSaveResponse {
  saved: boolean;
  active: {
    status: string;
    provider: string;
    model: string;
    error?: string;
  };
}

interface LLMTestResponse {
  status: 'ok' | 'error';
  response_time_ms?: number;
  model_response?: string;
  error?: string;
}

function useLlmConfig() {
  return useQuery<LLMConfigData>({
    queryKey: ['llm-config'],
    queryFn: () => api.get<LLMConfigData>('/settings/llm'),
  });
}

function useLlmSave() {
  const queryClient = useQueryClient();
  return useMutation<LLMSaveResponse, Error, LLMConfigBody>({
    mutationFn: (body) => api.put<LLMSaveResponse>('/settings/llm', body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['llm-config'] });
    },
  });
}

function useLlmTest() {
  return useMutation<LLMTestResponse, Error, LLMConfigBody>({
    mutationFn: (body) => api.post<LLMTestResponse>('/settings/llm/test', body),
  });
}

export { useLlmConfig, useLlmSave, useLlmTest };
export type {
  LLMProvider,
  LLMConfigBody,
  LLMConfigData,
  LLMActiveStatus,
  LLMDefaults,
  LLMSaveResponse,
  LLMTestResponse,
};
