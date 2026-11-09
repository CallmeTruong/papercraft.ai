import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem("papercraft-photo-overlay-v2", "false"),
  );
});

test("overlapping depth sheets show three transmission levels in uniform light and respond to opacity", async ({
  page,
}) => {
  await page.goto("/");
  const encoded = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 72;
    canvas.height = 48;
    const ctx = canvas.getContext("2d")!;
    ["#1e3c50", "#6e96aa", "#f5f5f5"].forEach((color, i) => {
      ctx.fillStyle = color;
      ctx.fillRect(i * 24, 0, 24, 48);
    });
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const config = {
    mode: "lightbox",
    colors: 3,
    layers: 3,
    max_side: 72,
    detail: 1,
    frame_mm: 10,
    workflow: {
      family: "depth_scene",
      style: "landscape",
      assembly: "spaced",
      lighting: "backlit",
    },
  };
  const project = await (
    await page.request.post("/projects", {
      multipart: {
        image: {
          name: "strips.png",
          mimeType: "image/png",
          buffer: Buffer.from(encoded, "base64"),
        },
        config: JSON.stringify(config),
      },
    })
  ).json();
  const job = await (
    await page.request.post(`/projects/${project.id}/generate`, {
      data: {
        expected_revision_id: project.revision_id,
        config,
        depths: { 0: 0, 1: 1, 2: 2 },
        source_lighting: false,
        paper_transmittance: 0.5,
      },
    })
  ).json();
  for (let i = 0; i < 100; i++) {
    const state = await (await page.request.get(`/jobs/${job.job_id}`)).json();
    if (state.state === "succeeded") break;
    if (state.state === "failed") throw Error(state.error.message);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  await page.goto(`/?project=${project.id}`);
  await page
    .getByRole("button", { name: "Light preview", exact: true })
    .click();
  await expect(page.getByLabel("Light source", { exact: true })).toHaveValue(
    "uniform",
  );
  const table = page.getByLabel("Paper transmission profile", {
    exact: true,
  });
  await table.locator("summary").click();
  await expect(table.getByText("12.50%", { exact: true })).toBeVisible();
  await expect(table.getByText("25.00%", { exact: true })).toBeVisible();
  await expect(table.getByText("50.00%", { exact: true })).toBeVisible();
  await expect(page.locator(".three-scene")).toHaveAttribute(
    "data-light-fields-loaded",
    "4",
  );
  await page.screenshot({
    path: "../.papercraft/depth-transmission-50.png",
    fullPage: true,
  });
  await page.getByLabel("Paper transmission", { exact: true }).fill("0.1");
  await expect(table.getByText("0.10%", { exact: true })).toBeVisible();
  await expect(page.locator(".three-scene")).toHaveAttribute(
    "data-light-fields-loaded",
    "4",
  );
  const current = await (
    await page.request.get(`/projects/${project.id}`)
  ).json();
  expect(current.light_plan.mode).toBe("uniform_transmission");
  expect(current.report.status).toBe("PASS");
  await page.screenshot({
    path: "../.papercraft/depth-transmission-browser.png",
    fullPage: true,
  });
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes("/light-preview/") &&
        new URL(response.url()).searchParams.get("source_gain") === "128" &&
        response.status() === 200,
    ),
    page.getByLabel("Light intensity", { exact: true }).fill("7"),
  ]);
  await expect(page.getByText("128×", { exact: true })).toBeVisible();
  await expect(page.locator(".three-scene")).toHaveAttribute(
    "data-light-fields-loaded",
    "4",
  );
  await page.screenshot({
    path: "../.papercraft/light-gain-browser.png",
    fullPage: true,
  });
  await page.getByLabel("Paper transmission", { exact: true }).fill("1");
  await expect(table.getByText("100.00%", { exact: true })).toHaveCount(3);
});
