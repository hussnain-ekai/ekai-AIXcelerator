'use client';

import { use, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Badge,
  Box,
  Breadcrumbs,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Link as MuiLink,
  Tooltip,
  Typography,
} from '@mui/material';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';
import FolderOutlinedIcon from '@mui/icons-material/FolderOutlined';
import TableChartOutlinedIcon from '@mui/icons-material/TableChartOutlined';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined';
import NextLink from 'next/link';
import { EditDataProductModal } from '@/components/dashboard/EditDataProductModal';
import { DeleteDataProductDialog } from '@/components/dashboard/DeleteDataProductDialog';
import { ComponentErrorBoundary } from '@/components/ErrorBoundary';
import { PhaseStepper } from '@/components/chat/PhaseStepper';
import { MessageThread } from '@/components/chat/MessageThread';
import { ChatInput } from '@/components/chat/ChatInput';
import { DataSourceSettingsPanel } from '@/components/dashboard/DataSourceSettingsPanel';
import { ArtifactsPanel } from '@/components/panels/ArtifactsPanel';
import type { Artifact as PanelArtifact } from '@/components/panels/ArtifactsPanel';
import { DocumentsPanel } from '@/components/panels/DocumentsPanel';
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
import {
  useApplyDocumentContext,
  useDeleteDocument,
  useDocumentContext,
  useDocuments,
  useReextractDocument,
  useUploadDocument,
  type ContextSelectionState,
  type MissionStep,
  type UploadedDocument,
} from '@/hooks/useDocuments';
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

function phaseToMissionStep(phase: string): MissionStep {
  const normalized = phase.toLowerCase();
  if (normalized === 'prepare' || normalized === 'transformation' || normalized === 'idle') {
    return 'discovery';
  }
  if (normalized === 'discovery') return 'discovery';
  if (normalized === 'requirements') return 'requirements';
  if (normalized === 'modeling') return 'modeling';
  if (normalized === 'generation') return 'generation';
  if (normalized === 'validation') return 'validation';
  if (normalized === 'publishing' || normalized === 'explorer') return 'publishing';
  return 'discovery';
}

function missionStepLabel(step: MissionStep): string {
  return `${step.charAt(0).toUpperCase()}${step.slice(1)}`;
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
  const router = useRouter();
  const { data: dataProduct } = useDataProduct(id);
  const { isHydrated } = useSessionRecovery(dataProduct);
  const { sendMessage, retryMessage, interrupt, pendingQueueCount } = useAgent({ dataProductId: id });
  const storeApi = useChatStoreApi();
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [rerunConfirmOpen, setRerunConfirmOpen] = useState(false);
  const [documentsUploadNotice, setDocumentsUploadNotice] = useState<string | null>(null);
  const [documentsUploadNoticeSeverity, setDocumentsUploadNoticeSeverity] = useState<'info' | 'error'>('info');
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
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [artifactsPanelOpen, setArtifactsPanelOpen] = useState(false);
  const discoveryTriggeredRef = useRef(false);
  const artifactsHydratedRef = useRef(false);

  // Load persisted artifacts from PostgreSQL on mount
  const { data: persistedArtifacts } = useArtifacts(id);
  const { data: documentsResponse } = useDocuments(id);
  const uploadDocument = useUploadDocument(id);
  const currentMissionStep = phaseToMissionStep(currentPhase);
  const { data: documentContextResponse } = useDocumentContext(id, currentMissionStep);
  const applyDocumentContext = useApplyDocumentContext(id);
  const deleteDocument = useDeleteDocument(id);
  const reextractDocument = useReextractDocument(id);
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
      data_quality: 'data_quality',
      document: 'data_preview',
      data_description: 'data_description',
      data_catalog: 'data_catalog',
      business_glossary: 'business_glossary',
      metrics: 'metrics',
      metrics_definitions: 'metrics',
      validation_rules: 'validation_rules',
      lineage: 'lineage',
    };
    const TITLE_MAP: Record<string, string> = {
      erd: 'ERD Diagram',
      yaml: 'Semantic View YAML',
      brd: 'Business Requirements',
      quality_report: 'Data Quality Report',
      data_quality: 'Data Quality Report',
      document: 'Data Preview',
      data_description: 'Data Description',
      data_catalog: 'Data Catalog',
      business_glossary: 'Business Glossary',
      metrics: 'Metrics & KPIs',
      metrics_definitions: 'Metrics Definitions',
      validation_rules: 'Validation Rules',
      lineage: 'Data Lineage',
    };

    // Find the most recent quality_report — marks the start of the current
    // pipeline run.  Non-discovery artifacts older than this are stale from a
    // previous run and should not appear in the panel.
    const DISCOVERY_DB_TYPES = new Set(['quality_report', 'erd', 'data_description']);
    let currentRunCutoff = 0;
    for (const a of persistedArtifacts.data) {
      if (a.artifact_type === 'quality_report') {
        const t = new Date(a.created_at).getTime();
        if (t > currentRunCutoff) currentRunCutoff = t;
      }
    }

    let hasDiscoveryArtifacts = false;
    for (const a of persistedArtifacts.data) {
      const mappedType = TYPE_MAP[a.artifact_type];
      if (!mappedType) continue; // Skip unknown artifact types

      // Skip stale artifacts from previous pipeline runs
      if (currentRunCutoff > 0 && !DISCOVERY_DB_TYPES.has(a.artifact_type)) {
        if (new Date(a.created_at).getTime() < currentRunCutoff) continue;
      }

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

  const handleConfirmRerunDiscovery = useCallback(() => {
    setRerunConfirmOpen(false);
    handleRerunDiscovery();
  }, [handleRerunDiscovery]);

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
  const documentCount = documentsResponse?.data.length ?? 0;
  const artifactCount = artifacts.length;

  const handleUploadDocuments = useCallback(
    (files: File[]) => {
      setDocumentsUploadNotice(null);
      setDocumentsUploadNoticeSeverity('info');

      void (async () => {
        const results = await Promise.allSettled(
          files.map((file) => uploadDocument.mutateAsync(file)),
        );

        const failures = results.filter(
          (result): result is PromiseRejectedResult => result.status === 'rejected',
        );

        if (failures.length === 0) {
          if (files.length > 1) {
            setDocumentsUploadNotice(`${files.length} files uploaded successfully.`);
            setDocumentsUploadNoticeSeverity('info');
          }
          return;
        }

        const firstError = failures[0]?.reason;
        const firstErrorMessage =
          firstError instanceof Error && firstError.message.trim().length > 0
            ? firstError.message
            : 'Upload failed';
        const failureSummary =
          failures.length === files.length
            ? `Upload failed: ${firstErrorMessage}`
            : `${files.length - failures.length}/${files.length} files uploaded. ${failures.length} failed: ${firstErrorMessage}`;

        setDocumentsUploadNotice(failureSummary);
        setDocumentsUploadNoticeSeverity('error');

        addMessage({
          id: crypto.randomUUID(),
          role: 'system',
          content: failureSummary,
          timestamp: new Date().toISOString(),
        });
      })();
    },
    [uploadDocument, addMessage],
  );

  const contextByDocumentId = useMemo(() => {
    const lookup: Record<string, { evidenceId: string; state: ContextSelectionState }> = {};
    const perStep = documentContextResponse?.step?.[currentMissionStep];
    if (!perStep) return lookup;

    const addItems = (
      state: ContextSelectionState,
      items: Array<{ evidence_id: string; document: { id: string } }>,
    ) => {
      for (const item of items) {
        if (!lookup[item.document.id]) {
          lookup[item.document.id] = {
            evidenceId: item.evidence_id,
            state,
          };
        }
      }
    };

    addItems('active', perStep.active ?? []);
    addItems('candidate', perStep.candidate ?? []);
    addItems('reference', perStep.reference ?? []);
    addItems('excluded', perStep.excluded ?? []);

    return lookup;
  }, [documentContextResponse, currentMissionStep]);

  const handleSetDocumentState = useCallback(
    (documentId: string, state: ContextSelectionState) => {
      const entry = contextByDocumentId[documentId];
      if (!entry) return;

      void applyDocumentContext
        .mutateAsync({
          step: currentMissionStep,
          reason: 'documents_panel_update',
          updates: [{ evidence_id: entry.evidenceId, state }],
        })
        .catch(() => undefined);
    },
    [contextByDocumentId, applyDocumentContext, currentMissionStep],
  );

  const handleDeleteDocument = useCallback(
    (document: UploadedDocument) => {
      const filename = document.filename || 'document';
      void deleteDocument
        .mutateAsync(document.id)
        .then((result) => {
          const impactedSteps =
            result.impacted_steps.length > 0
              ? ` Impacted steps: ${result.impacted_steps.map((step) => missionStepLabel(step as MissionStep)).join(', ')}.`
              : '';
          const recommendedActions =
            result.recommended_actions.length > 0
              ? ` Recovery plan: ${result.recommended_actions.join(' ')}`
              : '';
          const summary = `Deleted "${filename}".${impactedSteps}${recommendedActions}`;

          setDocumentsUploadNotice(summary);
          setDocumentsUploadNoticeSeverity('info');

          if (result.impacted_steps.length > 0) {
            addMessage({
              id: crypto.randomUUID(),
              role: 'system',
              content: summary,
              timestamp: new Date().toISOString(),
            });
          }
        })
        .catch((error) => {
          const message =
            error instanceof Error && error.message.trim().length > 0
              ? error.message
              : `Failed to delete "${filename}"`;
          setDocumentsUploadNotice(message);
          setDocumentsUploadNoticeSeverity('error');
        });
    },
    [deleteDocument, addMessage],
  );

  const handleReextractDocument = useCallback(
    (document: UploadedDocument) => {
      const filename = document.filename || 'document';
      void reextractDocument
        .mutateAsync(document.id)
        .then((result) => {
          if (result.status === 'completed') {
            const message = `Extraction completed for "${filename}" (${result.extracted_chars ?? 0} chars).`;
            setDocumentsUploadNotice(message);
            setDocumentsUploadNoticeSeverity('info');
            return;
          }

          if (result.status === 'pending') {
            const message = result.message
              ? `Extraction update for "${filename}": ${result.message}`
              : `Extraction for "${filename}" is still pending.`;
            setDocumentsUploadNotice(message);
            setDocumentsUploadNoticeSeverity('info');
            return;
          }

          const message = result.message
            ? `Extraction failed for "${filename}": ${result.message}`
            : `Extraction failed for "${filename}".`;
          setDocumentsUploadNotice(message);
          setDocumentsUploadNoticeSeverity('error');
          addMessage({
            id: crypto.randomUUID(),
            role: 'system',
            content: message,
            timestamp: new Date().toISOString(),
          });
        })
        .catch((error) => {
          const message =
            error instanceof Error && error.message.trim().length > 0
              ? error.message
              : `Failed to re-run extraction for "${filename}"`;
          setDocumentsUploadNotice(message);
          setDocumentsUploadNoticeSeverity('error');
        });
    },
    [reextractDocument, addMessage],
  );

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
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
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
          <Tooltip title="Edit name & description">
            <IconButton size="small" onClick={() => setEditOpen(true)}>
              <EditOutlinedIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Delete data product">
            <IconButton size="small" onClick={() => setDeleteOpen(true)}>
              <DeleteOutlinedIcon sx={{ fontSize: 16, color: 'error.main' }} />
            </IconButton>
          </Tooltip>
          {dataProduct?.description && (
            <Typography
              variant="caption"
              sx={{ color: 'text.secondary', ml: 1, maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              title={dataProduct.description}
            >
              {dataProduct.description}
            </Typography>
          )}
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="outlined"
            size="small"
            startIcon={<RestartAltIcon />}
            onClick={() => setRerunConfirmOpen(true)}
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
            badgeContent={documentCount}
            color="primary"
            invisible={documentCount === 0}
          >
            <Button
              variant="outlined"
              size="small"
              startIcon={<FolderOutlinedIcon />}
              onClick={() => setDocumentsOpen(true)}
              sx={{ borderColor: 'divider' }}
            >
              Documents
            </Button>
          </Badge>
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
        disabled={!dataProduct}
        isStreaming={isStreaming}
        pendingQueueCount={pendingQueueCount}
      />

      {/* Data source settings slide-over */}
      {dataProduct && (
        <DataSourceSettingsPanel
          open={tablesOpen}
          onClose={() => setTablesOpen(false)}
          dataProduct={dataProduct}
        />
      )}

      <DocumentsPanel
        open={documentsOpen}
        onClose={() => setDocumentsOpen(false)}
        documents={documentsResponse?.data ?? []}
        onUploadFiles={handleUploadDocuments}
        isUploading={uploadDocument.isPending}
        uploadNotice={documentsUploadNotice}
        uploadNoticeSeverity={documentsUploadNoticeSeverity}
        currentStep={missionStepLabel(currentMissionStep)}
        contextByDocumentId={contextByDocumentId}
        onSetDocumentState={handleSetDocumentState}
        onDeleteDocument={handleDeleteDocument}
        onReextractDocument={handleReextractDocument}
        isUpdatingContext={applyDocumentContext.isPending}
        isDeleting={deleteDocument.isPending}
        isReextracting={reextractDocument.isPending}
      />

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

      {/* Edit data product modal */}
      {dataProduct && (
        <EditDataProductModal
          open={editOpen}
          onClose={() => setEditOpen(false)}
          product={dataProduct}
        />
      )}

      {/* Delete data product dialog */}
      {dataProduct && (
        <DeleteDataProductDialog
          open={deleteOpen}
          onClose={() => setDeleteOpen(false)}
          product={dataProduct}
          onDeleted={() => router.push('/data-products')}
        />
      )}

      <Dialog
        open={rerunConfirmOpen}
        onClose={() => setRerunConfirmOpen(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Re-run Discovery?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            This restarts discovery from scratch and clears current chat progress and generated downstream artifacts.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRerunConfirmOpen(false)} color="inherit">
            Cancel
          </Button>
          <Button onClick={handleConfirmRerunDiscovery} color="warning" variant="contained">
            Re-run Discovery
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
