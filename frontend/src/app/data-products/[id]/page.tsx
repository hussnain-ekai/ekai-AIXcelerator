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
import { ComponentErrorBoundary } from '@/components/ErrorBoundary';
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
import { DataCatalogViewer } from '@/components/panels/DataCatalogViewer';
import { BusinessGlossaryViewer } from '@/components/panels/BusinessGlossaryViewer';
import { MetricsViewer } from '@/components/panels/MetricsViewer';
import { ValidationRulesViewer } from '@/components/panels/ValidationRulesViewer';
import { LineageDiagramViewer } from '@/components/panels/LineageDiagramViewer';
import { useDataProduct } from '@/hooks/useDataProducts';
import { useAgent } from '@/hooks/useAgent';
import { useSessionRecovery } from '@/hooks/useSessionRecovery';
import { useArtifacts, useERDData, useQualityReport, useYAMLContent, useBRD, useDataDescription, useDataCatalog, useBusinessGlossary, useMetricsDefinitions, useValidationRules, useLineageData } from '@/hooks/useArtifacts';
import { useQueryClient } from '@tanstack/react-query';
import { ChatStoreProvider, useChatStore, useChatStoreApi } from '@/stores/chatStoreProvider';
import type { ArtifactType } from '@/stores/chatStore';

interface ChatWorkspacePageProps {
  params: Promise<{ id: string }>;
}

/** Map chatStore phase to ArtifactsPanel phase label. */
function phaseForArtifactType(type: ArtifactType): 'DISCOVERY' | 'REQUIREMENTS' | 'MODELING' | 'GENERATION' | 'VALIDATION' {
  switch (type) {
    case 'erd':
    case 'data_quality':
    case 'data_preview':
    case 'data_description':
      return 'DISCOVERY';
    case 'brd':
      return 'REQUIREMENTS';
    case 'data_catalog':
    case 'business_glossary':
    case 'metrics':
    case 'validation_rules':
    case 'lineage':
      return 'MODELING';
    case 'yaml':
      return 'GENERATION';
  }
}

/**
 * Outer wrapper: mounts a fresh ChatStoreProvider per data product.
 * When React unmounts this (navigating away), the store is destroyed.
 */
export default function ChatWorkspacePage({
  params,
}: ChatWorkspacePageProps): React.ReactNode {
  const { id } = use(params);
  return (
    <ChatStoreProvider key={id}>
      <ChatWorkspaceContent id={id} />
    </ChatStoreProvider>
  );
}

/**
 * Inner component: all hooks consume the scoped store via context.
 */
function ChatWorkspaceContent({ id }: { id: string }): React.ReactNode {
  const { data: dataProduct } = useDataProduct(id);
  const { isHydrated } = useSessionRecovery(dataProduct);
  const { sendMessage, retryMessage, interrupt, isConnected } = useAgent({ dataProductId: id });
  const storeApi = useChatStoreApi();
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
  const dataTier = useChatStore((state) => state.dataTier);
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
    const currentMessages = storeApi.getState().messages;
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
      data_catalog: 'data_catalog',
      business_glossary: 'business_glossary',
      metrics: 'metrics',
      validation_rules: 'validation_rules',
      lineage: 'lineage',
    };
    const TITLE_MAP: Record<string, string> = {
      erd: 'ERD Diagram',
      yaml: 'Semantic View YAML',
      brd: 'Business Requirements',
      quality_report: 'Data Quality Report',
      document: 'Data Preview',
      data_description: 'Data Description',
      data_catalog: 'Data Catalog',
      business_glossary: 'Business Glossary',
      metrics: 'Metrics & KPIs',
      validation_rules: 'Validation Rules',
      lineage: 'Data Lineage',
    };

    let hasDiscoveryArtifacts = false;
    for (const a of persistedArtifacts.data) {
      const mappedType = TYPE_MAP[a.artifact_type] ?? 'erd';
      if (mappedType === 'erd' || mappedType === 'data_quality' || mappedType === 'data_description') {
        hasDiscoveryArtifacts = true;
      }
      const alreadyExists = storeApi.getState().artifacts.some(
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
      const msgs = storeApi.getState().messages;
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
        storeApi.setState((state) => ({
          messages: [discoveryMsg, ...state.messages],
        }));
      }
    }
  }, [persistedArtifacts, addArtifact, addMessage, isHydrated, pipelineRunning, storeApi]);

  // Fetch detail data on-demand when panels open
  const { data: erdData } = useERDData(id, activePanel === 'erd');
  const { data: qualityReport } = useQualityReport(
    id,
    activePanel === 'data_quality',
  );
  const { data: yamlData } = useYAMLContent(id, activePanel === 'yaml');
  const { data: brdData, isLoading: brdLoading } = useBRD(id, activePanel === 'brd');
  const { data: dataDescriptionData, isLoading: dataDescriptionLoading } = useDataDescription(id, activePanel === 'data_description');
  const { data: dataCatalogData, isLoading: dataCatalogLoading } = useDataCatalog(id, activePanel === 'data_catalog');
  const { data: glossaryData, isLoading: glossaryLoading } = useBusinessGlossary(id, activePanel === 'business_glossary');
  const { data: metricsData, isLoading: metricsLoading } = useMetricsDefinitions(id, activePanel === 'metrics');
  const { data: validationRulesData, isLoading: validationRulesLoading } = useValidationRules(id, activePanel === 'validation_rules');
  const { data: lineageData, isLoading: lineageLoading } = useLineageData(id, activePanel === 'lineage');

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
      const msg = storeApi.getState().messages.find((m) => m.id === messageId);
      const originalContent = msg?.content ?? '';
      editMessage(messageId, newContent);
      truncateAfter(messageId);
      void retryMessage({ messageId, editedContent: newContent, originalContent });
    },
    [editMessage, truncateAfter, retryMessage, storeApi],
  );

  const handleRetryMessage = useCallback(
    (messageId: string) => {
      const msgs = storeApi.getState().messages;
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
    [truncateAfter, retryMessage, storeApi],
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
      <PhaseStepper currentPhase={currentPhase} dataTier={dataTier} />
      <Divider />

      {/* Message thread */}
      <ComponentErrorBoundary fallbackMessage="Chat messages failed to render.">
        <MessageThread
          messages={messages}
          isStreaming={isStreaming}
          onOpenArtifact={handleOpenPanel}
          onEditMessage={handleEditMessage}
          onRetryMessage={handleRetryMessage}
        />
      </ComponentErrorBoundary>

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
      <ComponentErrorBoundary fallbackMessage="ERD diagram failed to render.">
        <ERDDiagramPanel
          open={activePanel === 'erd'}
          onClose={handleClosePanel}
          erdData={erdData ?? null}
        />
      </ComponentErrorBoundary>

      {/* Data Quality Report detail panel */}
      <ComponentErrorBoundary fallbackMessage="Data quality report failed to render.">
        <DataQualityReport
          open={activePanel === 'data_quality'}
          onClose={handleClosePanel}
          report={qualityReport ?? null}
        />
      </ComponentErrorBoundary>

      {/* BRD viewer detail panel */}
      <ComponentErrorBoundary fallbackMessage="Business requirements failed to render.">
        <BRDViewer
          open={activePanel === 'brd'}
          onClose={handleClosePanel}
          brd={brdData ?? null}
          isLoading={brdLoading}
        />
      </ComponentErrorBoundary>

      {/* YAML viewer detail panel */}
      <ComponentErrorBoundary fallbackMessage="YAML viewer failed to render.">
        <YAMLViewer
          open={activePanel === 'yaml'}
          onClose={handleClosePanel}
          yaml={yamlData?.yaml_content ?? ''}
        />
      </ComponentErrorBoundary>

      {/* Data preview detail panel */}
      <ComponentErrorBoundary fallbackMessage="Data preview failed to render.">
        <DataPreview
          open={activePanel === 'data_preview'}
          onClose={handleClosePanel}
          data={null}
        />
      </ComponentErrorBoundary>

      {/* Data Description viewer panel */}
      <ComponentErrorBoundary fallbackMessage="Data description failed to render.">
        <DataDescriptionViewer
          open={activePanel === 'data_description'}
          onClose={handleClosePanel}
          dataDescription={dataDescriptionData ?? null}
          isLoading={dataDescriptionLoading}
        />
      </ComponentErrorBoundary>

      {/* Data Catalog viewer panel */}
      <ComponentErrorBoundary fallbackMessage="Data catalog failed to render.">
        <DataCatalogViewer
          open={activePanel === 'data_catalog'}
          onClose={handleClosePanel}
          data={dataCatalogData ?? null}
          isLoading={dataCatalogLoading}
        />
      </ComponentErrorBoundary>

      {/* Business Glossary viewer panel */}
      <ComponentErrorBoundary fallbackMessage="Business glossary failed to render.">
        <BusinessGlossaryViewer
          open={activePanel === 'business_glossary'}
          onClose={handleClosePanel}
          data={glossaryData ?? null}
          isLoading={glossaryLoading}
        />
      </ComponentErrorBoundary>

      {/* Metrics & KPIs viewer panel */}
      <ComponentErrorBoundary fallbackMessage="Metrics viewer failed to render.">
        <MetricsViewer
          open={activePanel === 'metrics'}
          onClose={handleClosePanel}
          data={metricsData ?? null}
          isLoading={metricsLoading}
        />
      </ComponentErrorBoundary>

      {/* Validation Rules viewer panel */}
      <ComponentErrorBoundary fallbackMessage="Validation rules failed to render.">
        <ValidationRulesViewer
          open={activePanel === 'validation_rules'}
          onClose={handleClosePanel}
          data={validationRulesData ?? null}
          isLoading={validationRulesLoading}
        />
      </ComponentErrorBoundary>

      {/* Lineage Diagram viewer panel */}
      <ComponentErrorBoundary fallbackMessage="Data lineage failed to render.">
        <LineageDiagramViewer
          open={activePanel === 'lineage'}
          onClose={handleClosePanel}
          data={lineageData ?? null}
          isLoading={lineageLoading}
        />
      </ComponentErrorBoundary>
    </Box>
  );
}
