'use client';

import { use, useCallback, useEffect, useRef, useState } from 'react';
import {
  Badge,
  Box,
  Breadcrumbs,
  Button,
  Divider,
  Link as MuiLink,
  Typography,
} from '@mui/material';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';
import TableChartOutlinedIcon from '@mui/icons-material/TableChartOutlined';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import NextLink from 'next/link';
import { PhaseStepper } from '@/components/chat/PhaseStepper';
import { MessageThread } from '@/components/chat/MessageThread';
import { ChatInput } from '@/components/chat/ChatInput';
import { DataSourceSettingsPanel } from '@/components/dashboard/DataSourceSettingsPanel';
import { ArtifactsPanel } from '@/components/panels/ArtifactsPanel';
import type { Artifact as PanelArtifact } from '@/components/panels/ArtifactsPanel';
import { ERDDiagramPanel } from '@/components/panels/ERDDiagramPanel';
import { DataQualityReport } from '@/components/panels/DataQualityReport';
import { YAMLViewer } from '@/components/panels/YAMLViewer';
import { DataPreview } from '@/components/panels/DataPreview';
import { useDataProduct } from '@/hooks/useDataProducts';
import { useAgent } from '@/hooks/useAgent';
import { useSessionRecovery } from '@/hooks/useSessionRecovery';
import { useArtifacts, useERDData, useQualityReport, useYAMLContent } from '@/hooks/useArtifacts';
import { useChatStore } from '@/stores/chatStore';
import type { ArtifactType } from '@/stores/chatStore';

interface ChatWorkspacePageProps {
  params: Promise<{ id: string }>;
}

/** Map chatStore phase to ArtifactsPanel phase label. */
function phaseForArtifactType(type: ArtifactType): 'DISCOVERY' | 'REQUIREMENTS' | 'GENERATION' | 'VALIDATION' {
  switch (type) {
    case 'erd':
    case 'data_quality':
    case 'data_preview':
      return 'DISCOVERY';
    case 'brd':
      return 'REQUIREMENTS';
    case 'yaml':
      return 'GENERATION';
  }
}

export default function ChatWorkspacePage({
  params,
}: ChatWorkspacePageProps): React.ReactNode {
  const { id } = use(params);
  const { data: dataProduct } = useDataProduct(id);
  const { isHydrated } = useSessionRecovery(dataProduct);
  const { sendMessage, isConnected } = useAgent({ dataProductId: id });
  const messages = useChatStore((state) => state.messages);
  const isStreaming = useChatStore((state) => state.isStreaming);
  const currentPhase = useChatStore((state) => state.currentPhase);
  const clearMessages = useChatStore((state) => state.clearMessages);
  const setHydrated = useChatStore((state) => state.setHydrated);
  const artifacts = useChatStore((state) => state.artifacts);
  const activePanel = useChatStore((state) => state.activePanel);
  const setActivePanel = useChatStore((state) => state.setActivePanel);
  const addArtifact = useChatStore((state) => state.addArtifact);
  const [tablesOpen, setTablesOpen] = useState(false);
  const [artifactsPanelOpen, setArtifactsPanelOpen] = useState(false);
  const discoveryTriggeredRef = useRef(false);
  const artifactsHydratedRef = useRef(false);

  // Load persisted artifacts from PostgreSQL on mount
  const { data: persistedArtifacts } = useArtifacts(id);
  const addMessage = useChatStore((state) => state.addMessage);

  useEffect(() => {
    // Wait for BOTH artifacts to load AND session recovery to finish
    // so we don't inject messages that get overwritten by hydrateFromHistory
    if (artifactsHydratedRef.current || !persistedArtifacts?.data || !isHydrated) return;
    artifactsHydratedRef.current = true;

    const TYPE_MAP: Record<string, ArtifactType> = {
      erd: 'erd',
      yaml: 'yaml',
      brd: 'brd',
      quality_report: 'data_quality',
      document: 'data_preview',
    };
    const TITLE_MAP: Record<string, string> = {
      erd: 'ERD Diagram',
      yaml: 'Semantic View YAML',
      brd: 'Business Requirements',
      quality_report: 'Data Quality Report',
      document: 'Data Preview',
    };

    let hasDiscoveryArtifacts = false;
    for (const a of persistedArtifacts.data) {
      const mappedType = TYPE_MAP[a.artifact_type] ?? 'erd';
      if (mappedType === 'erd' || mappedType === 'data_quality') {
        hasDiscoveryArtifacts = true;
      }
      const alreadyExists = useChatStore.getState().artifacts.some(
        (existing) => existing.id === a.id,
      );
      if (!alreadyExists) {
        addArtifact({
          id: a.id,
          type: mappedType,
          title: TITLE_MAP[a.artifact_type] ?? a.artifact_type,
          dataProductId: a.data_product_id,
          createdAt: a.created_at,
          version: a.version,
        });
      }
    }

    // On page reload, if discovery artifacts exist but no discovery-complete
    // message is in the thread, prepend one so artifact cards always appear first
    if (hasDiscoveryArtifacts) {
      const msgs = useChatStore.getState().messages;
      const hasDiscoveryMsg = msgs.some((m) => m.artifactRefs && m.artifactRefs.length > 0);
      if (!hasDiscoveryMsg) {
        const discoveryMsg = {
          id: crypto.randomUUID(),
          role: 'assistant' as const,
          content: "I've reviewed your data tables, mapped the relationships between them, and checked the overall data quality.",
          timestamp: new Date(Date.now() - 1000).toISOString(),
          artifactRefs: ['erd' as const, 'data_quality' as const],
        };
        useChatStore.setState((state) => ({
          messages: [discoveryMsg, ...state.messages],
        }));
      }
    }
  }, [persistedArtifacts, addArtifact, addMessage, isHydrated]);

  // Fetch detail data on-demand when panels open
  const { data: erdData } = useERDData(id, activePanel === 'erd');
  const { data: qualityReport } = useQualityReport(
    id,
    activePanel === 'data_quality',
  );
  const { data: yamlData } = useYAMLContent(id, activePanel === 'yaml');

  const handleSendMessage = useCallback(
    (content: string) => {
      void sendMessage(content);
    },
    [sendMessage],
  );

  const handleStartDiscovery = useCallback(() => {
    void sendMessage('__START_DISCOVERY__');
  }, [sendMessage]);

  const handleRerunDiscovery = useCallback(() => {
    clearMessages();
    discoveryTriggeredRef.current = false;
    // clearMessages resets isHydrated to false; re-enable so auto-trigger fires
    setHydrated(true);
  }, [clearMessages, setHydrated]);

  // Auto-trigger discovery when no messages and data product exists
  // Wait for hydration to complete before deciding - prevents re-triggering on navigation
  useEffect(() => {
    if (
      !discoveryTriggeredRef.current &&
      isHydrated &&
      messages.length === 0 &&
      dataProduct &&
      dataProduct.tables &&
      dataProduct.tables.length > 0 &&
      !isStreaming
    ) {
      discoveryTriggeredRef.current = true;
      handleStartDiscovery();
    }
  }, [isHydrated, messages.length, dataProduct, isStreaming, handleStartDiscovery]);

  // Quality modal is now disabled - users can view quality report via Artifacts panel
  // The modal was intrusive and the report is better viewed in the dedicated panel

  const handleArtifactClick = useCallback(
    (artifact: PanelArtifact) => {
      setActivePanel(artifact.type as ArtifactType);
      setArtifactsPanelOpen(false);
    },
    [setActivePanel],
  );

  const handleOpenPanel = useCallback(
    (type: ArtifactType) => {
      setActivePanel(type);
    },
    [setActivePanel],
  );

  const handleClosePanel = useCallback(() => {
    setActivePanel(null);
  }, [setActivePanel]);

  // Convert chatStore artifacts to ArtifactsPanel format
  const panelArtifacts: PanelArtifact[] = artifacts.map((a) => ({
    id: a.id,
    type: a.type,
    title: a.title,
    phase: phaseForArtifactType(a.type),
    createdAt: a.createdAt,
    version: a.version,
  }));

  const productName = dataProduct?.name ?? 'Data Product';
  const tableCount = dataProduct?.tables?.length ?? 0;
  const artifactCount = artifacts.length;

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: 'calc(100vh - 48px)',
      }}
    >
      {/* Top bar */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 3,
          py: 1.5,
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Breadcrumbs>
          <MuiLink
            component={NextLink}
            href="/data-products"
            underline="hover"
            color="text.secondary"
            sx={{ fontSize: '0.875rem' }}
          >
            Data Products
          </MuiLink>
          <Typography
            variant="body2"
            sx={{ color: 'primary.main', fontWeight: 600 }}
          >
            {productName}
          </Typography>
        </Breadcrumbs>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="outlined"
            size="small"
            startIcon={<RestartAltIcon />}
            onClick={handleRerunDiscovery}
            disabled={isStreaming}
            sx={{ borderColor: 'divider' }}
          >
            Re-run Discovery
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<TableChartOutlinedIcon />}
            onClick={() => setTablesOpen(true)}
            sx={{ borderColor: 'divider' }}
          >
            Tables{tableCount > 0 ? ` (${tableCount})` : ''}
          </Button>
          <Badge
            badgeContent={artifactCount}
            color="primary"
            invisible={artifactCount === 0}
          >
            <Button
              variant="outlined"
              size="small"
              startIcon={<FolderOpenIcon />}
              onClick={() => setArtifactsPanelOpen(true)}
              sx={{ borderColor: 'divider' }}
            >
              Artifacts
            </Button>
          </Badge>
        </Box>
      </Box>

      {/* Phase stepper */}
      <Divider />
      <PhaseStepper currentPhase={currentPhase} />
      <Divider />

      {/* Message thread */}
      <MessageThread
        messages={messages}
        isStreaming={isStreaming}
        onOpenArtifact={handleOpenPanel}
      />

      {/* Chat input */}
      <ChatInput
        onSend={handleSendMessage}
        disabled={isStreaming || isConnected}
      />

      {/* Data source settings slide-over */}
      {dataProduct && (
        <DataSourceSettingsPanel
          open={tablesOpen}
          onClose={() => setTablesOpen(false)}
          dataProduct={dataProduct}
        />
      )}

      {/* Artifacts list panel */}
      <ArtifactsPanel
        open={artifactsPanelOpen}
        onClose={() => setArtifactsPanelOpen(false)}
        artifacts={panelArtifacts}
        onArtifactClick={handleArtifactClick}
      />

      {/* ERD detail panel */}
      <ERDDiagramPanel
        open={activePanel === 'erd'}
        onClose={handleClosePanel}
        erdData={erdData ?? null}
      />

      {/* Data Quality Report detail panel */}
      <DataQualityReport
        open={activePanel === 'data_quality'}
        onClose={handleClosePanel}
        report={qualityReport ?? null}
      />

      {/* YAML viewer detail panel */}
      <YAMLViewer
        open={activePanel === 'yaml'}
        onClose={handleClosePanel}
        yaml={yamlData?.yaml ?? ''}
      />

      {/* Data preview detail panel */}
      <DataPreview
        open={activePanel === 'data_preview'}
        onClose={handleClosePanel}
        data={null}
      />
    </Box>
  );
}
