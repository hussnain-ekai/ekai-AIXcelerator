export type SourceMode = 'structured' | 'document' | 'hybrid' | 'unknown';

export type ExactnessState =
  | 'validated_exact'
  | 'estimated'
  | 'insufficient_evidence'
  | 'not_applicable';

export type ConfidenceDecision = 'high' | 'medium' | 'abstain';

export type AnswerTrustState =
  | 'answer_ready'
  | 'answer_with_warnings'
  | 'abstained_missing_evidence'
  | 'abstained_conflicting_evidence'
  | 'blocked_access'
  | 'failed_recoverable'
  | 'failed_admin';

export interface AnswerCitation {
  citation_type: 'sql' | 'document_chunk' | 'document_fact';
  reference_id: string;
  label?: string | null;
  page?: number | null;
  score?: number | null;
  metadata?: Record<string, unknown>;
}

export interface AnswerRecoveryAction {
  action: string;
  description: string;
  metadata?: Record<string, unknown>;
}

export interface AnswerContract {
  source_mode: SourceMode;
  exactness_state: ExactnessState;
  confidence_decision: ConfidenceDecision;
  trust_state: AnswerTrustState;
  evidence_summary?: string | null;
  conflict_notes: string[];
  citations: AnswerCitation[];
  recovery_actions: AnswerRecoveryAction[];
  metadata: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function toSourceMode(value: unknown): SourceMode {
  if (value === 'structured' || value === 'document' || value === 'hybrid') return value;
  return 'unknown';
}

function toExactnessState(value: unknown): ExactnessState {
  if (
    value === 'validated_exact' ||
    value === 'estimated' ||
    value === 'insufficient_evidence'
  ) {
    return value;
  }
  return 'not_applicable';
}

function toConfidenceDecision(value: unknown): ConfidenceDecision {
  if (value === 'high' || value === 'medium' || value === 'abstain') return value;
  return 'medium';
}

function toTrustState(value: unknown): AnswerTrustState {
  if (
    value === 'answer_ready' ||
    value === 'answer_with_warnings' ||
    value === 'abstained_missing_evidence' ||
    value === 'abstained_conflicting_evidence' ||
    value === 'blocked_access' ||
    value === 'failed_recoverable' ||
    value === 'failed_admin'
  ) {
    return value;
  }
  return 'answer_ready';
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

export function normalizeAnswerContract(value: unknown): AnswerContract | null {
  if (!isRecord(value)) return null;

  const rawCitations = Array.isArray(value.citations) ? value.citations : [];
  const citations: AnswerCitation[] = rawCitations
    .filter((item): item is Record<string, unknown> => isRecord(item))
    .map((item) => ({
      citation_type:
        item.citation_type === 'sql' ||
        item.citation_type === 'document_chunk' ||
        item.citation_type === 'document_fact'
          ? item.citation_type
          : 'document_chunk',
      reference_id: typeof item.reference_id === 'string' ? item.reference_id : 'unknown',
      label: typeof item.label === 'string' ? item.label : null,
      page: typeof item.page === 'number' ? item.page : null,
      score: typeof item.score === 'number' ? item.score : null,
      metadata: isRecord(item.metadata) ? item.metadata : {},
    }));

  const rawRecoveryActions = Array.isArray(value.recovery_actions) ? value.recovery_actions : [];
  const recoveryActions: AnswerRecoveryAction[] = rawRecoveryActions
    .filter((item): item is Record<string, unknown> => isRecord(item))
    .map((item) => ({
      action: typeof item.action === 'string' ? item.action : 'review',
      description: typeof item.description === 'string' ? item.description : 'Review evidence context.',
      metadata: isRecord(item.metadata) ? item.metadata : {},
    }));

  return {
    source_mode: toSourceMode(value.source_mode),
    exactness_state: toExactnessState(value.exactness_state),
    confidence_decision: toConfidenceDecision(value.confidence_decision),
    trust_state: toTrustState(value.trust_state),
    evidence_summary:
      typeof value.evidence_summary === 'string' ? value.evidence_summary : null,
    conflict_notes: asStringList(value.conflict_notes),
    citations,
    recovery_actions: recoveryActions,
    metadata: isRecord(value.metadata) ? value.metadata : {},
  };
}
