import { z } from 'zod';

// --- Create ---

export const createDataProductSchema = z.object({
  name: z.string().min(1).max(256),
  description: z.string().max(2000).optional().default(''),
  database_reference: z.string().min(1),
  schemas: z.array(z.string().min(1)).min(1),
  tables: z.array(z.string().min(1)).min(1),
});

export type CreateDataProductInput = z.infer<typeof createDataProductSchema>;

// --- Update ---

export const updateDataProductSchema = z.object({
  name: z.string().min(1).max(256).optional(),
  description: z.string().max(2000).optional(),
  database_reference: z.string().min(1).optional(),
  schemas: z.array(z.string().min(1)).min(1).optional(),
  tables: z.array(z.string().min(1)).min(1).optional(),
  status: z
    .enum([
      'discovery',
      'requirements',
      'generation',
      'validation',
      'published',
      'archived',
    ])
    .optional(),
});

export type UpdateDataProductInput = z.infer<typeof updateDataProductSchema>;

// --- Share ---

export const shareDataProductSchema = z.object({
  shared_with_user: z.string().min(1),
  permission: z.enum(['view', 'edit']),
});

export type ShareDataProductInput = z.infer<typeof shareDataProductSchema>;

// --- Pagination query ---

export const listDataProductsQuerySchema = z.object({
  page: z.coerce.number().int().min(1).default(1),
  per_page: z.coerce.number().int().min(1).max(100).default(20),
  search: z.string().optional(),
  status: z
    .enum([
      'discovery',
      'requirements',
      'generation',
      'validation',
      'published',
      'archived',
    ])
    .optional(),
  sort_by: z
    .enum(['name', 'updated_at', 'created_at', 'status', 'health_score'])
    .default('updated_at'),
  sort_order: z.enum(['asc', 'desc']).default('desc'),
});

export type ListDataProductsQuery = z.infer<typeof listDataProductsQuerySchema>;
