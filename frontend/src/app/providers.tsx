'use client';

import { useState } from 'react';
import { Box } from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeRegistry } from '@/theme/ThemeRegistry';
import { AppSidebar } from '@/components/layout/AppSidebar';

interface ProvidersProps {
  children: React.ReactNode;
}

export function Providers({ children }: ProvidersProps): React.ReactNode {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeRegistry>
        <Box sx={{ display: 'flex', minHeight: '100vh' }}>
          <AppSidebar />
          <Box component="main" sx={{ flex: 1, p: 3, overflow: 'auto' }}>
            {children}
          </Box>
        </Box>
      </ThemeRegistry>
    </QueryClientProvider>
  );
}
