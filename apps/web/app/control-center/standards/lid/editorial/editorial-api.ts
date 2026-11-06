import { standardsRequest } from "../../../standards-api";
import { lidPath, type LidBlueprintSummary } from "../lid-api";

const collectionsPath = `${lidPath}/editorial/collections`;
const collectionPath = (id: string) =>
  `${collectionsPath}/${encodeURIComponent(id)}`;
const itemPath = (collection: string, version: string) =>
  `${collectionPath(collection)}/items/${encodeURIComponent(version)}`;

export type ReviewStage = "FACTUAL" | "ASSESSMENT" | "RIGHTS";

export type ItemRevision = {
  prompt: string;
  options: string[];
  correct_index: number;
  rationale: string;
  replacement_source?: {
    version_id: string;
    metadata_checksum: string;
    reference_url: string;
    locator: string;
    fact: string;
    last_verified: string;
    recheck_after: string;
  };
};

export type EditorialCounts = {
  drafted: number;
  fact_check_pending: number;
  expert_review_pending: number;
  rights_pending: number;
  approved: number;
  rejected: number;
  withdrawn: number;
  published: number;
  general_pool_eligible: number;
  berlin_pool_eligible: number;
  test_review_decisions: number;
};

export type EditorialItemSummary = {
  item_id: string;
  item_version_id: string;
  checksum: string;
  provenance_checksum: string;
  code: string;
  version_label: string;
  scope: "GENERAL_DE" | "BUNDESLAND_SPECIFIC";
  jurisdiction: string | null;
  maturity: string;
  authorship: string;
  blockers: string[];
};

export type EditorialCollectionSummary = {
  id: string;
  code: string;
  checksum: string;
  organisation_id: string;
  blueprint_version_id: string;
  pack: { id: string; code: string; checksum: string; status: string };
};

export type EditorialCollection = EditorialCollectionSummary & {
  counts: EditorialCounts;
  blockers: string[];
  items: EditorialItemSummary[];
  allowed_actions: { preview: boolean; approve: boolean; publish: boolean };
};

export type EditorialSource = {
  version_id: string;
  metadata_checksum: string;
  rights_decision_checksum: string | null;
  publisher: string;
  title: string;
  source_url: string;
  version_label: string;
  publication_date: string | null;
  effective_from: string | null;
  retrieved_at: string;
  rights_status: string;
};

export type EditorialClaim = {
  claim_code: string;
  reference_url: string;
  locator: string;
  fact: string;
  last_verified: string;
  recheck_after: string;
  recheck_trigger: string;
  applicability_as_of: string | null;
  legal_effective_from: string | null;
  effective_date_basis: string | null;
  effective_date_reference: string | null;
  historical_event_date: string | null;
};

export type EditorialQualification = {
  id: string;
  checksum: string;
  stage: ReviewStage;
  evidence_class: string;
};

export type EditorialReceipt = {
  id: string;
  item_version_id: string;
  operation_id: string;
  action: string;
  decision: string | null;
  evidence_class: string;
  item_checksum: string;
  recorded_at: string;
  actor_user_id: string;
  authority: string;
  checksum: string;
};

export type EditorialItem = EditorialItemSummary & {
  prompt: string;
  options: Array<{ option_id: string; display_text: string }>;
  correct_option_id: string;
  rationale: string;
  source: EditorialSource;
  claim: EditorialClaim;
  key_checksum: string;
  rights_status: string;
  history: EditorialItemSummary[];
  review_receipts: EditorialReceipt[];
  allowed_actions: string[];
  qualifications: EditorialQualification[];
  review_protocol: Record<ReviewStage, string[]>;
};

export type EditorialPreview = {
  mode: "CONTENT_EDITORIAL_PREVIEW";
  collection_checksum: string;
  checksum: string;
  bundesland: string;
  duration_seconds: number;
  blueprint_version_id: string;
  pack_version_id: string;
  items: Array<EditorialItem & { slot: number }>;
};

type OperationPin = { operation_id: string; expected_checksum: string };
type RightsDecision = {
  basis_reference: string;
  rights_holder_status:
    | "CONFIRMED_RIGHTSHOLDER"
    | "NO_EXCLUSIVE_RIGHTS_ASSERTED";
  rights_holder?: string;
  practice_use_allowed: boolean;
  display_allowed: boolean;
  redistribution_allowed: boolean;
  derivative_use_allowed: boolean;
};

export const editorialApi = {
  blueprints: () =>
    standardsRequest<LidBlueprintSummary[]>(`${lidPath}/blueprints`),
  collections: (organisation: string) =>
    standardsRequest<EditorialCollectionSummary[]>(
      `${collectionsPath}?${new URLSearchParams({ organisation_id: organisation })}`,
    ),
  create: (
    organisation: string,
    blueprint: LidBlueprintSummary,
    operation: string,
  ) =>
    standardsRequest<EditorialCollection>(collectionsPath, "POST", {
      organisation_id: organisation,
      blueprint_version_id: blueprint.id,
      expected_blueprint_checksum: blueprint.checksum,
      operation_id: operation,
    }),
  collection: (id: string) =>
    standardsRequest<EditorialCollection>(collectionPath(id)),
  item: (collection: string, version: string) =>
    standardsRequest<EditorialItem>(itemPath(collection, version)),
  preview: (collection: EditorialCollection) =>
    standardsRequest<EditorialPreview>(
      `${collectionPath(collection.id)}/preview`,
      "POST",
      {
        expected_collection_checksum: collection.checksum,
        bundesland: "DE-BE",
      },
    ),
  packDecision: (
    collection: EditorialCollection,
    action: "approve" | "publish",
    operation: string,
  ) =>
    standardsRequest<EditorialCollection>(
      `${collectionPath(collection.id)}/${action}`,
      "POST",
      {
        expected_collection_checksum: collection.checksum,
        operation_id: operation,
      },
    ),
  submit: (collection: string, version: string, data: OperationPin) =>
    standardsRequest<EditorialReceipt>(
      `${itemPath(collection, version)}/review/submit`,
      "POST",
      data,
    ),
  decision: (
    collection: string,
    version: string,
    data: OperationPin & {
      stage: ReviewStage;
      decision: "APPROVE" | "REJECT";
      qualification_id: string;
      qualification_checksum: string;
      reason: string;
      rights?: RightsDecision;
    },
  ) =>
    standardsRequest<EditorialReceipt>(
      `${itemPath(collection, version)}/review/decision`,
      "POST",
      data,
    ),
  successor: (
    collection: string,
    version: string,
    data: OperationPin & ItemRevision,
  ) =>
    standardsRequest<EditorialItem>(
      `${itemPath(collection, version)}/successor`,
      "POST",
      data,
    ),
};

export function evidenceLabel(value: string) {
  return value === "SYNTHETIC_TEST" || value === "TEST_ONLY"
    ? "TEST ONLY"
    : value.replaceAll("_", " ");
}
