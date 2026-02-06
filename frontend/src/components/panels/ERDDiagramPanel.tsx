'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  Drawer,
  IconButton,
  TextField,
  Tooltip,
  Typography,
  InputAdornment,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import SearchIcon from '@mui/icons-material/Search';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  useNodesState,
  useEdgesState,
  useReactFlow,
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

const GOLD = '#D4A843';
const GREEN = '#4CAF50';
const DRAWER_WIDTH = 1100;
const SIDEBAR_WIDTH = 220;

/* ------------------------------------------------------------------ */
/*  External data types (what the consumer provides)                  */
/* ------------------------------------------------------------------ */

interface ERDColumn {
  name: string;
  dataType: string;
  nullable?: boolean;
  isPrimaryKey?: boolean;
  isForeignKey?: boolean;
}

interface ERDNode {
  id: string;
  name: string;
  type: 'fact' | 'dimension';
  rowCount: number;
  columns?: ERDColumn[];
  x?: number;
  y?: number;
}

interface ERDEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  sourceColumn?: string;
  targetColumn?: string;
  confidence?: number;
  cardinality?: string;
}

interface ERDDiagramPanelProps {
  open: boolean;
  onClose: () => void;
  erdData: { nodes: ERDNode[]; edges: ERDEdge[] } | null;
}

/* ------------------------------------------------------------------ */
/*  Cardinality formatting                                             */
/* ------------------------------------------------------------------ */

function formatCardinality(raw?: string): string {
  if (!raw) return 'FK';
  const lower = raw.toLowerCase().replace(/[_\s-]/g, '');
  if (lower === 'manytoone' || lower === 'n:1') return 'N:1';
  if (lower === 'onetomany' || lower === '1:n') return '1:N';
  if (lower === 'onetoone' || lower === '1:1') return '1:1';
  if (lower === 'manytomany' || lower === 'm:n') return 'M:N';
  return raw;
}

/* ------------------------------------------------------------------ */
/*  Custom table node                                                  */
/* ------------------------------------------------------------------ */

type TableNodeData = {
  label: string;
  tableType: 'fact' | 'dimension';
  rowCount: number;
  columns: ERDColumn[];
  focused?: boolean;
  dimmed?: boolean;
};

type TableNode = Node<TableNodeData, 'table'>;

function ColumnRow({ col }: { col: ERDColumn }): React.ReactNode {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 0.5,
        px: 1.5,
        py: 0.35,
        borderBottom: '1px solid #2F2F33',
        '&:last-child': { borderBottom: 'none' },
      }}
    >
      {col.isPrimaryKey && (
        <Typography sx={{ fontSize: '0.6rem', lineHeight: 1, color: GOLD }}>PK</Typography>
      )}
      {col.isForeignKey && (
        <Typography sx={{ fontSize: '0.6rem', lineHeight: 1, color: '#64B5F6' }}>FK</Typography>
      )}
      <Typography
        variant="caption"
        sx={{
          color: '#F5F5F5',
          fontWeight: col.isPrimaryKey ? 700 : 400,
          fontSize: '0.7rem',
        }}
      >
        {col.name}
      </Typography>
      <Typography
        variant="caption"
        sx={{ color: '#9E9E9E', fontSize: '0.6rem', ml: 'auto' }}
      >
        {col.dataType.toLowerCase()}
      </Typography>
    </Box>
  );
}

function TableNodeComponent({ data }: NodeProps<TableNode>): React.ReactNode {
  const isFact = data.tableType === 'fact';
  const accent = isFact ? GOLD : GREEN;

  // Sort PK columns to top, group them
  const pkCols = data.columns.filter((c) => c.isPrimaryKey);
  const fkCols = data.columns.filter((c) => c.isForeignKey && !c.isPrimaryKey);
  const otherCols = data.columns.filter((c) => !c.isPrimaryKey && !c.isForeignKey);
  const isCompositeKey = pkCols.length > 1;

  return (
    <Box
      sx={{
        minWidth: 230,
        maxWidth: 280,
        bgcolor: '#252528',
        borderLeft: `4px solid ${accent}`,
        border: data.focused
          ? `2px solid ${GOLD}`
          : '1px solid #3A3A3E',
        borderLeftWidth: '4px !important',
        borderLeftColor: `${accent} !important`,
        borderRadius: 1,
        overflow: 'hidden',
        boxShadow: data.focused
          ? `0 0 16px ${GOLD}40`
          : '0 2px 8px rgba(0,0,0,0.3)',
        opacity: data.dimmed ? 0.2 : 1,
        transition: 'opacity 0.25s ease, box-shadow 0.25s ease, border-color 0.25s ease',
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: accent, width: 8, height: 8, border: '2px solid #252528' }}
      />

      {/* Header */}
      <Box
        sx={{
          px: 1.5,
          py: 0.75,
          bgcolor: isFact ? 'rgba(212,168,67,0.15)' : 'rgba(76,175,80,0.15)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid #3A3A3E',
        }}
      >
        <Typography
          variant="caption"
          fontWeight={700}
          sx={{ color: '#F5F5F5', letterSpacing: 0.5, fontSize: '0.75rem' }}
        >
          {data.label}
        </Typography>
        <Typography
          sx={{
            fontSize: '0.6rem',
            fontWeight: 700,
            color: accent,
            bgcolor: `${accent}20`,
            px: 0.75,
            py: 0.15,
            borderRadius: 0.5,
            lineHeight: 1.4,
          }}
        >
          {isFact ? 'FACT' : 'DIM'}
        </Typography>
      </Box>

      {/* Columns — PKs first, then FKs, then rest */}
      <Box sx={{ py: 0.25 }}>
        {/* Composite key section header */}
        {isCompositeKey && (
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              px: 1.5,
              py: 0.3,
              bgcolor: 'rgba(212,168,67,0.08)',
              borderBottom: '1px solid #2F2F33',
            }}
          >
            <Typography sx={{ fontSize: '0.55rem', color: GOLD, fontWeight: 600, letterSpacing: 0.5 }}>
              COMPOSITE KEY ({pkCols.length} cols)
            </Typography>
          </Box>
        )}
        {pkCols.map((col) => (
          <ColumnRow key={col.name} col={col} />
        ))}
        {/* Divider between PK and non-PK when PKs exist */}
        {pkCols.length > 0 && (fkCols.length > 0 || otherCols.length > 0) && (
          <Box sx={{ borderBottom: '1px solid #3A3A3E' }} />
        )}
        {fkCols.map((col) => (
          <ColumnRow key={col.name} col={col} />
        ))}
        {otherCols.map((col) => (
          <ColumnRow key={col.name} col={col} />
        ))}
      </Box>

      <Handle
        type="source"
        position={Position.Right}
        style={{ background: accent, width: 8, height: 8, border: '2px solid #252528' }}
      />
    </Box>
  );
}

/* ------------------------------------------------------------------ */
/*  Custom edge component with cardinality labels + tooltip            */
/* ------------------------------------------------------------------ */

type ERDEdgeData = {
  sourceColumn?: string;
  targetColumn?: string;
  confidence?: number;
  cardinality?: string;
  dimmed?: boolean;
  highlighted?: boolean;
};

function ERDEdgeComponent(props: EdgeProps<Edge<ERDEdgeData>>): React.ReactNode {
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

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 12,
  });

  const isDimmed = data?.dimmed ?? false;
  const isHighlighted = data?.highlighted ?? false;
  const cardinalityLabel = formatCardinality(data?.cardinality);
  const confidence = data?.confidence ?? 1.0;
  const srcCol = data?.sourceColumn || '';
  const tgtCol = data?.targetColumn || '';
  const hasColumns = srcCol.length > 0 && tgtCol.length > 0;

  // Build visible label: "SENSOR_ID → SENSOR_ID (N:1)" or just "N:1"
  const visibleLabel = hasColumns
    ? `${srcCol} → ${tgtCol}`
    : cardinalityLabel;

  const tooltipLines = [
    hasColumns ? `Column: ${srcCol} → ${tgtCol}` : 'Inferred relationship',
    `Confidence: ${Math.round(confidence * 100)}%`,
    `Cardinality: ${cardinalityLabel}`,
  ].join('\n');

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: isHighlighted ? GOLD : '#9E9E9E',
          strokeWidth: isHighlighted ? 2 : 1.5,
          opacity: isDimmed ? 0.15 : 1,
          transition: 'opacity 0.25s ease, stroke 0.25s ease',
        }}
        markerEnd={isHighlighted ? `url(#gold-arrow-${id})` : undefined}
      />
      {/* Custom gold marker for highlighted edges */}
      {isHighlighted && (
        <svg style={{ position: 'absolute', width: 0, height: 0 }}>
          <defs>
            <marker
              id={`gold-arrow-${id}`}
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="8"
              markerHeight="8"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill={GOLD} />
            </marker>
          </defs>
        </svg>
      )}
      <EdgeLabelRenderer>
        <Tooltip title={tooltipLines} arrow placement="top">
          <Box
            sx={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'all',
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              bgcolor: '#252528',
              color: '#F5F5F5',
              border: '1px solid #3A3A3E',
              borderRadius: 0.75,
              px: 0.75,
              py: 0.25,
              fontSize: '0.58rem',
              lineHeight: 1.4,
              opacity: isDimmed ? 0.15 : 1,
              transition: 'opacity 0.25s ease',
              cursor: 'default',
              whiteSpace: 'nowrap',
            }}
            className="nodrag nopan"
          >
            <Box component="span" sx={{ fontWeight: 600 }}>
              {visibleLabel}
            </Box>
            {hasColumns && (
              <Box
                component="span"
                sx={{
                  fontSize: '0.52rem',
                  color: '#9E9E9E',
                  fontWeight: 500,
                }}
              >
                {cardinalityLabel}
              </Box>
            )}
          </Box>
        </Tooltip>
      </EdgeLabelRenderer>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Dagre auto-layout                                                  */
/* ------------------------------------------------------------------ */

const HEADER_HEIGHT = 32;
const ROW_HEIGHT = 22;
const NODE_PADDING = 8;
const NODE_WIDTH = 260;

function computeNodeHeight(columns: ERDColumn[]): number {
  return HEADER_HEIGHT + columns.length * ROW_HEIGHT + NODE_PADDING;
}

function toFlowNodes(erdNodes: ERDNode[]): Node<TableNodeData, 'table'>[] {
  return erdNodes.map((n) => ({
    id: n.id,
    type: 'table' as const,
    position: { x: 0, y: 0 }, // placeholder — dagre will position
    data: {
      label: n.name,
      tableType: n.type,
      rowCount: n.rowCount,
      columns: n.columns ?? [],
    },
  }));
}

function toFlowEdges(erdEdges: ERDEdge[]): Edge<ERDEdgeData>[] {
  return erdEdges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: 'erd',
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#9E9E9E' },
    data: {
      sourceColumn: e.sourceColumn,
      targetColumn: e.targetColumn,
      confidence: e.confidence,
      cardinality: e.cardinality,
    },
  }));
}

function getLayoutedElements(
  nodes: Node<TableNodeData, 'table'>[],
  edges: Edge<ERDEdgeData>[],
): { nodes: Node<TableNodeData, 'table'>[]; edges: Edge<ERDEdgeData>[] } {
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 80, ranksep: 200 });

  for (const node of nodes) {
    const h = computeNodeHeight(node.data.columns);
    g.setNode(node.id, { width: NODE_WIDTH, height: h });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  Dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    const h = computeNodeHeight(node.data.columns);
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - h / 2,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
}

/* ------------------------------------------------------------------ */
/*  Node / Edge types                                                  */
/* ------------------------------------------------------------------ */

const NODE_TYPES: NodeTypes = {
  table: TableNodeComponent,
};

const EDGE_TYPES: EdgeTypes = {
  erd: ERDEdgeComponent,
};

/* ------------------------------------------------------------------ */
/*  Inner canvas (must be inside ReactFlowProvider)                    */
/* ------------------------------------------------------------------ */

interface ERDFlowCanvasProps {
  erdData: { nodes: ERDNode[]; edges: ERDEdge[] };
  focusedNodeId: string | null;
  onNodeClick: (nodeId: string) => void;
  onPaneClick: () => void;
}

function ERDFlowCanvas({
  erdData,
  focusedNodeId,
  onNodeClick,
  onPaneClick,
}: ERDFlowCanvasProps): React.ReactNode {
  const reactFlow = useReactFlow();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<TableNodeData, 'table'>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<ERDEdgeData>>([]);

  // Layout when erdData changes
  useEffect(() => {
    const rawNodes = toFlowNodes(erdData.nodes);
    const rawEdges = toFlowEdges(erdData.edges);
    const { nodes: ln, edges: le } = getLayoutedElements(rawNodes, rawEdges);
    setNodes(ln);
    setEdges(le);
  }, [erdData, setNodes, setEdges]);

  // Derive connected set for focus highlighting
  const connectedIds = useMemo(() => {
    if (!focusedNodeId) return null;
    const ids = new Set<string>([focusedNodeId]);
    const edgeIds = new Set<string>();
    for (const e of edges) {
      if (e.source === focusedNodeId || e.target === focusedNodeId) {
        ids.add(e.source);
        ids.add(e.target);
        edgeIds.add(e.id);
      }
    }
    return { nodeIds: ids, edgeIds };
  }, [focusedNodeId, edges]);

  // Apply focus styling to nodes
  const styledNodes = useMemo(() => {
    if (!connectedIds) {
      return nodes.map((n) => ({
        ...n,
        data: { ...n.data, focused: false, dimmed: false },
      }));
    }
    return nodes.map((n) => ({
      ...n,
      data: {
        ...n.data,
        focused: n.id === focusedNodeId,
        dimmed: !connectedIds.nodeIds.has(n.id),
      },
    }));
  }, [nodes, connectedIds, focusedNodeId]);

  // Apply focus styling to edges
  const styledEdges = useMemo(() => {
    if (!connectedIds) {
      return edges.map((e) => ({
        ...e,
        data: { ...e.data, dimmed: false, highlighted: false },
        animated: false,
      }));
    }
    return edges.map((e) => {
      const isConnected = connectedIds.edgeIds.has(e.id);
      return {
        ...e,
        data: { ...e.data, dimmed: !isConnected, highlighted: isConnected },
        animated: isConnected,
      };
    });
  }, [edges, connectedIds]);

  // Zoom to node when focusedNodeId changes externally (sidebar click)
  useEffect(() => {
    if (focusedNodeId) {
      const node = nodes.find((n) => n.id === focusedNodeId);
      if (node) {
        reactFlow.setCenter(
          node.position.x + NODE_WIDTH / 2,
          node.position.y + computeNodeHeight(node.data.columns) / 2,
          { zoom: 1.2, duration: 500 },
        );
      }
    }
  }, [focusedNodeId, nodes, reactFlow]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onNodeClick(node.id);
    },
    [onNodeClick],
  );

  const handleInit = useCallback(
    (instance: { fitView: () => void }) => {
      instance.fitView();
    },
    [],
  );

  return (
    <ReactFlow
      nodes={styledNodes}
      edges={styledEdges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      onNodeClick={handleNodeClick}
      onPaneClick={onPaneClick}
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
        nodeColor={(node: Node) => {
          const data = node.data as TableNodeData;
          return data.tableType === 'fact' ? GOLD : GREEN;
        }}
        maskColor="rgba(0,0,0,0.7)"
        style={{ backgroundColor: '#1A1A1E', borderRadius: 8, border: '1px solid #3A3A3E' }}
        pannable
        zoomable
      />
    </ReactFlow>
  );
}

/* ------------------------------------------------------------------ */
/*  Table list sidebar                                                 */
/* ------------------------------------------------------------------ */

interface TableSidebarProps {
  erdNodes: ERDNode[];
  focusedNodeId: string | null;
  onTableClick: (nodeId: string) => void;
}

function TableSidebar({ erdNodes, focusedNodeId, onTableClick }: TableSidebarProps): React.ReactNode {
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    if (!search.trim()) return erdNodes;
    const lower = search.toLowerCase();
    return erdNodes.filter((n) => n.name.toLowerCase().includes(lower));
  }, [erdNodes, search]);

  return (
    <Box
      sx={{
        width: SIDEBAR_WIDTH,
        flexShrink: 0,
        borderRight: 1,
        borderColor: '#3A3A3E',
        display: 'flex',
        flexDirection: 'column',
        bgcolor: '#1E1E22',
        overflow: 'hidden',
      }}
    >
      {/* Search */}
      <Box sx={{ px: 1, py: 1, borderBottom: '1px solid #3A3A3E' }}>
        <TextField
          size="small"
          placeholder="Search tables..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          fullWidth
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon sx={{ fontSize: 16, color: '#9E9E9E' }} />
                </InputAdornment>
              ),
              sx: {
                fontSize: '0.75rem',
                color: '#F5F5F5',
                bgcolor: '#252528',
                '& fieldset': { borderColor: '#3A3A3E' },
                '&:hover fieldset': { borderColor: '#555' },
                '&.Mui-focused fieldset': { borderColor: GOLD },
              },
            },
          }}
        />
      </Box>

      {/* Table list */}
      <Box sx={{ flex: 1, overflowY: 'auto', py: 0.5 }}>
        {filtered.map((n) => {
          const isFact = n.type === 'fact';
          const accent = isFact ? GOLD : GREEN;
          const isFocused = n.id === focusedNodeId;
          return (
            <Box
              key={n.id}
              onClick={() => onTableClick(n.id)}
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                px: 1,
                py: 0.6,
                cursor: 'pointer',
                bgcolor: isFocused ? '#2A2A2E' : 'transparent',
                borderLeft: `3px solid ${accent}`,
                '&:hover': { bgcolor: '#2A2A2E' },
                transition: 'background-color 0.15s ease',
              }}
            >
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography
                  variant="caption"
                  sx={{
                    color: isFocused ? GOLD : '#F5F5F5',
                    fontWeight: isFocused ? 700 : 400,
                    fontSize: '0.7rem',
                    display: 'block',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {n.name}
                </Typography>
                <Typography
                  sx={{
                    fontSize: '0.58rem',
                    color: '#9E9E9E',
                    lineHeight: 1.3,
                  }}
                >
                  {isFact ? 'Fact' : 'Dim'} &middot; {n.rowCount.toLocaleString()} rows
                </Typography>
              </Box>
            </Box>
          );
        })}
        {filtered.length === 0 && (
          <Typography
            variant="caption"
            sx={{ color: '#9E9E9E', px: 1.5, py: 2, display: 'block', textAlign: 'center' }}
          >
            No tables match
          </Typography>
        )}
      </Box>
    </Box>
  );
}

/* ------------------------------------------------------------------ */
/*  Panel component (outer wrapper)                                    */
/* ------------------------------------------------------------------ */

export function ERDDiagramPanel({
  open,
  onClose,
  erdData,
}: ERDDiagramPanelProps): React.ReactNode {
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);

  // Clear focus when panel closes or data changes
  useEffect(() => {
    if (!open) setFocusedNodeId(null);
  }, [open]);

  const handleNodeClick = useCallback((nodeId: string) => {
    setFocusedNodeId((prev) => (prev === nodeId ? null : nodeId));
  }, []);

  const handlePaneClick = useCallback(() => {
    setFocusedNodeId(null);
  }, []);

  const relationshipCount = erdData?.edges.length ?? 0;
  const tableCount = erdData?.nodes.length ?? 0;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      variant="temporary"
      slotProps={{
        paper: {
          sx: {
            width: DRAWER_WIDTH,
            bgcolor: 'background.default',
            borderLeft: 1,
            borderColor: 'divider',
          },
        },
      }}
    >
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
            ERD Diagram
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Legend */}
        <Box
          sx={{
            display: 'flex',
            gap: 3,
            px: 2.5,
            py: 1.5,
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Box
              sx={{
                width: 16,
                height: 12,
                borderLeft: `4px solid ${GOLD}`,
                bgcolor: 'rgba(212,168,67,0.15)',
                borderRadius: 0.5,
              }}
            />
            <Typography variant="caption" color="text.secondary">
              Fact Table
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Box
              sx={{
                width: 16,
                height: 12,
                borderLeft: `4px solid ${GREEN}`,
                bgcolor: 'rgba(76,175,80,0.15)',
                borderRadius: 0.5,
              }}
            />
            <Typography variant="caption" color="text.secondary">
              Dimension Table
            </Typography>
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
            Click a table to focus &middot; Click canvas to reset
          </Typography>
        </Box>

        {/* Main content: Sidebar + Canvas */}
        <Box sx={{ flex: 1, minHeight: 0, display: 'flex' }}>
          {erdData === null ? (
            <Box
              sx={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Typography variant="body2" color="text.secondary">
                No ERD data available.
              </Typography>
            </Box>
          ) : (
            <>
              <TableSidebar
                erdNodes={erdData.nodes}
                focusedNodeId={focusedNodeId}
                onTableClick={handleNodeClick}
              />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <ReactFlowProvider>
                  <ERDFlowCanvas
                    erdData={erdData}
                    focusedNodeId={focusedNodeId}
                    onNodeClick={handleNodeClick}
                    onPaneClick={handlePaneClick}
                  />
                </ReactFlowProvider>
              </Box>
            </>
          )}
        </Box>

        {/* Footer */}
        <Box
          sx={{
            px: 2.5,
            py: 1.5,
            borderTop: 1,
            borderColor: 'divider',
          }}
        >
          <Typography variant="caption" color="text.secondary">
            {tableCount} table{tableCount !== 1 ? 's' : ''} &middot;{' '}
            {relationshipCount} relationship{relationshipCount !== 1 ? 's' : ''} detected
          </Typography>
        </Box>
      </Box>
    </Drawer>
  );
}

export type { ERDColumn, ERDNode, ERDEdge, ERDDiagramPanelProps };
