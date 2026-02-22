'use client';

import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from '@mui/material';
import { useUpdateDataProduct } from '@/hooks/useDataProducts';
import type { DataProduct } from '@/hooks/useDataProducts';

interface EditDataProductModalProps {
  open: boolean;
  onClose: () => void;
  product: DataProduct;
}

export function EditDataProductModal({
  open,
  onClose,
  product,
}: EditDataProductModalProps): React.ReactNode {
  const [name, setName] = useState(product.name);
  const [description, setDescription] = useState(product.description ?? '');
  const [error, setError] = useState('');
  const updateMutation = useUpdateDataProduct(product.id);

  // Sync fields when product changes (e.g. different row selected)
  useEffect(() => {
    if (open) {
      setName(product.name);
      setDescription(product.description ?? '');
      setError('');
    }
  }, [open, product.name, product.description]);

  const trimmedName = name.trim();
  const isValid = trimmedName.length > 0 && trimmedName.length <= 256;

  async function handleSave(): Promise<void> {
    if (!isValid) return;
    setError('');
    try {
      await updateMutation.mutateAsync({
        name: trimmedName,
        description: description.trim() || undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update data product');
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
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="h6" fontWeight={700}>
          Edit Data Product
        </Typography>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          <TextField
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            fullWidth
            autoFocus
            error={trimmedName.length > 256}
            helperText={trimmedName.length > 256 ? 'Name must be 256 characters or less' : undefined}
            slotProps={{ htmlInput: { maxLength: 257 } }}
          />
          <TextField
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            fullWidth
            multiline
            rows={3}
            placeholder="Describe the purpose of this data product..."
            error={description.length > 2000}
            helperText={description.length > 2000 ? 'Description must be 2000 characters or less' : undefined}
            slotProps={{ htmlInput: { maxLength: 2001 } }}
          />
          {error && <Alert severity="error">{error}</Alert>}
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          onClick={() => void handleSave()}
          variant="contained"
          disabled={!isValid || description.length > 2000 || updateMutation.isPending}
          startIcon={updateMutation.isPending ? <CircularProgress size={16} color="inherit" /> : undefined}
        >
          {updateMutation.isPending ? 'Saving...' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
