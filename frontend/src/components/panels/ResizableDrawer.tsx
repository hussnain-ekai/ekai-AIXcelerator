'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Drawer } from '@mui/material';
import type { DrawerProps } from '@mui/material';

const GOLD = '#D4A843';

interface ResizableDrawerProps extends Omit<DrawerProps, 'PaperProps'> {
  defaultWidth: number;
  minWidth?: number;
  maxWidth?: number;
  children: React.ReactNode;
}

export function ResizableDrawer({
  defaultWidth,
  minWidth = 380,
  maxWidth = 1200,
  children,
  open,
  onClose,
  ...rest
}: ResizableDrawerProps): React.ReactNode {
  const [width, setWidth] = useState(defaultWidth);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  // Reset width when drawer closes then reopens
  useEffect(() => {
    if (open) {
      setWidth(defaultWidth);
    }
  }, [open, defaultWidth]);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDragging.current = true;
      startX.current = e.clientX;
      startWidth.current = width;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [width],
  );

  useEffect(() => {
    function handleMouseMove(e: MouseEvent) {
      if (!isDragging.current) return;
      // Dragging left edge of a right-anchored drawer: moving mouse left = wider
      const delta = startX.current - e.clientX;
      const newWidth = Math.min(maxWidth, Math.max(minWidth, startWidth.current + delta));
      setWidth(newWidth);
    }

    function handleMouseUp() {
      if (!isDragging.current) return;
      isDragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [minWidth, maxWidth]);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      variant="temporary"
      slotProps={{
        paper: {
          sx: {
            width,
            bgcolor: 'background.default',
            borderLeft: 1,
            borderColor: 'divider',
            overflow: 'visible',
          },
        },
      }}
      {...rest}
    >
      {/* Drag handle */}
      <Box
        onMouseDown={handleMouseDown}
        sx={{
          position: 'absolute',
          top: 0,
          left: -3,
          width: 6,
          height: '100%',
          cursor: 'col-resize',
          zIndex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          '&:hover > div, &:active > div': {
            opacity: 1,
            bgcolor: GOLD,
          },
        }}
      >
        <Box
          sx={{
            width: 3,
            height: 48,
            borderRadius: 1,
            bgcolor: 'divider',
            opacity: 0.5,
            transition: 'opacity 0.15s, background-color 0.15s',
          }}
        />
      </Box>
      {children}
    </Drawer>
  );
}
