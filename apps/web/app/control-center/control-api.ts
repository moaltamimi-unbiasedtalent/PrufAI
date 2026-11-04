import { authenticatedFetch } from "../../lib/api";

export type MetadataValue = string | number | boolean | null | string[];
export type MetadataRow = Record<string, MetadataValue>;
export type MetadataPage = {
  items: MetadataRow[];
  total: number;
  offset: number;
  limit: number;
};
export type PlatformOverview = {
  environment: string;
  metrics: Record<string, number | null>;
  available_sections: string[];
  metric_scopes?: Record<string, string>;
  recent_audit: MetadataRow[] | null;
  service_health: PlatformSystem | null;
};
export type PlatformSystem = {
  environment: string;
  database: { status: string };
  object_storage: { backend: string; health: string };
  redis: { health: string };
  ai_executor: { status: string; description: string };
  writing_proposal_executor?: {
    status: "CONFIGURED" | "UNCONFIGURED" | "TEST_ONLY";
    description: string;
    provider?: string;
    model_name?: string;
    adapter_version?: string;
    test_only: boolean;
  };
  speech_transcription_executor?: {
    status: "CONFIGURED" | "UNCONFIGURED" | "TEST_ONLY";
    description: string;
    provider?: string;
    model_name?: string;
    adapter_version?: string;
    test_only: boolean;
  };
  certification: {
    official_issuance: string;
    synthetic_issuance_enabled: boolean;
  };
};

export class PlatformApiError extends Error {
  constructor(readonly status: number) {
    super(
      status === 403
        ? "Your platform permissions do not allow this view."
        : status === 401
          ? "Your session has expired. Sign in again."
          : "Platform data could not be loaded. Check service availability and retry.",
    );
  }
}

const prefix = "/api/v1/platform/control-center";
async function read<T>(path: string): Promise<T> {
  const response = await authenticatedFetch(`${prefix}${path}`);
  if (!response.ok) throw new PlatformApiError(response.status);
  return (await response.json()) as T;
}

export const controlApi = {
  overview: () => read<PlatformOverview>("/overview"),
  system: () => read<PlatformSystem>("/system"),
  page: (
    section: string,
    options: {
      kind?: string;
      event_type?: string;
      organisation_id?: string;
      offset?: number;
      limit?: number;
    } = {},
  ) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(options)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    return read<MetadataPage>(`/${section}?${query.toString()}`);
  },
};

export function displayMetadata(value: MetadataValue | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.join(", ") || "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}
