import { authenticatedFetch } from "../../lib/api";

export const sourcePath = "/api/v1/platform/standards";
export const packPath = "/api/v1/platform/standard-pack-versions";

export type CatalogueEntry = {
  key: string;
  publisher: string;
  title: string;
  edition_url: string;
  publication_label: string;
  effective_from: string | null;
  verified_on: string;
  rights_holder: string;
  rights_note: string;
  content_mode: "METADATA_ONLY";
  external_content_rights: string;
};

export type SourceVersion = {
  id: string;
  source_id: string;
  publisher: string;
  publication_date: string | null;
  retrieved_at: string | null;
  effective_from: string | null;
  effective_until: string | null;
  version_label: string;
  source_url: string | null;
  status: string;
  content_mode: string | null;
  metadata_checksum: string | null;
  sha256: string | null;
  legal_review_status: string;
  decision_checksum: string | null;
  approved_sha256: string | null;
  ingestion_allowed: boolean;
  rights_holder: string | null;
  licence_type: string | null;
  usage_rights: string | null;
  legal_reviewed_by_user_id: string | null;
  legal_reviewed_at: string | null;
  derivative_use_allowed: boolean;
  redistribution_allowed: boolean;
  ai_processing_allowed: boolean;
  retention_policy: string | null;
  latest_ingestion?: Ingestion | null;
};

export type Ingestion = {
  id: string;
  status: string;
  parser_name: string;
  parser_version: string | null;
  pages_detected: number | null;
  error_code: string | null;
};

export type Integrity = {
  id: string;
  sha256: string;
  file_size: number;
  integrity: string;
  storage_backend: string;
};

export type Citation = {
  citation_id: string;
  document_version_id: string;
  checksum?: string;
  authority_name: string;
  source_title: string;
  version_label: string;
  page_number?: number | null;
  source_id?: string;
};

export type FoundationDraft = {
  id: string;
  standard_pack_id: string;
  version_label: string;
  status: string;
  assessment_family?: string;
  language?: string;
  supported_levels?: string[];
  allowed_classifications?: string[];
  checksum?: string;
  configuration_checksum?: string;
  validation_status?: string;
  foundation?: boolean;
  publication_blockers?: string[];
  source_bindings?: {
    id: string;
    source_id: string;
    document_version_id: string;
    source_metadata_checksum: string | null;
    document_checksum: string | null;
    binding_type: string;
    purpose: string;
    publisher?: string;
    version_label?: string;
    content_mode?: string;
    legal_review_status?: string;
  }[];
  constructs?: Record<string, unknown>[];
};

export async function standardsRequest<T>(
  path: string,
  method = "GET",
  data?: object,
): Promise<T> {
  const response = await authenticatedFetch(path, {
    method,
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  if (!response.ok) {
    const message = await response.json().catch(() => null);
    const detail = typeof message?.detail === "string" ? message.detail : "";
    if (response.status === 403) {
      throw new Error(
        "Your permissions do not allow this standards operation.",
      );
    }
    if (response.status === 401) {
      throw new Error("Your session has expired. Sign in again.");
    }
    throw new Error(
      /^[A-Z][A-Z0-9_]{1,119}$/.test(detail)
        ? detail
        : `Standards operation could not be completed (${response.status}).`,
    );
  }
  return (await response.json()) as T;
}
