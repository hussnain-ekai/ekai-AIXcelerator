'use client';

import { Avatar, AvatarGroup, Tooltip } from '@mui/material';

interface CollaboratorAvatarsProps {
  collaborators: string[];
  max?: number;
}

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? '';
  const second = parts[1]?.[0] ?? '';
  return (first + second).toUpperCase();
}

function stringToColor(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash % 360);
  return `hsl(${hue}, 45%, 45%)`;
}

export function CollaboratorAvatars({
  collaborators,
  max = 3,
}: CollaboratorAvatarsProps): React.ReactNode {
  if (collaborators.length === 0) {
    return null;
  }

  return (
    <AvatarGroup
      max={max}
      sx={{
        '& .MuiAvatar-root': {
          width: 28,
          height: 28,
          fontSize: '0.75rem',
          fontWeight: 600,
        },
      }}
    >
      {collaborators.map((name) => (
        <Tooltip key={name} title={name}>
          <Avatar sx={{ bgcolor: stringToColor(name) }}>
            {getInitials(name)}
          </Avatar>
        </Tooltip>
      ))}
    </AvatarGroup>
  );
}
