import { sourcePath } from "../../standards-api";

export const dtzPath = `${sourcePath}/dtz`;
export const overallB1Rule = "PRUFAI_DTZ_S2_OVERALL_B1";

const berlinDate = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeZone: "Europe/Berlin",
});

export function effectiveDate(value: string | null) {
  if (!value) return "Not established";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : `${berlinDate.format(date)} (Europe/Berlin)`;
}

export type DtzSource = {
  key: string;
  title: string;
  publisher: string;
  source_url: string;
  edition_url: string | null;
  version_label: string;
  publication_label: string;
  verified_on: string;
  effective_from: string | null;
  rights_holder: string;
  rights_note: string;
  registered: {
    id: string;
    metadata_checksum: string;
    legal_review_status: string;
    reviewed: boolean;
  } | null;
};

export type DtzSummary = {
  id: string;
  standard_pack_id: string;
  checksum: string;
  status: string;
  version_label: string;
  effective_from: string | null;
  effective_until: string | null;
};

export type DtzSourceBinding = {
  id: string;
  key: string;
  document_version_id: string;
  source_metadata_checksum: string;
  source_url: string;
  publisher: string;
  title: string;
  version_label: string;
  verified_on: string;
  legal_review_status: string;
  rights_status: string;
  ingestion_allowed: boolean;
  redistribution_allowed: boolean;
  retention_policy: string | null;
  available: boolean;
};

type DtzArtifactSource = {
  source_version_id: string;
  source_url: string | null;
  metadata_checksum: string;
  reference_kind: string;
};

export type DtzRule = {
  id: string;
  code: string;
  name: string;
  version_label: string;
  checksum: string;
  status: string;
  review_status: string;
  expression: unknown;
  effective_from: string | null;
  effective_until: string | null;
  sources: DtzArtifactSource[];
};

export type DtzRubric = {
  id: string;
  skill: string;
  checksum: string;
  status: string;
  review_status: string;
  assessment_mode: string;
  evidence_policy: unknown;
  sources: DtzArtifactSource[];
};

export type DtzTask = {
  code: string;
  name: string;
  item_kind: string;
  item_count: number | null;
  response_mode: string;
  interaction: string;
  evidence_mode: string;
  fidelity: string;
  task_choice_count: number | null;
  synthetic_example: {
    label: string;
    prompt: string;
    options?: string[];
  } | null;
};

export type DtzSection = {
  skill: string;
  name: string;
  scoring_domain: string;
  time_limit_seconds: number | null;
  time_is_approximate: boolean;
  interaction_fidelity: string;
  item_total: number | null;
  groups: { code: string; name: string; tasks: DtzTask[] }[];
};

type DtzClaim = {
  source_key: string;
  source_version_id: string;
  source_metadata_checksum: string;
  source_url: string;
  reference_url?: string | null;
  locator: string;
  claimed_value: unknown;
  scope: string;
  applicable_from: string | null;
};

export type DtzClaimGroup = {
  code: string;
  category: string;
  status: string;
  interpretation: string | null;
  reviewing_expert: string | null;
  resolution_evidence: unknown;
  claims: DtzClaim[];
};

export type DtzBlueprint = DtzSummary & {
  assessment_family: string;
  exam_family: string;
  language: string;
  supported_levels: string[];
  allowed_classifications: string[];
  sections: DtzSection[];
  source_bindings: DtzSourceBinding[];
  claim_ledger: DtzClaimGroup[];
  rules: DtzRule[];
  rubrics: DtzRubric[];
  publication_blockers: { code: string; message: string }[];
  review_status: string;
  notice: string;
  manifest_checksum: string;
  content_maturity: string;
  structural_validation: string;
  regulatory_candidate_effective_from: string;
};

export type DtzPreview = {
  outcome: string;
  reason: string;
  execution_class: "DRAFT_PREVIEW";
  rule: DtzRule | null;
  objective?: {
    label: string;
    prompt: string;
    options: { option_id: string; label: string }[];
    status: string;
    awarded_points: string | null;
    maximum_points: string;
    combined_maximum_points: number;
    rule_version_id: string;
    rule_checksum: string;
  };
};
