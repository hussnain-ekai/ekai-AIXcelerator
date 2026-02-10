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
import { BRDViewer } from '@/components/panels/BRDViewer';
import { DataPreview } from '@/components/panels/DataPreview';
import { DataDescriptionViewer } from '@/components/panels/DataDescriptionViewer';
import { useDataProduct } from '@/hooks/useDataProducts';
import { useAgent } from '@/hooks/useAgent';
import { useSessionRecovery } from '@/hooks/useSessionRecovery';
import { useArtifacts, useERDData, useQualityReport, useYAMLContent, useBRD, useDataDescription } from '@/hooks/useArtifacts';
import { useQueryClient } from '@tanstack/react-query';
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
    case 'data_description':
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
  const { sendMessage, retryMessage, interrupt, isConnected } = useAgent({ dataProductId: id });
  const truncateAfter = useChatStore((state) => state.truncateAfter);
  const editMessage = useChatStore((state) => state.editMessage);
  const messages = useChatStore((state) => state.messages);
  const isStreaming = useChatStore((state) => state.isStreaming);
  const currentPhase = useChatStore((state) => state.currentPhase);
  const clearMessages = useChatStore((state) => state.clearMessages);
  const setHydrated = useChatStore((state) => state.setHydrated);
  const artifacts = useChatStore((state) => state.artifacts);
  const activePanel = useChatStore((state) => state.activePanel);
  const setActivePanel = useChatStore((state) => state.setActivePanel);
  const addArtifact = useChatStore((state) => state.addArtifact);
  const queryClient = useQueryClient();
  const [tablesOpen, setTablesOpen] = useState(false);
  const [artifactsPanelOpen, setArtifactsPanelOpen] = useState(false);
  const discoveryTriggeredRef = useRef(false);
  const artifactsHydratedRef = useRef(false);

  // Load persisted artifacts from PostgreSQL on mount
  const { data: persistedArtifacts } = useArtifacts(id);
  const addMessage = useChatStore((state) => state.addMessage);

  const pipelineRunning = useChatStore((state) => state.pipelineRunning);

  useEffect(() => {
    // Hydrate artifacts from PostgreSQL ONLY on page reload with existing conversation.
    // Two guards prevent loading stale artifacts during a fresh/re-run discovery:
    //   1. messages.length === 0: blocks before auto-trigger fires (same render cycle race)
    //   2. discoveryTriggeredRef: blocks after pipeline adds completion message
    // On page reload with recovered messages, both guards are false → hydration proceeds.
    const currentMessages = useChatStore.getState().messages;
    if (
      artifactsHydratedRef.current ||
      !persistedArtifacts?.data ||
      !isHydrated ||
      pipelineRunning ||
      discoveryTriggeredRef.current ||
      currentMessages.length === 0
    ) return;
    artifactsHydratedRef.current = true;

    const TYPE_MAP: Record<string, ArtifactType> = {
      erd: 'erd',
      yaml: 'yaml',
      brd: 'brd',
      quality_report: 'data_quality',
      document: 'data_preview',
      data_description: 'data_description',
    };
    const TITLE_MAP: Record<string, string> = {
      erd: 'ERD Diagram',
      yaml: 'Semantic View YAML',
      brd: 'Business Requirements',
      quality_report: 'Data Quality Report',
      document: 'Data Preview',
      data_description: 'Data Description',
    };

    let hasDiscoveryArtifacts = false;
    for (const a of persistedArtifacts.data) {
      const mappedType = TYPE_MAP[a.artifact_type] ?? 'erd';
      if (mappedType === 'erd' || mappedType === 'data_quality' || mappedType === 'data_description') {
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
      const discoveryText = "I've analyzed your data tables and checked the overall data quality.";
      const hasDiscoveryMsg = msgs.some((m) => m.content === discoveryText || (m.artifactRefs && m.artifactRefs.length > 0));
      if (!hasDiscoveryMsg) {
        const discoveryMsg = {
          id: crypto.randomUUID(),
          role: 'assistant' as const,
          content: "I've analyzed your data tables and checked the overall data quality.",
          timestamp: new Date(Date.now() - 1000).toISOString(),
          artifactRefs: ['data_quality' as const],
        };
        useChatStore.setState((state) => ({
          messages: [discoveryMsg, ...state.messages],
        }));
      }
    }
  }, [persistedArtifacts, addArtifact, addMessage, isHydrated, pipelineRunning]);

  // Fetch detail data on-demand when panels open
  const { data: erdData } = useERDData(id, activePanel === 'erd');
  const { data: qualityReport } = useQualityReport(
    id,
    activePanel === 'data_quality',
  );
  const { data: yamlData } = useYAMLContent(id, activePanel === 'yaml');
  const { data: brdData, isLoading: brdLoading } = useBRD(id, activePanel === 'brd');
  const { data: dataDescriptionData, isLoading: dataDescriptionLoading } = useDataDescription(id, activePanel === 'data_description');

  const handleSendMessage = useCallback(
    (content: string, files?: File[]) => {
      void sendMessage(content, files);
    },
    [sendMessage],
  );

  const handleStop = useCallback(() => {
    void interrupt();
  }, [interrupt]);

  const handleEditMessage = useCallback(
    (messageId: string, newContent: string) => {
      // Capture original content BEFORE editing the store
      const msg = useChatStore.getState().messages.find((m) => m.id === messageId);
      const originalContent = msg?.content ?? '';
      editMessage(messageId, newContent);
      truncateAfter(messageId);
      void retryMessage({ messageId, editedContent: newContent, originalContent });
    },
    [editMessage, truncateAfter, retryMessage],
  );

  const handleRetryMessage = useCallback(
    (messageId: string) => {
      const msgs = useChatStore.getState().messages;
      const msgIndex = msgs.findIndex((m) => m.id === messageId);
      const msg = msgs[msgIndex];
      if (!msg) return;

      if (msg.role === 'user') {
        const originalContent = msg.content;
        truncateAfter(messageId);
        void retryMessage({ messageId, originalContent });
      } else if (msg.role === 'assistant') {
        // Find the preceding user message and retry from there
        const prevUser = msgs.slice(0, msgIndex).reverse().find((m) => m.role === 'user');
        if (prevUser) {
          const originalContent = prevUser.content;
          truncateAfter(prevUser.id);
          void retryMessage({ messageId: prevUser.id, originalContent });
        }
      }
    },
    [truncateAfter, retryMessage],
  );

  const handleStartDiscovery = useCallback(() => {
    void sendMessage('__START_DISCOVERY__');
  }, [sendMessage]);

  const setPipelineRunningAction = useChatStore((state) => state.setPipelineRunning);
  const handleRerunDiscovery = useCallback(() => {
    clearMessages();
    discoveryTriggeredRef.current = true; // prevent auto-trigger from also firing
    artifactsHydratedRef.current = false;
    setPipelineRunningAction(true); // Block artifact hydration immediately
    // Invalidate React Query artifact caches so stale BRD/YAML/ERD don't persist
    void queryClient.invalidateQueries({ queryKey: ['artifacts', id] });
    // Send re-run trigger directly — bypasses cache in the pipeline
    void sendMessage('__RERUN_DISCOVERY__');
  }, [clearMessages, sendMessage, setPipelineRunningAction, queryClient, id]);

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
        {dataProduct?.description && (
          <Typography
            variant="caption"
            sx={{ color: 'text.secondary', ml: 2, maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={dataProduct.description}
          >
            {dataProduct.description}
          </Typography>
        )}
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
        onEditMessage={handleEditMessage}
        onRetryMessage={handleRetryMessage}
      />

      {/* Chat input */}
      <ChatInput
        onSend={handleSendMessage}
        onStop={handleStop}
        disabled={isStreaming || isConnected}
        isStreaming={isStreaming}
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

      {/* BRD viewer detail panel */}
      <BRDViewer
        open={activePanel === 'brd'}
        onClose={handleClosePanel}
        brd={brdData ?? null}
        isLoading={brdLoading}
      />

      {/* YAML viewer detail panel */}
      <YAMLViewer
        open={activePanel === 'yaml'}
        onClose={handleClosePanel}
        yaml={yamlData?.yaml_content ?? ''}
      />

      {/* Data preview detail panel */}
      <DataPreview
        open={activePanel === 'data_preview'}
        onClose={handleClosePanel}
        data={null}
      />

      {/* Data Description viewer panel */}
      <DataDescriptionViewer
        open={activePanel === 'data_description'}
        onClose={handleClosePanel}
        dataDescription={dataDescriptionData ?? null}
        isLoading={dataDescriptionLoading}
      />
    </Box>
  );
}
