import { expect, test } from "@playwright/test";

test("ambient depth preserves colored subject details and exposes a separate appearance check", async ({
  page,
}) => {
  await page.goto("/");
  const data = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 80;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#23aa23";
    ctx.fillRect(0, 0, 80, 80);
    ctx.fillStyle = "#d29650";
    ctx.fillRect(10, 20, 60, 60);
    ctx.fillStyle = "#23aa23";
    ctx.fillRect(22, 35, 12, 11);
    ctx.fillRect(47, 35, 12, 11);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const config = {
    colors: 2,
    layers: 3,
    max_side: 80,
    frame_mm: 6,
    workflow: {
      family: "depth_scene",
      style: "landscape",
      assembly: "spaced",
      lighting: "ambient",
    },
  };
  const project = await (
    await page.request.post("/projects", {
      multipart: {
        image: {
          name: "subject.png",
          mimeType: "image/png",
          buffer: Buffer.from(data, "base64"),
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
      },
    })
  ).json();
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/jobs/${job.job_id}`)).json()).state,
    )
    .toBe("succeeded");
  const current = await (
    await page.request.get(`/projects/${project.id}`)
  ).json();
  expect(current.report.image_fidelity.status).toBe("PASS");
  expect(
    new Set(current.layers.map((l: { color: string }) => l.color)).size,
  ).toBe(2);
  expect(current.layers.length).toBeLessThanOrEqual(3);
  await page.goto(`/?project=${project.id}`);
  await expect(page.getByLabel("Image quality", { exact: true })).toContainText(
    "Checks passed",
  );

  await page
    .getByRole("button", { name: "Assembly view", exact: true })
    .click();
  await expect(page.locator(".three-scene canvas")).toBeVisible();
});

test("paper modes and drawn detail regions persist after regeneration", async ({
  page,
}) => {
  await page.goto("/");
  const png = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 80;
    c.height = 80;
    const x = c.getContext("2d")!;
    x.fillStyle = "#dddddd";
    x.fillRect(0, 0, 80, 80);
    x.fillStyle = "#224422";
    x.fillRect(20, 20, 40, 60);
    return c.toDataURL("image/png").split(",")[1];
  });
  const config = {
    mode: "lightbox",
    colors: 2,
    layers: 3,
    max_side: 80,
    frame_mm: 6,
    depth_paper: "white",
    workflow: {
      family: "depth_scene",
      style: "landscape",
      assembly: "spaced",
      lighting: "ambient",
    },
  };
  const p = await (
    await page.request.post("/projects", {
      multipart: {
        image: {
          name: "shape.png",
          mimeType: "image/png",
          buffer: Buffer.from(png, "base64"),
        },
        config: JSON.stringify(config),
      },
    })
  ).json();
  const job = await (
    await page.request.post(`/projects/${p.id}/generate`, {
      data: { expected_revision_id: p.revision_id, config },
    })
  ).json();
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/jobs/${job.job_id}`)).json()).state,
    )
    .toBe("succeeded");
  await page.goto(`/?project=${p.id}`);
  await expect(
    page.getByLabel("Depth Scene paper", { exact: true }),
  ).toHaveValue("white");
  await page
    .getByLabel("Depth Scene paper", { exact: true })
    .selectOption("multicolor");
  await page.getByText(/Detail protection \(/).click();
  const region = page.getByRole("img", {
    name: "Draw detail regions",
    exact: true,
  });
  await region.scrollIntoViewIfNeeded();
  const bounds = (await region.boundingBox())!;
  await page.mouse.move(
    bounds.x + bounds.width * 0.03,
    bounds.y + bounds.height * 0.03,
  );
  await page.mouse.down();
  await page.mouse.move(
    bounds.x + bounds.width * 0.15,
    bounds.y + bounds.height * 0.15,
  );
  await page.mouse.up();
  await expect(
    page.getByText("Detail protection (1/16)", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("checkbox", { name: "Strict protection", exact: true })
    .check();
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect
    .poll(async () => {
      const d = await (await page.request.get(`/projects/${p.id}`)).json();
      return d.config.depth_paper;
    })
    .toBe("multicolor");
  const current = await (await page.request.get(`/projects/${p.id}`)).json();
  expect(current.config.detail_boxes).toHaveLength(1);
  expect(current.config.detail_policy).toBe("strict");
  expect(
    new Set(current.layers.map((l: { color: string }) => l.color)).size,
  ).toBe(2);
  await page.reload();
  await expect(
    page.getByLabel("Depth Scene paper", { exact: true }),
  ).toHaveValue("multicolor");
});
