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
  scoped_source_review?: boolean;
  v1_pilot_profile?: "DISABLED" | "BERLIN_LID_LOCKED" | "BERLIN_LID_SYNTHETIC";
};

export function candidateMembership(membership: Membership | undefined) {
  return Boolean(
    membership?.status === "ACTIVE" &&
      membership.role_keys.includes("EXAMINER"),
  );
}

export function pilotActive(user: DemoUser | null | undefined): boolean {
  return (
    user?.v1_pilot_profile === "BERLIN_LID_SYNTHETIC" ||
    user?.v1_pilot_profile === "BERLIN_LID_LOCKED"
  );
}

export function pilotRouteAllowed(path: string): boolean {
  return [
    /^\/$/,
    /^\/candidate(?:\/exams\/[^/]+|\/results(?:\/releases\/[^/]+)?)?$/,
    /^\/institution\/(?:overview|assignments|enrollment|results\/[^/]+|releases(?:\/[^/]+)?)$/,
    /^\/control-center(?:\/(?:organisations|source-registry|audit|system|standards\/lid))?$/,
  ].some((pattern) => pattern.test(path));
}

export function canViewRoute(
  path: string,
  membership: Membership | undefined,
  platformPermissions: string[] = [],
  scopedSourceReview = false,
  restrictedPilot = false,
) {
  if (restrictedPilot && !pilotRouteAllowed(path)) return false;
  const permissions =
    membership?.status === "ACTIVE" ? membership.permissions : [];
  if (path === "/control-center" || path.startsWith("/control-center/"))
    return platformPermissions.includes("platform.control_center.read");
  if (path === "/source-review") return scopedSourceReview;
  if (path.startsWith("/candidate")) return candidateMembership(membership);
  if (path === "/institution/enrollment")
    return Boolean(
      membership?.status === "ACTIVE" &&
        membership.permissions.includes("membership.manage"),
    );
  if (path.startsWith("/institution/overview"))
    return permissions.includes("exam_assignment.read");
  if (path.startsWith("/institution/assignments"))
    return ["exam_assignment.manage", "exam.read", "membership.read"].every(
      (key) => permissions.includes(key),
    );
  if (path.startsWith("/institution/results"))
    return permissions.includes("result.read");
  if (path.startsWith("/institution/releases"))
    return permissions.includes("result.read");
  if (path.startsWith("/institution/rubrics"))
    return permissions.includes("rubric.read");
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

export function initialMembership(user: DemoUser, demoMode = false) {
  const active = user.memberships.filter((item) => item.status === "ACTIVE");
  return (
    (demoMode &&
      active.find(
        (item) => item.organisation_slug === "prufai-demo-academy-v2",
      )) ||
    active[0]
  );
}

export function workspaceLanding(
  user: DemoUser,
  selected: Membership | undefined = initialMembership(user),
): string {
  if (user.platform_permissions.includes("platform.control_center.read"))
    return "/control-center";
  if (candidateMembership(selected)) return "/candidate";
  if (
    selected?.status === "ACTIVE" &&
    selected.permissions.includes("exam_assignment.read")
  )
    return "/institution/overview";
  const first = workspaceLinks.find(
    ([, href]) =>
      href !== "/" &&
      canViewRoute(href, selected, [], false, pilotActive(user)),
  );
  if (first) return first[1];
  if (user.scoped_source_review && !pilotActive(user)) return "/source-review";
  return "/";
}

export const workspaceLinks = [
  ["Overview", "/"],
  ["Scoped source review", "/source-review"],
  ["My exams", "/candidate"],
  ["My released results", "/candidate/results"],
  ["Institution inspection", "/institution/overview"],
  ["Assign practice assessment", "/institution/assignments"],
  ["Result releases", "/institution/releases"],
  ["Exam Studio preview", "/exam-studio"],
  ["Result moderation", "/institution/result-moderation"],
  ["Appeals", "/institution/appeals"],
  ["Writing review", "/institution/writing-reviews"],
  ["Writing rubrics", "/institution/rubrics"],
  ["Speaking review", "/institution/speaking-reviews"],
] as const;
