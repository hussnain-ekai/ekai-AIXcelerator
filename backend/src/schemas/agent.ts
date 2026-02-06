import { z } from 'zod';

// --- Send message ---

export const sendMessageSchema = z.object({
  session_id: z.string().uuid(),
  message: z.string().min(1).max(10000),
  data_product_id: z.string().uuid().optional(),
  attachments: z
    .array(
      z.object({
        type: z.enum(['document', 'artifact']),
        id: z.string().uuid(),
      }),
    )
    .optional()
    .default([]),
});

export type SendMessageInput = z.infer<typeof sendMessageSchema>;

// --- Session ID param ---

export const sessionIdParamSchema = z.object({
  sessionId: z.string().uuid(),
});

export type SessionIdParam = z.infer<typeof sessionIdParamSchema>;

// --- Interrupt ---

export const interruptSchema = z.object({
  reason: z.string().max(500).optional(),
});

export type InterruptInput = z.infer<typeof interruptSchema>;
