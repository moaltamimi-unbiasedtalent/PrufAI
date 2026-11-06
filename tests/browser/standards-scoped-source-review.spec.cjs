const { test, expect } = require("@playwright/test");
const { createHash, randomUUID } = require("node:crypto");
const { readFileSync } = require("node:fs");
const { actor, headers } = require("./evaluation-fixture.cjs");
const { apiResponse, login, logout } = require("./support.cjs");

const standards = "/api/v1/platform/standards";
const controlCentre = "/api/v1/platform/control-center";
const scopedReview = "/api/v1/scoped-source-review";
const publisher = "TEST ONLY scoped source publisher";
const versionLabel = "original-synthetic-edition-1";

function fixture() {
  const path = process.env.PRUFAI_E2E_REFERENCE_MANIFEST;
  if (!path)
    throw new Error("The guarded browser fixture manifest is required.");
  return JSON.parse(readFileSync(path, "utf8")).scoped_source_review;
}

async function checked(response, expectedStatus) {
  expect(response.status(), await response.text()).toBe(expectedStatus);
  return response.json();
}

function timestamp(milliseconds) {
  return new Date(milliseconds).toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

function checksum(input) {
  return createHash("sha256").update(JSON.stringify(input)).digest("hex");
}

async function createVersions(page, author) {
  const namespace = randomUUID().replaceAll("-", "");
  async function create(relative, data) {
    return checked(
      await page.request.post(`${standards}${relative}`, {
        headers: headers(author),
        data,
      }),
      201,
    );
  }
  const authority = await create("/authorities", {
    code: `SCOPED_REVIEW_${namespace}`,
    name: publisher,
    authority_type: "INTERNAL",
  });
  const versions = [];
  for (const number of [1, 2, 3, 4, 5, 6]) {
    const source = await create("/sources", {
      authority_id: authority.id,
      source_code: `SCOPED_REVIEW_${namespace}_${number}`,
      title: `TEST ONLY scoped source ${number}`,
      document_type: "PRACTICE_MATERIAL",
      assessment_family: "LANGUAGE",
      language: "de",
    });
    const version = await create(`/sources/${source.id}/versions`, {
      version_label: versionLabel,
      content_mode: "METADATA_ONLY",
      rights_holder: publisher,
      licence_type: "REFERENCE_ONLY",
    });
    versions.push({ ...version, source_id: source.id, title: source.title });
  }
  return versions;
}

test("an independent scoped reviewer can inspect and decide only five pinned editions until revoked", async ({
  page,
  browser,
  baseURL,
}) => {
  const { organisation_id, reviewer_id, reviewer_email } = fixture();
  const author = await login(page, actor("source"));
  const versions = await createVersions(page, author);
  const [first, pending] = versions;
  const allowed = versions.slice(0, 5);
  const unrelated = versions[5];
  await logout(page);

  // Keep the issuer and reviewer sessions separate. The revocation check uses
  // the same reviewer bearer token issued before the administrator revoked it.
  const adminContext = await browser.newContext({ baseURL });
  try {
    const adminPage = await adminContext.newPage();
    const admin = await login(adminPage, "platform.admin@demo.prufai.local");
    const grantsPath = `${scopedReview}/orgs/${organisation_id}/grants`;
    const now = Date.now();
    // The test manifest pins the same sorted JSON scope the server verifies.
    const scope = {
      authorized_by: admin.profile.id,
      expires_at: timestamp(now + 30 * 60_000),
      organisation_id,
      purpose: "TEST ONLY independent bibliographic review acceptance",
      resources: allowed
        .map(({ id, metadata_checksum }) => ({
          allowed_decisions: ["DENY", "METADATA_ONLY"],
          metadata_checksum,
          metadata_fields: [
            "effective_from",
            "effective_until",
            "publication_date",
            "publisher",
            "rights_status",
            "title",
            "version_label",
          ],
          version_id: id,
        }))
        .sort((left, right) => left.version_id.localeCompare(right.version_id)),
      reviewer_user_id: reviewer_id,
      schema_version: 1,
      starts_at: timestamp(now - 60_000),
    };
    const scopeChecksum = checksum(scope);
    const manifestChecksum = checksum({
      evidence_class: "SYNTHETIC_TEST",
      scope_checksum: scopeChecksum,
      scope,
    });
    const grant =
      await test.step("Issue the exact grant through the authenticated API", async () => {
        expect(admin.profile.platform_permissions).toContain(
          "platform.role.assign",
        );
        return checked(
          await adminPage.request.post(grantsPath, {
            headers: headers(admin),
            data: {
              operation_id: randomUUID(),
              reviewer_user_id: scope.reviewer_user_id,
              purpose: scope.purpose,
              starts_at: scope.starts_at,
              expires_at: scope.expires_at,
              resources: scope.resources,
              approved_scope_checksum: scopeChecksum,
              business_approval_reference: `sha256:${manifestChecksum}`,
            },
          }),
          200,
        );
      });
    expect(grant).toMatchObject({
      organisation_id,
      scope_checksum: scopeChecksum,
      status: "ACTIVE",
    });
    const grantPath = `${grantsPath}/${grant.grant_id}`;
    const firstPath = `${grantPath}/sources/${first.id}`;
    const decision = {
      operation_id: randomUUID(),
      expected_metadata_checksum: first.metadata_checksum,
      decision: "METADATA_ONLY",
      rights_holder: publisher,
      licence_type: "REFERENCE_ONLY",
      basis_reference: "synthetic:independent-bibliographic-review",
      retention_policy: "synthetic:browser-acceptance-retention",
    };
    const reviewer = await login(page, reviewer_email);
    expect(reviewer.profile).toMatchObject({
      id: reviewer_id,
      memberships: [],
      platform_scope: false,
      platform_permissions: [],
    });

    await test.step("Inspect the exact edition and submit metadata-only review", async () => {
      await expect(page).toHaveURL(/\/source-review$/);
      await expect(
        page.getByRole("heading", { name: "Scoped source review" }),
      ).toBeVisible();
      const versions = page.getByRole("button", {
        name: /^Inspect TEST ONLY scoped source /,
      });
      await expect(versions).toHaveCount(5);
      for (const version of allowed) {
        await expect(
          page.getByRole("button", {
            name: `Inspect ${version.title}`,
            exact: true,
          }),
        ).toBeVisible();
      }
      await expect(
        page.getByText(unrelated.title, { exact: true }),
      ).toHaveCount(0);

      const inspection = apiResponse(page, firstPath);
      await page
        .getByRole("button", { name: `Inspect ${first.title}`, exact: true })
        .click();
      expect((await inspection).status()).toBe(200);
      const detail = page.getByRole("article", {
        name: "Source version details",
      });
      await expect(detail).toContainText(first.title);
      await expect(detail).toContainText(publisher);
      await expect(detail).toContainText(versionLabel);
      await expect(detail).toContainText(first.metadata_checksum);
      await expect(detail).toContainText(/unknown/i);
      await detail
        .getByRole("combobox", { name: "Rights decision", exact: true })
        .selectOption("METADATA_ONLY");
      await detail
        .getByLabel("Rights holder", { exact: true })
        .fill(decision.rights_holder);
      await detail
        .getByLabel("Licence type", { exact: true })
        .fill(decision.licence_type);
      await detail
        .getByLabel("Rights basis reference", { exact: true })
        .fill(decision.basis_reference);
      await detail
        .getByLabel("Retention policy reference", { exact: true })
        .fill(decision.retention_policy);
      const recording = apiResponse(page, `${firstPath}/decision`, "POST");
      await detail
        .getByRole("button", { name: "Record rights decision", exact: true })
        .click();
      expect((await recording).status()).toBe(200);
      await expect(
        page
          .getByRole("status")
          .filter({ hasText: "Rights decision recorded." }),
      ).toBeVisible();
      await expect(
        page.getByText("4 visible sources remain unreviewed."),
      ).toBeVisible();
    });

    await test.step("Reject an unrelated source, legacy content and broad platform access", async () => {
      for (const denied of [
        `${grantPath}/sources/${unrelated.id}`,
        `${standards}/sources`,
        `${standards}/versions/${first.id}/pages`,
        `${standards}/versions/${first.id}/integrity`,
        `${controlCentre}/overview`,
      ]) {
        const response = await page.request.get(denied, {
          headers: headers(reviewer),
        });
        expect(response.status(), denied).toBe(403);
        expect(await response.text()).not.toContain(unrelated.title);
      }
    });

    await test.step("Revoke the grant and deny access through the still-authenticated browser", async () => {
      const revocation = await adminPage.request.post(`${grantPath}/revoke`, {
        headers: headers(admin),
        data: { operation_id: randomUUID() },
      });
      expect(revocation.status()).toBe(200);
      const profile = await page.request.get("/api/v1/auth/me", {
        headers: headers(reviewer),
      });
      expect(profile.status()).toBe(200);
      const inspection = await page.request.get(firstPath, {
        headers: headers(reviewer),
      });
      expect(inspection.status()).toBe(403);
      const deniedDecision = await page.request.post(
        `${grantPath}/sources/${pending.id}/decision`,
        {
          headers: headers(reviewer),
          data: {
            ...decision,
            expected_metadata_checksum: pending.metadata_checksum,
          },
        },
      );
      expect(deniedDecision.status()).toBe(403);
      await page.reload();
      await expect(
        page.getByText("No active source-review grants.", { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: /^Inspect TEST ONLY scoped source / }),
      ).toHaveCount(0);
    });

    await test.step("Preserve the legitimate administrator Source Registry flow", async () => {
      await adminPage
        .getByRole("link", { name: "Source Registry", exact: true })
        .click();
      await adminPage
        .getByLabel("Source version reference", { exact: true })
        .fill(first.id);
      const inspection = apiResponse(
        adminPage,
        `${standards}/versions/${first.id}`,
      );
      await adminPage
        .getByRole("button", { name: "Inspect source version", exact: true })
        .click();
      expect(await checked(await inspection, 200)).toMatchObject({
        id: first.id,
        metadata_checksum: first.metadata_checksum,
        legal_review_status: "APPROVED",
        legal_reviewed_by_user_id: reviewer_id,
        ingestion_allowed: false,
      });
      const unresolved = await adminPage.request.get(
        `${standards}/versions/${pending.id}`,
        { headers: headers(admin) },
      );
      expect(await checked(unresolved, 200)).toMatchObject({
        id: pending.id,
        legal_review_status: "NOT_REVIEWED",
        decision_checksum: null,
      });
    });
    await logout(page);
    await logout(adminPage);
  } finally {
    await adminContext.close();
  }
});
