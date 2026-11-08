import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

async function candidate(page: Page, production: boolean) {
  const config = {
    colors: 3,
    layers: 3,
    workflow: {
      family: "stencil",
      style: "bold",
      assembly: "flat",
      lighting: "backlit",
    },
  };
  const response = await page.request.post("/projects", {
    multipart: {
      image: {
        name: "two-objects.png",
        mimeType: "image/png",
        buffer: readFileSync(
          "../tests/fixtures/001-image-abstraction/two-objects.png",
        ),
      },
      config: JSON.stringify(config),
    },
  });
  const p = await response.json();
  const job = await (
    await page.request.post(`/projects/${p.id}/generate`, {
      data: { expected_revision_id: p.revision_id, config, production },
    })
  ).json();
  for (let i = 0; i < 300; i++) {
    const state = await (await page.request.get(`/jobs/${job.job_id}`)).json();
    if (state.state === "succeeded") break;
    if (state.state === "failed") throw new Error(state.error.message);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const result = await (await page.request.get(`/projects/${p.id}`)).json();
  await page.addInitScript(
    (id) => localStorage.setItem("papercraft-project", id),
    p.id,
  );
  await page.goto("/");
  await expect(page.locator('[data-testid="layer-row"]')).toHaveCount(1);
  return result;
}

test("unfabricable draft is labeled and production action yields a certified connected sheet", async ({
  page,
}) => {
  const draft = await candidate(page, false);
  expect(draft.report.status).toBe("FAIL");
  await expect(
    page.getByText("BẢN PHÂN TÍCH — CHƯA THỂ CHẾ TÁC"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Xuất cut-ready", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Tạo bản chế tác đạt kiểm tra", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Xuất cut-ready", exact: true }),
  ).toBeEnabled({ timeout: 45000 });
  const result = await (await page.request.get(`/projects/${draft.id}`)).json();
  expect(result.report.status).toBe("PASS");
  expect(result.layers[0].geometry.type).toBe("Polygon");
  expect(result.production_receipts[0].bridge_count).toBeGreaterThan(0);
  await expect(
    page.getByText("BẢN PHÂN TÍCH — CHƯA THỂ CHẾ TÁC"),
  ).not.toBeVisible();
});

test("source-color light uses registered red-blue image fields and exports a printable diffuser", async ({
  page,
}) => {
  const p = await candidate(page, true);
  expect(p.report.status).toBe("PASS");
  expect(p.light_plan.mode).toBe("source_color_filter");
  const loaded = page.waitForResponse(
    (response) =>
      response.url().includes("/light-preview/layer-01") &&
      response.status() === 200,
  );
  await page.getByRole("button", { name: "Xem ánh sáng", exact: true }).click();
  const field = await loaded;
  await expect(page.locator(".three-scene")).toHaveAttribute(
    "data-light-fields-loaded",
    "2",
  );
  await expect(page.getByLabel("Nguồn màu ánh sáng")).toHaveValue("source");
  const hues = await page.evaluate(async (url) => {
    const img = new Image();
    img.src = url;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    const sample = (x: number) =>
      Array.from(
        ctx.getImageData(
          Math.floor(c.width * x),
          Math.floor(c.height * 0.5),
          1,
          1,
        ).data,
      );
    return { red: sample(0.33), blue: sample(0.75) };
  }, field.url());
  expect(hues.red[0]).toBeGreaterThan(hues.red[2] * 2);
  expect(hues.blue[2]).toBeGreaterThan(hues.blue[0] * 2);
  const download = page.waitForEvent("download");
  await page.getByLabel("Định dạng xuất thêm").selectOption("light_filter");
  expect((await download).suggestedFilename()).toContain(".pdf");
});
