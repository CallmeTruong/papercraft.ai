import { expect, test } from "@playwright/test";

test("photo insert loads between source and cut sheets, supports strength and leaves revision unchanged", async ({
  page,
}) => {
  const errors: string[] = [];
  const photoRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/light-preview/photo?"))
      photoRequests.push(request.url());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    localStorage.setItem("papercraft-photo-overlay", "false"),
  );
  const project = await (
    await page.request.post("/references/forest-deer?mode=lightbox")
  ).json();
  await page.goto(`/?project=${project.id}`);
  await page
    .getByRole("button", { name: "Light preview", exact: true })
    .click();
  const scene = page.getByLabel("3D preview", { exact: true });
  const toggle = page.getByRole("checkbox", {
    name: "Original photo insert",
    exact: true,
  });
  await expect(toggle).toBeChecked();
  await expect(scene).toHaveAttribute(
    "data-light-fields-loaded",
    String(project.layers.length + 2),
    { timeout: 30000 },
  );
  expect(photoRequests.some((url) => url.includes("photo_overlay=true"))).toBe(
    true,
  );
  await toggle.uncheck();
  await expect(scene).toHaveAttribute(
    "data-light-fields-loaded",
    String(project.layers.length + 1),
    { timeout: 30000 },
  );
  await toggle.check();
  await expect(scene).toHaveAttribute("data-photo-overlay", "true");
  await expect(scene).toHaveAttribute(
    "data-light-fields-loaded",
    String(project.layers.length + 2),
    { timeout: 30000 },
  );
  await expect(
    page.getByLabel("Photo strength", { exact: true }),
  ).toBeVisible();
  const weaker = page.waitForRequest(
    (r) =>
      r.url().includes("/light-preview/photo?") &&
      r.url().includes("photo_strength=0.5"),
  );
  await page.getByLabel("Photo strength", { exact: true }).fill("0.5");
  await weaker;
  await expect(scene).toHaveAttribute(
    "data-light-fields-loaded",
    String(project.layers.length + 2),
    { timeout: 30000 },
  );
  await scene.screenshot({
    path: "../.papercraft/automatic-detail-review/photo-insert-ui.png",
  });
  const current = await (
    await page.request.get(`/projects/${project.id}`)
  ).json();
  expect(current.revision_id).toBe(project.revision_id);
  expect(current.report.status).toBe(project.report.status);
  await page.reload();
  await page
    .getByRole("button", { name: "Light preview", exact: true })
    .click();
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await expect(scene).toHaveAttribute("data-photo-overlay", "false");
  await expect(scene).toHaveAttribute(
    "data-light-fields-loaded",
    String(project.layers.length + 1),
    { timeout: 30000 },
  );
  expect(errors).toEqual([]);
});

test("old backend disables unsupported photo insert instead of showing an unfiltered image", async ({
  page,
}) => {
  const project = await (
    await page.request.post("/references/forest-deer?mode=lightbox")
  ).json();
  await page.route("**/api/capabilities", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    delete data.photo_overlay_preview;
    await route.fulfill({ response, json: data });
  });
  await page.goto(`/?project=${project.id}`);
  await page
    .getByRole("button", { name: "Light preview", exact: true })
    .click();
  await expect(
    page.getByRole("checkbox", {
      name: "Original photo insert",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(
    page.getByText("Restart the backend to enable photo insert.", {
      exact: true,
    }),
  ).toBeVisible();
});
