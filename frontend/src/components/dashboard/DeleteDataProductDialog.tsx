'use client';

import { useState } from 'react';
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Typography,
} from '@mui/material';
import { useDeleteDataProduct } from '@/hooks/useDataProducts';
import type { DataProduct } from '@/hooks/useDataProducts';

interface DeleteDataProductDialogProps {
  open: boolean;
  onClose: () => void;
  product: DataProduct;
  onDeleted?: () => void;
}

export function DeleteDataProductDialog({
  open,
  onClose,
  product,
  onDeleted,
}: DeleteDataProductDialogProps): React.ReactNode {
  const [error, setError] = useState('');
  const deleteMutation = useDeleteDataProduct();

  async function handleDelete(): Promise<void> {
    setError('');
    try {
      await deleteMutation.mutateAsync(product.id);
      onClose();
      onDeleted?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete data product');
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: { borderRadius: 2 } } }}
    >
      <DialogTitle>
        <Typography variant="h6" fontWeight={700}>
          Delete Data Product
        </Typography>
      </DialogTitle>

      <DialogContent>
        <DialogContentText>
          Are you sure you want to delete <strong>{product.name}</strong>? This will archive the
          data product and all its artifacts. This action cannot be easily undone.
        </DialogContentText>
        {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          onClick={() => void handleDelete()}
          variant="contained"
          color="error"
          disabled={deleteMutation.isPending}
          startIcon={deleteMutation.isPending ? <CircularProgress size={16} color="inherit" /> : undefined}
        >
          {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
