import { useAuthStore } from '@/stores/authStore';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface ApiError {
  error: string;
  message: string;
  details?: Record<string, unknown>;
}

class ApiRequestError extends Error {
  public readonly status: number;
  public readonly errorCode: string;
  public readonly details: Record<string, unknown> | undefined;

  constructor(status: number, body: ApiError) {
    super(body.message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.errorCode = body.error;
    this.details = body.details;
  }
}

function getAuthHeaders(): Record<string, string> {
  const user = useAuthStore.getState().user;
  const effectiveUser =
    user ?? (process.env.NODE_ENV === 'development' ? 'dev@localhost' : null);
  if (effectiveUser) {
    return { 'Sf-Context-Current-User': effectiveUser };
  }
  return {};
}

async function parseApiError(response: Response): Promise<ApiError> {
  const fallbackMessage = `Request failed (${response.status}${response.statusText ? ` ${response.statusText}` : ''})`;
  const contentType = response.headers.get('content-type') ?? '';

  if (contentType.includes('application/json')) {
    try {
      const parsed = (await response.json()) as Partial<ApiError>;
      const parsedMessage =
        typeof parsed.message === 'string' && parsed.message.trim().length > 0
          ? parsed.message.trim()
          : fallbackMessage;
      const parsedCode =
        typeof parsed.error === 'string' && parsed.error.trim().length > 0
          ? parsed.error
          : `HTTP_${response.status}`;
      const parsedDetails =
        parsed.details && typeof parsed.details === 'object'
          ? (parsed.details as Record<string, unknown>)
          : undefined;

      return {
        error: parsedCode,
        message: parsedMessage,
        details: parsedDetails,
      };
    } catch {
      // Fall through to plain-text parsing below.
    }
  }

  let rawText = '';
  try {
    rawText = await response.text();
  } catch {
    rawText = '';
  }

  const textMessage = rawText.trim().length > 0 ? rawText.trim() : fallbackMessage;
  return {
    error: `HTTP_${response.status}`,
    message: textMessage,
  };
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE_URL}${path}`;

  const headers: Record<string, string> = {
    ...getAuthHeaders(),
    ...(options.headers as Record<string, string> | undefined),
  };

  const isFormDataBody =
    typeof FormData !== 'undefined' && options.body instanceof FormData;

  if (options.body !== undefined && options.body !== null && !isFormDataBody) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const body = await parseApiError(response);
    throw new ApiRequestError(response.status, body);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

function get<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'GET' });
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

function postForm<T>(path: string, formData: FormData): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: formData,
  });
}

function put<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}

export const api = { get, post, postForm, put, del, request };
export { ApiRequestError };
export type { ApiError };
