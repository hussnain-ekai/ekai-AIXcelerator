import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { ERDNode, ERDEdge } from '@/components/panels/ERDDiagramPanel';
import type { QualityReport } from '@/components/panels/DataQualityReport';
import type { BRDResponse } from '@/components/panels/BRDViewer';
import type { DataDescriptionResponse } from '@/components/panels/DataDescriptionViewer';
import type { DataCatalogResponse } from '@/components/panels/DataCatalogViewer';
import type { BusinessGlossaryResponse } from '@/components/panels/BusinessGlossaryViewer';
import type { MetricsResponse } from '@/components/panels/MetricsViewer';
import type { ValidationRulesResponse } from '@/components/panels/ValidationRulesViewer';

interface Artifact {
  id: string;
  data_product_id: string;
  artifact_type: 'erd' | 'yaml' | 'brd' | 'quality_report' | 'document' | 'export' | 'data_description' | 'data_catalog' | 'business_glossary' | 'metrics' | 'validation_rules' | 'lineage';
  version?: number;
  filename?: string;
  file_size_bytes?: number;
  content_type?: string;
  metadata?: Record<string, unknown>;
  download_url?: string;
  created_at: string;
}

interface ArtifactsResponse {
  data: Artifact[];
}

interface ERDResponse {
  nodes: ERDNode[];
  edges: ERDEdge[];
}

function useArtifacts(dataProductId: string | null) {
  return useQuery<ArtifactsResponse>({
    queryKey: ['artifacts', dataProductId],
    queryFn: () =>
      api.get<ArtifactsResponse>(`/artifacts/${dataProductId}`),
    enabled: dataProductId !== null && dataProductId.length > 0,
  });
}

function useERDData(dataProductId: string | null, enabled = false) {
  return useQuery<ERDResponse>({
    queryKey: ['artifacts', 'erd', dataProductId],
    queryFn: () =>
      api.get<ERDResponse>(`/artifacts/${dataProductId}/erd`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

function useQualityReport(dataProductId: string | null, enabled = false) {
  return useQuery<QualityReport>({
    queryKey: ['artifacts', 'quality-report', dataProductId],
    queryFn: () =>
      api.get<QualityReport>(`/artifacts/${dataProductId}/quality-report`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

interface SemanticViewRow {
  yaml_content: string;
  version?: number;
  validation_status?: string;
}

function useYAMLContent(dataProductId: string | null, enabled = false) {
  return useQuery<SemanticViewRow>({
    queryKey: ['artifacts', 'yaml', dataProductId],
    queryFn: () =>
      api.get<SemanticViewRow>(`/artifacts/${dataProductId}/yaml`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

function useBRD(dataProductId: string | null, enabled = false) {
  return useQuery<BRDResponse>({
    queryKey: ['artifacts', 'brd', dataProductId],
    queryFn: () =>
      api.get<BRDResponse>(`/artifacts/${dataProductId}/brd`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

function useDataDescription(dataProductId: string | null, enabled = false) {
  return useQuery<DataDescriptionResponse>({
    queryKey: ['artifacts', 'data-description', dataProductId],
    queryFn: () =>
      api.get<DataDescriptionResponse>(`/artifacts/${dataProductId}/data-description`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

function useDataCatalog(dataProductId: string | null, enabled = false) {
  return useQuery<DataCatalogResponse>({
    queryKey: ['artifacts', 'data-catalog', dataProductId],
    queryFn: () =>
      api.get<DataCatalogResponse>(`/artifacts/${dataProductId}/data-catalog`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

function useBusinessGlossary(dataProductId: string | null, enabled = false) {
  return useQuery<BusinessGlossaryResponse>({
    queryKey: ['artifacts', 'business-glossary', dataProductId],
    queryFn: () =>
      api.get<BusinessGlossaryResponse>(`/artifacts/${dataProductId}/business-glossary`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

function useMetricsDefinitions(dataProductId: string | null, enabled = false) {
  return useQuery<MetricsResponse>({
    queryKey: ['artifacts', 'metrics-definitions', dataProductId],
    queryFn: () =>
      api.get<MetricsResponse>(`/artifacts/${dataProductId}/metrics-definitions`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

function useValidationRules(dataProductId: string | null, enabled = false) {
  return useQuery<ValidationRulesResponse>({
    queryKey: ['artifacts', 'validation-rules', dataProductId],
    queryFn: () =>
      api.get<ValidationRulesResponse>(`/artifacts/${dataProductId}/validation-rules`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

interface LineageNode {
  fqn: string;
  layer: 'source' | 'silver' | 'gold';
  tableType: string;
}

interface LineageEdge {
  id: string;
  source: string;
  target: string;
  relType: string;
}

interface LineageResponse {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

function useLineageData(dataProductId: string | null, enabled = false) {
  return useQuery<LineageResponse>({
    queryKey: ['artifacts', 'lineage', dataProductId],
    queryFn: () =>
      api.get<LineageResponse>(`/artifacts/${dataProductId}/lineage`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

export { useArtifacts, useERDData, useQualityReport, useYAMLContent, useBRD, useDataDescription, useDataCatalog, useBusinessGlossary, useMetricsDefinitions, useValidationRules, useLineageData };
export type { Artifact, ArtifactsResponse, ERDResponse, LineageNode, LineageEdge, LineageResponse };
