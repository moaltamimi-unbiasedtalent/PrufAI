export type LidPracticeAssignment = {
  title: string;
  bundeslaender: Array<{ code: string; label: string; available: boolean }>;
};

export type LidPracticeForm = {
  title: string;
  bundesland: string;
  bundesland_label: string;
  general_count: number;
  state_count: number;
  form_checksum: string;
  attempt_id: string;
  notice: string;
};

export type LidCitation = {
  source_version_id: string;
  source_metadata_checksum: string;
  source_url: string | null;
  reference_url?: string | null;
  locator: string;
};

export type LidPurpose =
  | "ORIENTATION_COURSE_CONTEXT"
  | "NATURALIZATION_KNOWLEDGE_CONTEXT";

export type LidNumericalOutcome =
  | "THRESHOLD_MET"
  | "BELOW_THRESHOLD"
  | "UNDETERMINED";

export type LidThresholdContext = {
  purpose: LidPurpose;
  threshold: number;
  numerical_outcome: LidNumericalOutcome;
  rule_version_id: string | null;
  rule_version_label: string | null;
  rule_checksum: string | null;
  legal_effective_from: string | null;
  legal_effective_date?: string | null;
  citations: LidCitation[];
};

export type LidPracticeResult = {
  scoring_run_id: string;
  result_version_id: string;
  raw_correct: number;
  total_items: number;
  answered_count: number;
  contexts: LidThresholdContext[];
  official_outcome: "UNDETERMINED";
  administrative_recognition: "NOT_ESTABLISHED";
  certification: "NOT_CERTIFIED";
};

export const lidPurposeLabel: Record<LidPurpose, string> = {
  ORIENTATION_COURSE_CONTEXT: "Orientation-course threshold preview",
  NATURALIZATION_KNOWLEDGE_CONTEXT:
    "Naturalization-knowledge threshold preview",
};

export const lidOutcomeLabel: Record<LidNumericalOutcome, string> = {
  THRESHOLD_MET: "Numerical threshold met",
  BELOW_THRESHOLD: "Below numerical threshold",
  UNDETERMINED: "Undetermined",
};
