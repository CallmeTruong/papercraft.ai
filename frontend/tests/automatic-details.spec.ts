import { expect, test } from "@playwright/test";

test("migrating an explicit CPU-only preference keeps all models disabled", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem(
      "papercraft-ai-settings",
      JSON.stringify({
        segmentation: false,
        face_features: false,
        depth: false,
      }),
    ),
  );
  await page.goto("/");
  await expect(page.getByText("CPU mode", { exact: false })).toBeVisible();
  await page.getByText("Advanced settings", { exact: false }).click();
  await expect(
    page.getByRole("checkbox", {
      name: "Protect important details",
      exact: true,
    }),
  ).not.toBeChecked();
});

test("automatic detail defaults, crop and decoding budget persist", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Advanced settings", { exact: false }).click();
  const toggle = page.getByRole("checkbox", {
    name: "Protect important details",
    exact: true,
  });
  await expect(toggle).toBeChecked();
  await page.getByLabel("AI detail crops", { exact: true }).selectOption("2");
  await page.getByLabel("AI detail budget", { exact: true }).fill("60");
  await page.reload();
  await page.getByText("Advanced settings", { exact: false }).click();
  await expect(page.getByLabel("AI detail crops", { exact: true })).toHaveValue(
    "2",
  );
  await expect(
    page.getByLabel("AI detail budget", { exact: true }),
  ).toHaveValue("60");
  await toggle.uncheck();
  await page.reload();
  await page.getByText("Advanced settings", { exact: false }).click();
  await expect(toggle).not.toBeChecked();
});

test("saved automatic proposal overlay can be promoted to a manual region without regenerating", async ({
  page,
}) => {
  const project = await (
    await page.request.post("/references/forest-deer?mode=multicolor")
  ).json();
  // Presentation fixture only: this is not model quality evidence.
  await page.route(`**/projects/${project.id}`, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.metrics.ai_diagnostics = [
      {
        component: "detail_selection",
        status: "applied",
        method: "structure_only",
        region_count: 1,
        regions: [
          {
            box: [0.2, 0.2, 0.3, 0.3],
            label: "local structure",
            source: "structure",
          },
        ],
      },
    ];
    await route.fulfill({ response, json: data });
  });
  await page.goto(`/?project=${project.id}`);
  await page.getByText("Detail protection (0/16)", { exact: true }).click();
  await expect(
    page.getByText("Suggested regions (1)", { exact: true }),
  ).toBeVisible();
  await expect(page.locator('rect[stroke="#26845b"]')).toHaveCount(1);
  await page.getByText("Suggested regions (1)", { exact: true }).click();
  await page
    .getByRole("button", {
      name: "Keep suggestion 1",
      exact: true,
    })
    .click();
  await expect(
    page.getByText("Detail protection (1/16)", { exact: true }),
  ).toBeVisible();
  await expect(page.locator('rect[stroke="#6331a3"]')).toHaveCount(1);
  const unchanged = await (
    await page.request.get(`/projects/${project.id}`)
  ).json();
  expect(unchanged.revision_id).toBe(project.revision_id);
});

test("old nested AI schema is rejected before upload", async ({ page }) => {
  await page.route("**/api/capabilities", (route) =>
    route.fulfill({
      json: { config_fields: ["detail_boxes", "detail_policy", "depth_paper"] },
    }),
  );
  let uploads = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/projects"
    )
      uploads++;
  });
  await page.goto("/");
  await page
    .locator('input[type="file"]')
    .first()
    .setInputFiles({
      name: "test.png",
      mimeType: "image/png",
      buffer: Buffer.from("must not be uploaded"),
    });
  await expect(page.getByRole("alert").last()).toContainText(
    "Restart the backend to enable automatic detail protection",
  );
  expect(uploads).toBe(0);
});
