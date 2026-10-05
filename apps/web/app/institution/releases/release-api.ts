export type ResultRelease = {
  id: string;
  candidate_user_id: string;
  result_version_id: string;
  classification: string;
  status: string;
  released_at: string;
};

async function request<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "include" });
  if (!response.ok) throw new Error(`RELEASE_HTTP_${response.status}`);
  return (await response.json()) as T;
}

export const releaseApi = {
  list: (organisationId: string) =>
    request<ResultRelease[]>(
      `/api/v1/organisations/${organisationId}/releases`,
    ),
};
