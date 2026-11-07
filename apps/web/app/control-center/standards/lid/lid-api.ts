import type {
  LidCitation,
  LidPurpose,
  LidThresholdContext,
} from "../../../../lib/lid-practice";
import { sourcePath } from "../../standards-api";

export const lidPath = `${sourcePath}/lid`;

export type LidSource = {
  key: string;
  title: string;
  publisher: string;
  source_url: string;
  edition_url: string | null;
  version_label: string;
  publication_label: string;
  verified_on: string;
  retrieved_at: string | null;
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

export type LidBlueprintSummary = {
  id: string;
  standard_pack_id: string;
  status: string;
  version_label: string;
  checksum: string;
};

export type LidBlueprint = LidBlueprintSummary & {
  assessment_family: string;
  exam_family: string;
  language: string;
  cefr_level: string | null;
  allowed_classifications: string[];
  review_status: string;
  content_maturity: string;
  official_provider_authorization: boolean;
  notice: string;
  structure: {
    general_items: number;
    state_items: number;
    total_items: number;
    options_per_item: number;
    correct_options_per_item: number;
    time_limit_seconds: number;
  };
  selection_policy_version: string;
  jurisdictions: Array<{ code: string; name: string }>;
  synthetic_fixture_jurisdictions: Array<{ code: string; name: string }>;
  question_pools: Array<{
    scope: string;
    scope_label: string;
    eligible_catalogue_count: number;
  }>;
  source_bindings: Array<{
    key: string;
    document_version_id: string;
    source_metadata_checksum: string;
    title: string;
    rights_status: string;
    ingestion_allowed: boolean;
    redistribution_allowed: boolean;
    question_reuse_allowed: boolean;
    image_reuse_allowed: boolean;
    available: boolean;
  }>;
  claim_ledger: Array<{
    code: string;
    status: string;
    interpretation: string | null;
    claims: Array<
      LidCitation & {
        scope: string;
        claimed_value: unknown;
        applicable_from: string | null;
      }
    >;
  }>;
  rules: Array<{
    id: string;
    purpose: LidPurpose;
    threshold: number;
    name: string;
    version_label: string;
    checksum: string;
    status: string;
    review_status: string;
    legal_effective_from: string | null;
  }>;
  publication_blockers: Array<{ code: string; message: string }>;
};

export type LidPreview = {
  contexts: LidThresholdContext[];
  official_outcome: string;
  administrative_recognition: string;
};
