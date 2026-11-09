import { expect, test } from "@playwright/test";

test("English studio uses consistent forms without comparison or verbose helpers", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page).toHaveTitle("PaperCraft AI — Paper Art Studio");
  await expect(
    page.getByRole("heading", { name: "Design settings", exact: true }),
  ).toBeVisible();
  await page.getByText("Advanced settings", { exact: false }).click();
  await expect(
    page.getByLabel("AI detail budget", { exact: true }),
  ).toBeVisible();
  const controls = await page
    .locator("#time-limit, #width, input[aria-label='AI detail budget']")
    .evaluateAll((elements) =>
      elements.map((e) => {
        const s = getComputedStyle(e);
        return {
          height: s.minHeight,
          radius: s.borderRadius,
          font: s.fontSize,
          background: s.backgroundColor,
        };
      }),
    );
  expect(controls).toHaveLength(3);
  expect(
    controls.every((c) => JSON.stringify(c) === JSON.stringify(controls[0])),
  ).toBe(true);
  const text = await page.locator("body").innerText();
  expect(text).not.toMatch(/[\u0102-\u01b0\u1ea0-\u1ef9]/);
  await expect(
    page.getByRole("button", { name: /comparison|algorithm/i }),
  ).toHaveCount(0);
  await expect(
    page.locator("#time-limit").locator("..").locator("small"),
  ).toHaveCount(0);
  await page.screenshot({
    path: "../.papercraft/ui-english-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.papercraft/ui-english-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("long fabrication errors use a concise message with expandable diagnostics", async ({
  page,
}) => {
  const detail =
    "No certified design found in the bounded color-order search: " +
    "IMPORTANT_FEATURE_CHANGED: local retained/cutout budget exceeded; ".repeat(
      8,
    );
  await page.route("**/projects", (route) =>
    route.fulfill({ status: 422, json: { error: { message: detail } } }),
  );
  await page.goto("/");
  await page
    .locator('input[type="file"]')
    .first()
    .setInputFiles({
      name: "sample.png",
      mimeType: "image/png",
      buffer: Buffer.from("intercepted upload"),
    });
  const alert = page.getByRole("alert");
  await expect(
    alert.getByText(
      "No cut-ready design found. Adjust the size or material profile and try again.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(alert.getByText(detail, { exact: true })).not.toBeVisible();
  await alert.getByText("Error details", { exact: true }).click();
  await expect(alert.getByText(detail, { exact: true })).toBeVisible();
});
