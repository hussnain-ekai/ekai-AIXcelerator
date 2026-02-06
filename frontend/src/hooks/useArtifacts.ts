import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { ERDNode, ERDEdge } from '@/components/panels/ERDDiagramPanel';
import type { QualityReport } from '@/components/panels/DataQualityReport';

interface Artifact {
  id: string;
  data_product_id: string;
  artifact_type: 'erd' | 'yaml' | 'brd' | 'quality_report' | 'document' | 'export';
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

function useYAMLContent(dataProductId: string | null, enabled = false) {
  return useQuery<{ yaml: string }>({
    queryKey: ['artifacts', 'yaml', dataProductId],
    queryFn: () =>
      api.get<{ yaml: string }>(`/artifacts/${dataProductId}/yaml`),
    enabled: enabled && dataProductId !== null && dataProductId.length > 0,
  });
}

export { useArtifacts, useERDData, useQualityReport, useYAMLContent };
export type { Artifact, ArtifactsResponse, ERDResponse };
