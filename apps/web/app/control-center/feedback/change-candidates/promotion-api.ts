import {
  feedbackReadPrefix,
  feedbackRequest,
  feedbackWritePrefix,
  type Pin,
} from "../feedback-api";

export const nonproductionEnvironment = "NONPRODUCTION_VALIDATION";
type Environment = typeof nonproductionEnvironment | "PRODUCTION";
type SelectionTarget = { candidate: Pin; prompt: Pin };
export type Selection = Pin & {
  revision: number;
  status: "SELECTED" | "REVERTED" | "DENIED";
  environment: Environment;
  actor_id: string;
  created_at: string;
  selected: SelectionTarget | null;
  previous: SelectionTarget | null;
  reason_code?: string | null;
  reversion_mode?: "RESTORE_PREVIOUS" | "CLEAR_SELECTION" | null;
};
export type Validation = Pin & {
  status: "COMPLETED";
  created_at: string;
  case_count: number;
  production_activation: false;
  environment: typeof nonproductionEnvironment;
  execution_class: "TEST_ONLY";
  model_execution: "NOT_RUN";
  selection: Pin;
  prompt: Pin;
  dataset_version: Pin;
  cases: {
    case_id: string;
    input_checksum: string;
    request_checksum: string;
    output_checksum: string;
    validated_criteria_count: number;
  }[];
};
export type PromotionView = {
  prompt_definition_id: string;
  environment: typeof nonproductionEnvironment;
  baseline: Pin;
  readiness: Pin | null;
  current_selection: Selection | null;
  history: Selection[];
  validations: Validation[];
  allowed_actions: (
    | "AUTHORIZE_AND_SELECT"
    | "VALIDATE"
    | "REVERT"
    | "CLEAR_SELECTION"
  )[];
  blockers: string[];
  production_readiness: "NOT_READY";
  production_blocker: string;
};

const readPath = (id: string) =>
  `${feedbackReadPrefix}/change-candidates/${encodeURIComponent(id)}/promotion`;
const writePath = (id: string) =>
  `${feedbackWritePrefix}/change-candidates/${encodeURIComponent(id)}`;

type Operation = { operation_id: string; environment: Environment };
export const promotionApi = {
  read: (id: string) => feedbackRequest<PromotionView>(readPath(id)),
  select: (
    id: string,
    data: Operation & {
      readiness: Pin;
      expected_selection: Pin | null;
      rationale: string;
    },
  ) => feedbackRequest<Selection>(`${writePath(id)}/promotion`, data),
  revert: (
    id: string,
    data: Operation & {
      expected_selection: Pin;
      rationale: string;
      clear_selection?: boolean;
    },
  ) => feedbackRequest<Selection>(`${writePath(id)}/revert`, data),
  validate: (id: string, data: Operation & { selection: Pin }) =>
    feedbackRequest<Validation>(
      `${writePath(id)}/nonproduction-validations`,
      data,
    ),
};
