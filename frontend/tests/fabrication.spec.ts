import { expect, test } from "@playwright/test";

test("forest reference exposes five sheets, prints PDF, and invalidates cut-ready after an edit", async ({
  page,
}) => {
  const project = await (
    await page.request.post("/references/forest-deer?mode=multicolor")
  ).json();
  await page.goto(`/?project=${project.id}`);
  await expect(page.locator('[data-testid="layer-row"]')).toHaveCount(5);
  await page.getByText("Advanced settings").click();
  await expect(page.getByLabel("Role deer", { exact: true })).toHaveValue(
    "retain",
  );
  await expect(
    page.getByLabel("Lock object deer", { exact: true }),
  ).toBeChecked();
  const cutReady = page.getByRole("button", {
    name: "Download cut-ready",
    exact: true,
  });
  await expect(cutReady).toBeEnabled();
  await page.getByLabel("Paper size").selectOption("A3");
  await page.getByLabel("Print side", { exact: true }).selectOption("back");
  const pending = page.waitForEvent("download");
  await page.getByLabel("More export formats").selectOption("pdf");
  expect((await pending).suggestedFilename()).toContain(".pdf");
  await page.getByLabel("Layer color layer-01").fill("#aabbcc");
  await expect(cutReady).toBeDisabled();
  await page
    .getByRole("button", { name: "Validate design", exact: true })
    .click();
  await expect(cutReady).toBeEnabled();
  await page.reload();
  await expect(page.getByLabel("Layer color layer-01")).toHaveValue("#aabbcc");
  await expect(page.getByLabel("Frame width (mm)")).toHaveValue("10");
});

test("stencil family exposes styles and frame-connected fabrication controls on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Stencil", exact: true }).click();
  await page.getByLabel("Style", { exact: true }).selectOption("detailed");
  await page.getByLabel("Frame width (mm)").fill("12");
  await expect(page.getByLabel("Assembly", { exact: true })).toHaveValue(
    "flat",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
