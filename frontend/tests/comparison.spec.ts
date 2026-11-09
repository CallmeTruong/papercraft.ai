import { expect, test } from "@playwright/test";

test("comparison controls and background requests are absent", async ({
  page,
}) => {
  const project = await (
    await page.request.post("/references/forest-deer?mode=multicolor")
  ).json();
  const requests: string[] = [];
  page.on("request", (request) => {
    if (/\/compar(e|isons)(\/|$)/.test(new URL(request.url()).pathname))
      requests.push(request.url());
  });
  await page.goto(`/?project=${project.id}`);
  await expect(
    page.getByRole("button", { name: /compar|algorithm/i }),
  ).toHaveCount(0);
  await expect(page.locator(".comparison-panel")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Regenerate", exact: true }),
  ).toBeVisible();
  expect(requests).toEqual([]);
  await expect(page.getByTestId("flat-preview")).toBeVisible();
  await page.screenshot({
    path: "../.papercraft/ui-english-reference.png",
    fullPage: true,
  });
});
