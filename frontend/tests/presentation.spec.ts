import { expect, test } from "@playwright/test";

test("decorative surround is independent of fabrication, and deadline preference persists", async ({
  page,
}) => {
  const project = await (
    await page.request.post("/references/forest-deer?mode=multicolor")
  ).json();
  await page.goto(`/?project=${project.id}`);
  await expect(page.getByText("CPU mode", { exact: false })).toBeVisible();
  await expect(page.getByTestId("flat-preview")).toBeVisible();
  await expect(page.locator(".paper")).toHaveClass(/display-surround/);
  const originalPath = await page
    .getByTestId("flat-preview")
    .locator("path")
    .last()
    .getAttribute("d");
  await page
    .getByLabel("Display frame", { exact: true })
    .selectOption("walnut");
  await expect(page.locator(".paper")).toHaveCSS("--display-wood", "#533b2c");
  await page.getByLabel("Time limit (s)", { exact: true }).fill("600");
  await page.getByLabel("Display frame", { exact: true }).selectOption("none");
  await expect(page.locator(".paper")).not.toHaveClass(/display-surround/);
  expect(
    await page
      .getByTestId("flat-preview")
      .locator("path")
      .last()
      .getAttribute("d"),
  ).toBe(originalPath);
  const current = await (
    await page.request.get(`/projects/${project.id}`)
  ).json();
  expect(current.revision_id).toBe(project.revision_id);
  expect(current.report.status).toBe("PASS");
  await page.reload();
  await expect(page.getByLabel("Time limit (s)", { exact: true })).toHaveValue(
    "600",
  );
  await expect(page.getByLabel("Display frame", { exact: true })).toHaveValue(
    "none",
  );
});

test("AI preferences can be explicitly turned off and survive reload", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Advanced settings", { exact: false }).click();
  for (const label of [
    "Object segmentation",
    "Protect facial features",
    "Protect important details",
    "Depth estimation",
  ])
    await page.getByRole("checkbox", { name: label, exact: true }).uncheck();
  await page.reload();
  await page.getByText("Advanced settings", { exact: false }).click();
  await expect(
    page.getByRole("checkbox", { name: "Object segmentation", exact: true }),
  ).not.toBeChecked();
});
