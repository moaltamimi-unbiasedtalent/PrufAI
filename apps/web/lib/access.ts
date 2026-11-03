export type Membership = {
  organisation_id: string;
  organisation_name: string;
  organisation_slug?: string;
  status: string;
  role_keys: string[];
  permissions: string[];
};
export type DemoUser = {
  id: string;
  email: string;
  display_name: string | null;
  memberships: Membership[];
  platform_scope: boolean;
  platform_permissions: string[];
};

export function candidateMembership(membership: Membership | undefined) {
  return Boolean(
    membership?.status === "ACTIVE" &&
      membership.role_keys.includes("EXAMINER"),
  );
}

export function canViewRoute(path: string, membership: Membership | undefined) {
  const permissions =
    membership?.status === "ACTIVE" ? membership.permissions : [];
  if (path.startsWith("/candidate")) return candidateMembership(membership);
  if (path.startsWith("/institution/overview"))
    return permissions.includes("exam_assignment.read");
  if (
    path.startsWith("/institution/writing-reviews") ||
    path.startsWith("/institution/speaking-reviews")
  )
    return permissions.includes("subjective.read");
  if (path.startsWith("/institution/result-moderation"))
    return permissions.includes("result.moderate");
  if (path.startsWith("/institution/appeals"))
    return permissions.includes("appeal.read");
  if (path.startsWith("/exam-studio")) return permissions.includes("exam.read");
  if (path.startsWith("/institution")) return false;
  return true;
}

export const workspaceLinks = [
  ["Overview", "/"],
  ["My exams", "/candidate"],
  ["Institution inspection", "/institution/overview"],
  ["Exam Studio preview", "/exam-studio"],
  ["Result moderation", "/institution/result-moderation"],
  ["Appeals", "/institution/appeals"],
  ["Writing review", "/institution/writing-reviews"],
  ["Speaking review", "/institution/speaking-reviews"],
] as const;
