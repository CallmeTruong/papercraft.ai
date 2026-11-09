import { expect, test } from "@playwright/test";

test("AI controls expose independent choices and explicit local asset state", async ({
  page,
}) => {
  await page.route("**/api/models", (route) =>
    route.fulfill({
      json: {
        segmentation: { status: "prepared" },
        depth: { status: "unavailable" },
        face_features: { status: "prepared" },
        important_details: { status: "prepared" },
      },
    }),
  );
  await page.goto("/");
  await page.getByText("Advanced settings", { exact: false }).click();
  const segment = page.getByRole("checkbox", {
    name: "Object segmentation",
    exact: true,
  });
  const face = page.getByRole("checkbox", {
    name: "Protect facial features",
    exact: true,
  });
  const depth = page.getByRole("checkbox", {
    name: "Depth estimation",
    exact: true,
  });
  await expect(segment).toBeChecked();
  await expect(face).toBeChecked();
  await expect(depth).toBeChecked();
  await segment.uncheck();
  await face.uncheck();
  await depth.uncheck();
  await segment.check();
  await face.check();
  await expect(depth).not.toBeChecked();
  await expect(
    page.getByLabel("AI subject bounds", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("AI subject bounds", { exact: true })
    .fill("0.2,0.1,0.8,0.9");
  await depth.check();
  await segment.uncheck();
  await expect(face).toBeChecked();
  await expect(depth).toBeChecked();
  await expect(
    page.getByLabel("AI subject bounds", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("Fallback", { exact: true })).toBeVisible();
});
