'use client';

import { useCallback, useEffect, useMemo } from 'react';
import {
  Box,
  Chip,
  IconButton,
  Skeleton,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  BaseEdge,
  getSmoothStepPath,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import type {
  Node,
  Edge,
  EdgeProps,
  NodeProps,
  NodeTypes,
  EdgeTypes,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import Dagre from '@dagrejs/dagre';
import { ResizableDrawer } from './ResizableDrawer';
import type { LineageNode, LineageEdge, LineageResponse } from '@/hooks/useArtifacts';

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const GOLD = '#D4A843';
const BLUE = '#3B82F6';
const PURPLE = '#9333EA';
const DRAWER_WIDTH = 900;
const NODE_WIDTH = 240;
const NODE_HEIGHT = 72;

const LAYER_COLORS: Record<string, string> = {
  source: BLUE,
  silver: PURPLE,
  gold: GOLD,
};

const LAYER_LABELS: Record<string, string> = {
  source: 'Source',
  silver: 'Silver',
  gold: 'Gold',
};

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface LineageDiagramViewerProps {
  open: boolean;
  onClose: () => void;
  data: LineageResponse | null;
  isLoading?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Custom node                                                        */
/* ------------------------------------------------------------------ */

type LineageNodeData = {
  label: string;
  fqn: string;
  layer: string;
  tableType: string;
};

type LineageFlowNode = Node<LineageNodeData, 'lineage'>;

function LineageNodeComponent({ data }: NodeProps<LineageFlowNode>): React.ReactNode {
  const color = LAYER_COLORS[data.layer] ?? BLUE;
  const layerLabel = LAYER_LABELS[data.layer] ?? 'Source';
  const tableType = data.tableType?.toLowerCase() ?? '';
  const showChip = data.layer === 'gold' && (tableType === 'fact' || tableType === 'dimension');

  return (
    <Box
      sx={{
        width: NODE_WIDTH,
        bgcolor: '#252528',
        border: '1px solid #3A3A3E',
        borderRadius: 1.5,
        overflow: 'hidden',
        boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: color, width: 8, height: 8, border: '2px solid #252528' }}
      />

      {/* Colored header bar */}
      <Box
        sx={{
          height: 4,
          bgcolor: color,
        }}
      />

      {/* Content */}
      <Box sx={{ px: 1.5, py: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.25 }}>
          <Typography
            variant="caption"
            fontWeight={700}
            sx={{ color: '#F5F5F5', fontSize: '0.75rem', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {data.label}
          </Typography>
          <Chip
            label={layerLabel}
            size="small"
            sx={{
              height: 18,
              fontSize: '0.58rem',
              fontWeight: 700,
              bgcolor: `${color}20`,
              color: color,
              border: `1px solid ${color}40`,
              '& .MuiChip-label': { px: 0.75 },
            }}
          />
          {showChip && (
            <Chip
              label={tableType === 'fact' ? 'FACT' : 'DIM'}
              size="small"
              sx={{
                height: 18,
                fontSize: '0.55rem',
                fontWeight: 700,
                bgcolor: tableType === 'fact' ? `${GOLD}20` : 'rgba(76,175,80,0.15)',
                color: tableType === 'fact' ? GOLD : '#4CAF50',
                border: `1px solid ${tableType === 'fact' ? `${GOLD}40` : 'rgba(76,175,80,0.4)'}`,
                '& .MuiChip-label': { px: 0.5 },
              }}
            />
          )}
        </Box>
        <Typography
          variant="caption"
          sx={{ color: '#9E9E9E', fontSize: '0.6rem', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          title={data.fqn}
        >
          {data.fqn}
        </Typography>
      </Box>

      <Handle
        type="source"
        position={Position.Right}
        style={{ background: color, width: 8, height: 8, border: '2px solid #252528' }}
      />
    </Box>
  );
}

/* ------------------------------------------------------------------ */
/*  Custom edge                                                        */
/* ------------------------------------------------------------------ */

type LineageEdgeData = {
  relType: string;
};

function LineageEdgeComponent(props: EdgeProps<Edge<LineageEdgeData>>): React.ReactNode {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    data,
  } = props;

  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 16,
  });

  const isTransform = data?.relType === 'TRANSFORMED_TO';
  const strokeColor = isTransform ? PURPLE : GOLD;
  const dashArray = isTransform ? '6 4' : undefined;

  return (
    <BaseEdge
      id={id}
      path={edgePath}
      style={{
        stroke: strokeColor,
        strokeWidth: 2,
        strokeDasharray: dashArray,
      }}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Dagre layout                                                       */
/* ------------------------------------------------------------------ */

function extractTableName(fqn: string): string {
  return fqn.split('.').pop() ?? fqn;
}

function toFlowNodes(lineageNodes: LineageNode[]): LineageFlowNode[] {
  return lineageNodes.map((n) => ({
    id: n.fqn,
    type: 'lineage' as const,
    position: { x: 0, y: 0 },
    data: {
      label: extractTableName(n.fqn),
      fqn: n.fqn,
      layer: n.layer,
      tableType: n.tableType,
    },
  }));
}

function toFlowEdges(lineageEdges: LineageEdge[]): Edge<LineageEdgeData>[] {
  return lineageEdges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: 'lineage',
    data: {
      relType: e.relType,
    },
  }));
}

function getLayoutedElements(
  nodes: LineageFlowNode[],
  edges: Edge<LineageEdgeData>[],
): { nodes: LineageFlowNode[]; edges: Edge<LineageEdgeData>[] } {
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 250 });

  for (const node of nodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  Dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
}

/* ------------------------------------------------------------------ */
/*  Node / Edge types                                                  */
/* ------------------------------------------------------------------ */

const NODE_TYPES: NodeTypes = {
  lineage: LineageNodeComponent,
};

const EDGE_TYPES: EdgeTypes = {
  lineage: LineageEdgeComponent,
};

/* ------------------------------------------------------------------ */
/*  Inner canvas (must be inside ReactFlowProvider)                    */
/* ------------------------------------------------------------------ */

interface LineageFlowCanvasProps {
  lineageData: LineageResponse;
}

function LineageFlowCanvas({ lineageData }: LineageFlowCanvasProps): React.ReactNode {
  const [nodes, setNodes, onNodesChange] = useNodesState<LineageFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<LineageEdgeData>>([]);

  useEffect(() => {
    const rawNodes = toFlowNodes(lineageData.nodes);
    const rawEdges = toFlowEdges(lineageData.edges);
    const { nodes: ln, edges: le } = getLayoutedElements(rawNodes, rawEdges);
    setNodes(ln);
    setEdges(le);
  }, [lineageData, setNodes, setEdges]);

  const handleInit = useCallback(
    (instance: { fitView: () => void }) => {
      instance.fitView();
    },
    [],
  );

  // MiniMap color by layer
  const miniMapNodeColor = useCallback((node: Node) => {
    const data = node.data as LineageNodeData;
    return LAYER_COLORS[data.layer] ?? BLUE;
  }, []);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      onInit={handleInit}
      fitView
      proOptions={{ hideAttribution: true }}
      style={{ backgroundColor: '#1A1A1E' }}
      minZoom={0.2}
      maxZoom={2}
    >
      <Background color="#3A3A3E" gap={20} />
      <Controls showInteractive={false} style={{ borderRadius: 8 }} />
      <MiniMap
        nodeColor={miniMapNodeColor}
        maskColor="rgba(0,0,0,0.7)"
        style={{ backgroundColor: '#1A1A1E', borderRadius: 8, border: '1px solid #3A3A3E' }}
        pannable
        zoomable
      />
    </ReactFlow>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function LineageDiagramViewer({
  open,
  onClose,
  data,
  isLoading,
}: LineageDiagramViewerProps): React.ReactNode {
  const hasData = data !== null && data.nodes.length > 0;

  // Count nodes by layer
  const layerCounts = useMemo(() => {
    if (!data) return { source: 0, silver: 0, gold: 0 };
    return {
      source: data.nodes.filter((n) => n.layer === 'source').length,
      silver: data.nodes.filter((n) => n.layer === 'silver').length,
      gold: data.nodes.filter((n) => n.layer === 'gold').length,
    };
  }, [data]);

  return (
    <ResizableDrawer defaultWidth={DRAWER_WIDTH} open={open} onClose={onClose}>
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 2.5,
            py: 2,
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          <Typography variant="h6" fontWeight={700}>
            Data Lineage
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Legend */}
        {hasData && (
          <Box
            sx={{
              display: 'flex',
              gap: 3,
              px: 2.5,
              py: 1.5,
              borderBottom: 1,
              borderColor: 'divider',
              alignItems: 'center',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Box sx={{ width: 12, height: 12, borderRadius: 0.5, bgcolor: BLUE }} />
              <Typography variant="caption" color="text.secondary">Source</Typography>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Box sx={{ width: 12, height: 12, borderRadius: 0.5, bgcolor: PURPLE }} />
              <Typography variant="caption" color="text.secondary">Silver</Typography>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Box sx={{ width: 12, height: 12, borderRadius: 0.5, bgcolor: GOLD }} />
              <Typography variant="caption" color="text.secondary">Gold</Typography>
            </Box>
            <Box sx={{ ml: 'auto', display: 'flex', alignItems: 'center', gap: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Box sx={{ width: 24, height: 0, borderTop: `2px dashed ${PURPLE}` }} />
                <Typography variant="caption" color="text.secondary">Transform</Typography>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Box sx={{ width: 24, height: 0, borderTop: `2px solid ${GOLD}` }} />
                <Typography variant="caption" color="text.secondary">Model</Typography>
              </Box>
            </Box>
          </Box>
        )}

        {/* Content */}
        <Box sx={{ flex: 1, minHeight: 0 }}>
          {isLoading ? (
            <Box sx={{ p: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <Skeleton variant="rectangular" width="100%" height={60} sx={{ borderRadius: 1 }} />
              <Skeleton variant="rectangular" width="80%" height={60} sx={{ borderRadius: 1 }} />
              <Skeleton variant="rectangular" width="60%" height={60} sx={{ borderRadius: 1 }} />
            </Box>
          ) : hasData ? (
            <ReactFlowProvider>
              <LineageFlowCanvas lineageData={data} />
            </ReactFlowProvider>
          ) : (
            <Box
              sx={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                px: 4,
                py: 6,
                textAlign: 'center',
              }}
            >
              <Box
                sx={{
                  width: 64,
                  height: 64,
                  borderRadius: 2,
                  border: 2,
                  borderColor: `${GOLD}44`,
                  bgcolor: `${GOLD}11`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  mb: 3,
                }}
              >
                <Box
                  sx={{
                    width: 32,
                    height: 2,
                    bgcolor: GOLD,
                    position: 'relative',
                    '&::before': {
                      content: '""',
                      position: 'absolute',
                      top: -10,
                      left: 0,
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      border: 2,
                      borderColor: GOLD,
                    },
                    '&::after': {
                      content: '""',
                      position: 'absolute',
                      top: -10,
                      right: 0,
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      border: 2,
                      borderColor: GOLD,
                    },
                  }}
                />
              </Box>
              <Typography variant="h6" fontWeight={700} sx={{ mb: 1 }}>
                No Lineage Data
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ lineHeight: 1.7, maxWidth: 320 }}
              >
                Lineage data will appear here after the transformation and modeling
                phases complete. The graph shows how source tables flow through
                silver to gold layers.
              </Typography>
            </Box>
          )}
        </Box>

        {/* Footer */}
        {hasData && (
          <Box
            sx={{
              px: 2.5,
              py: 1.5,
              borderTop: 1,
              borderColor: 'divider',
            }}
          >
            <Typography variant="caption" color="text.secondary">
              {layerCounts.source} source &middot; {layerCounts.silver} silver &middot; {layerCounts.gold} gold &middot; {data.edges.length} relationship{data.edges.length !== 1 ? 's' : ''}
            </Typography>
          </Box>
        )}
      </Box>
    </ResizableDrawer>
  );
}

export type { LineageDiagramViewerProps };
