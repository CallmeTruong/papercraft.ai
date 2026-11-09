import { expect, test } from "@playwright/test";

test("old backend is detected before upload and reloading after upgrade clears the warning", async ({
  page,
}) => {
  let upgraded = false;
  let uploads = 0;
  await page.route("**/api/capabilities", (route) =>
    route.fulfill({
      json: upgraded
        ? { config_fields: ["detail_boxes", "detail_policy", "depth_paper"] }
        : { families: ["stencil", "multicolor", "depth_scene"] },
    }),
  );
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/projects"
    )
      uploads++;
  });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "Backend update required",
  );
  await page
    .locator('input[type="file"]')
    .first()
    .setInputFiles({
      name: "image.png",
      mimeType: "image/png",
      buffer: Buffer.from("not sent to the backend"),
    });
  await expect(page.getByRole("alert").last()).toContainText("Restart");
  expect(uploads).toBe(0);
  upgraded = true;
  await page.reload();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
