import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

interface PaginationMeta {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
}

interface DataProductState {
  session_id?: string;
  current_phase?: string;
  [key: string]: unknown;
}

interface DataProduct {
  id: string;
  name: string;
  description?: string;
  database_reference: string;
  schemas?: string[];
  tables?: string[];
  status: 'discovery' | 'requirements' | 'generation' | 'validation' | 'published' | 'archived';
  state?: DataProductState;
  health_score: number | null;
  current_phase: string | null;
  published_at: string | null;
  published_agent_fqn: string | null;
  owner: string;
  share_count: number;
  created_at: string;
  updated_at: string;
}

interface DataProductsResponse {
  data: DataProduct[];
  meta: PaginationMeta;
}

interface DataProductCreateInput {
  name: string;
  description?: string;
  database_reference: string;
  schemas: string[];
  tables: string[];
}

interface DataProductUpdateInput {
  name?: string;
  description?: string;
  database_reference?: string;
  schemas?: string[];
  tables?: string[];
  status?: string;
}

function useDataProducts(page: number = 1, perPage: number = 20) {
  return useQuery<DataProductsResponse>({
    queryKey: ['data-products', page, perPage],
    queryFn: () =>
      api.get<DataProductsResponse>(
        `/data-products?page=${page}&per_page=${perPage}`,
      ),
  });
}

function useDataProduct(id: string) {
  return useQuery<DataProduct>({
    queryKey: ['data-products', id],
    queryFn: () => api.get<DataProduct>(`/data-products/${id}`),
    enabled: id.length > 0,
  });
}

function useCreateDataProduct() {
  const queryClient = useQueryClient();

  return useMutation<DataProduct, Error, DataProductCreateInput>({
    mutationFn: (input: DataProductCreateInput) =>
      api.post<DataProduct>('/data-products', input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['data-products'] });
    },
  });
}

function useUpdateDataProduct(id: string) {
  const queryClient = useQueryClient();

  return useMutation<DataProduct, Error, DataProductUpdateInput>({
    mutationFn: (input: DataProductUpdateInput) =>
      api.put<DataProduct>(`/data-products/${id}`, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['data-products'] });
      void queryClient.invalidateQueries({ queryKey: ['data-products', id] });
    },
  });
}

function useDeleteDataProduct() {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: (id: string) => api.del<void>(`/data-products/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['data-products'] });
    },
  });
}

export {
  useDataProducts,
  useDataProduct,
  useCreateDataProduct,
  useUpdateDataProduct,
  useDeleteDataProduct,
};
export type {
  DataProduct,
  DataProductState,
  DataProductsResponse,
  DataProductCreateInput,
  DataProductUpdateInput,
  PaginationMeta,
};
