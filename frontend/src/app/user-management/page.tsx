'use client';

import { useState } from 'react';
import {
  Avatar,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  Typography,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material';
import { useAuthStore } from '@/stores/authStore';

const GOLD = '#D4A843';

export default function UserManagementPage(): React.ReactNode {
  const displayName = useAuthStore((state) => state.displayName);
  const snowflakeRole = useAuthStore((state) => state.snowflakeRole);
  const user = useAuthStore((state) => state.user);

  const [darkMode, setDarkMode] = useState(true);
  const [emailNotifications, setEmailNotifications] = useState(true);
  const [defaultRows, setDefaultRows] = useState('20');

  function handleRowsChange(event: SelectChangeEvent): void {
    setDefaultRows(event.target.value);
  }

  function handleSave(): void {
    // Preferences would be persisted via API in production
  }

  const initials = displayName
    ? displayName
        .split(/\s+/)
        .map((p) => p[0])
        .join('')
        .toUpperCase()
        .slice(0, 2)
    : 'U';

  return (
    <Box sx={{ maxWidth: 600 }}>
      <Typography variant="h4" component="h1" fontWeight={700} gutterBottom>
        User Management
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Manage your profile and workspace preferences.
      </Typography>

      {/* Profile Card */}
      <Card variant="outlined" sx={{ mb: 3 }}>
        <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <Avatar
            sx={{
              width: 56,
              height: 56,
              bgcolor: GOLD,
              color: '#1A1A1E',
              fontWeight: 700,
              fontSize: '1.25rem',
            }}
          >
            {initials}
          </Avatar>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h6" fontWeight={600}>
              {displayName ?? 'Unknown User'}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {user ?? 'Not connected to Snowflake'}
            </Typography>
          </Box>
          <Chip
            label={snowflakeRole ?? 'No Role'}
            variant="outlined"
            sx={{
              borderColor: GOLD,
              color: GOLD,
              fontWeight: 600,
            }}
          />
        </CardContent>
      </Card>

      {/* Preferences Card */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="h6" fontWeight={600} gutterBottom>
            Preferences
          </Typography>
          <Divider sx={{ mb: 2 }} />

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={darkMode}
                  onChange={(_, checked) => setDarkMode(checked)}
                  sx={{
                    '& .MuiSwitch-switchBase.Mui-checked': { color: GOLD },
                    '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                      bgcolor: GOLD,
                    },
                  }}
                />
              }
              label="Dark mode"
            />

            <FormControlLabel
              control={
                <Switch
                  checked={emailNotifications}
                  onChange={(_, checked) => setEmailNotifications(checked)}
                  sx={{
                    '& .MuiSwitch-switchBase.Mui-checked': { color: GOLD },
                    '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                      bgcolor: GOLD,
                    },
                  }}
                />
              }
              label="Email notifications"
            />

            <FormControl size="small" sx={{ maxWidth: 240 }}>
              <InputLabel>Default rows per page</InputLabel>
              <Select
                value={defaultRows}
                onChange={handleRowsChange}
                label="Default rows per page"
              >
                <MenuItem value="10">10</MenuItem>
                <MenuItem value="20">20</MenuItem>
                <MenuItem value="50">50</MenuItem>
              </Select>
            </FormControl>

            <Box sx={{ mt: 2 }}>
              <Button variant="contained" onClick={handleSave}>
                Save Preferences
              </Button>
            </Box>
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}
