import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

test("upload starts generation automatically and shows stage, progress and elapsed time", async ({
  page,
}) => {
  let polls = 0;
  await page.route(/\/jobs\/[a-f0-9]+$/, async (route) => {
    if (route.request().method() === "GET" && polls++ < 3) {
      await route.fulfill({
        json: {
          state: "running",
          stage: "validate:layer-02",
          progress: 0.7,
          elapsed_seconds: 12,
        },
      });
    } else await route.continue();
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Dùng ảnh mẫu" }).click();
  await expect(page.getByRole("status")).toContainText(
    "Kiểm tra liền khung và độ rộng",
  );
  await expect(page.getByRole("status")).toContainText("70%");
  await expect(page.getByRole("status")).toContainText("12 giây");
  await expect(page.getByText("Thiết kế đã tạo")).toBeVisible({
    timeout: 45000,
  });
});

test("CPU workflow edits, invalidates, validates and downloads fabrication bundle", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Từ một bức ảnh, thành từng lớp giấy." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Dùng ảnh mẫu" }).click();
  await expect(page.getByText("Thiết kế đã tạo")).toBeVisible({
    timeout: 45000,
  });
  const layers = page.locator('[data-testid="layer-row"]');
  await expect(layers.first()).toBeVisible();
  expect(await layers.count()).toBeGreaterThanOrEqual(2);
  expect(await layers.count()).toBeLessThanOrEqual(8);
  await page.screenshot({
    path: "../docs/screenshots/studio-desktop.png",
    fullPage: true,
  });
  await page.getByLabel("Màu lớp layer-01").fill("#aabbcc");
  await expect(page.getByText("Cần kiểm tra lại")).toBeVisible();
  await page.getByRole("button", { name: "Kiểm tra thiết kế" }).click();
  await expect(page.getByText("Đang kiểm tra")).not.toBeVisible({
    timeout: 45000,
  });
  await page.getByRole("button", { name: "Xem tách lớp" }).click();
  await expect(page.locator("canvas")).toBeVisible();
  await page.getByRole("button", { name: "Xem phẳng" }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Tải ZIP bản nháp" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain(".zip");
  await page.reload();
  await expect(page.getByLabel("Màu lớp layer-01")).toHaveValue("#aabbcc");
});

test("Lightbox runs without an AI dependency and supports mobile layout", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Depth Scene" }).click();
  await page.getByRole("button", { name: "Dùng ảnh mẫu" }).click();
  await expect(page.getByText("Thiết kế đã tạo")).toBeVisible({
    timeout: 45000,
  });
  await page.getByRole("button", { name: "Xem ánh sáng" }).click();
  await expect(page.getByLabel("Cường độ ánh sáng")).toBeVisible();
  const overflowing = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflowing).toBe(false);
  await page.screenshot({
    path: "../docs/screenshots/studio-mobile.png",
    fullPage: true,
  });
});

test("manual Lightbox assignments remain manual after reopening and regenerating", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Depth Scene", exact: true }).click();
  await page.getByRole("button", { name: "Dùng ảnh mẫu" }).click();
  await expect(page.getByText("Thiết kế đã tạo")).toBeVisible({
    timeout: 45000,
  });
  await page.getByText("Tùy chỉnh nâng cao").click();
  await page.locator("#depth-mode").selectOption("manual");
  await page.getByLabel("Chiều sâu vùng 1", { exact: true }).selectOption("6");
  await page.getByRole("button", { name: "Tạo lại thiết kế" }).click();
  await expect(page.getByText("Thiết kế đã tạo")).toBeVisible({
    timeout: 45000,
  });
  await page.reload();
  await page.getByText("Tùy chỉnh nâng cao").click();
  await expect(page.locator("#depth-mode")).toHaveValue("manual");
  await expect(
    page.getByLabel("Chiều sâu vùng 1", { exact: true }),
  ).toHaveValue("6");
});

test("exported SVG rasterizes back to the physical paper mask", async ({
  page,
}) => {
  await page.goto("/");
  const config = { colors: 3, layers: 3, detail: 1, width_mm: 200 };
  const upload = await page.request.post("/projects", {
    multipart: {
      image: {
        name: "fixture.png",
        mimeType: "image/png",
        buffer: readFileSync(
          "../tests/fixtures/001-image-abstraction/two-objects.png",
        ),
      },
      config: JSON.stringify(config),
    },
  });
  expect(upload.status()).toBe(201);
  const project = await upload.json();
  async function wait(id: string) {
    for (let i = 0; i < 200; i++) {
      const job = await (await page.request.get(`/jobs/${id}`)).json();
      if (job.state === "succeeded") return;
      if (job.state === "failed") throw new Error(job.error.message);
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error("Job timed out");
  }
  const generation = await (
    await page.request.post(`/projects/${project.id}/generate`, {
      data: { expected_revision_id: project.revision_id, config },
    })
  ).json();
  await wait(generation.job_id);
  const design = await (
    await page.request.get(`/projects/${project.id}`)
  ).json();
  const job = await (
    await page.request.post(`/projects/${project.id}/exports`, {
      data: {
        revision_id: design.revision_id,
        format: "svg",
        intent: "draft",
        layer_id: design.layers[0].id,
      },
    })
  ).json();
  await wait(job.job_id);
  const ratio = await page.evaluate(
    async ({ id, width, height }) => {
      const blob = await (await fetch(`/jobs/${id}/download`)).blob();
      const url = URL.createObjectURL(blob);
      const image = new Image();
      image.src = url;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = 96;
      canvas.height = 64;
      const context = canvas.getContext("2d")!;
      context.drawImage(image, 0, 0, 96, 64);
      const pixels = context.getImageData(0, 0, 96, 64).data;
      let mismatch = 0,
        union = 0;
      for (let y = 0; y < 64; y++)
        for (let x = 0; x < 96; x++) {
          const ax = ((((x + 0.5) * width) / 96 - 3) * 96) / (width - 6);
          const ay = ((((y + 0.5) * height) / 64 - 3) * 64) / (height - 6);
          const expected = !(
            (ax >= 16 && ax < 48 && ay >= 12 && ay < 52) ||
            (ax >= 60 && ax < 84 && ay >= 20 && ay < 44)
          );
          const actual = pixels[(y * 96 + x) * 4 + 3] >= 128;
          if (expected !== actual) mismatch++;
          if (expected || actual) union++;
        }
      URL.revokeObjectURL(url);
      return mismatch / union;
    },
    { id: job.job_id, width: design.width_mm, height: design.height_mm },
  );
  expect(ratio).toBeLessThanOrEqual(0.01);
  writeFileSync(
    "../benchmarks/svg-roundtrip.json",
    JSON.stringify(
      {
        fixture: "two-objects",
        layer: "front sheet",
        renderer: "Playwright Chromium",
        xor_union_ratio: ratio,
        threshold: 0.01,
      },
      null,
      2,
    ),
  );
});
