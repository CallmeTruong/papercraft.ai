import { expect, test } from "@playwright/test";

async function openDepthReference(page: any) {
  const response = await page.request.post(
    "/references/forest-deer?mode=lightbox",
  );
  const project = await response.json();
  await page.addInitScript(
    (id: string) => localStorage.setItem("papercraft-project", id),
    project.id,
  );
  await page.goto("/");
  await expect(page.locator('[data-testid="layer-row"]')).toHaveCount(5);
  return project;
}

async function raster(page: any) {
  return page
    .locator('[data-testid="flat-preview"]')
    .evaluate(async (svg: SVGSVGElement) => {
      const copy = svg.cloneNode(true) as SVGSVGElement;
      copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      copy.setAttribute("width", "800");
      copy.setAttribute("height", "640");
      const url = URL.createObjectURL(
        new Blob([new XMLSerializer().serializeToString(copy)], {
          type: "image/svg+xml",
        }),
      );
      const img = new Image();
      img.src = url;
      await img.decode();
      const canvas = document.createElement("canvas");
      canvas.width = 800;
      canvas.height = 640;
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(img, 0, 0, 800, 640);
      const data = ctx.getImageData(0, 0, 800, 640).data;
      let dark = 0,
        waste = 0,
        frame = 0;
      for (let y = 0; y < 640; y++)
        for (let x = 0; x < 800; x++) {
          const i = (y * 800 + x) * 4;
          const low = Math.min(data[i], data[i + 1], data[i + 2]);
          if (data[i + 3] && low < 210) {
            dark++;
            if (x > 320 && x < 400 && y > 80 && y < 160) waste++;
            if (x > 8 && x < 28 && y > 200 && y < 280) frame++;
          }
        }
      URL.revokeObjectURL(url);
      return { dark, waste, frame };
    });
}

test("single sheet shows hatched waste in UI without marking retained paper", async ({
  page,
}) => {
  await openDepthReference(page);
  await page
    .getByRole("button", { name: "Xem riêng layer-02", exact: true })
    .click();
  const svg = page.getByTestId("flat-preview");
  await expect(svg).toHaveAttribute("data-display-mode", "layer");
  await expect(svg).toHaveAttribute("data-preview-layer", "layer-02");
  await expect(svg.getByTestId("waste-hatch")).toBeVisible();
  const pixels = await raster(page);
  expect(pixels.waste).toBeGreaterThan(20);
  expect(pixels.frame).toBe(0);
  await page.screenshot({
    path: "../docs/screenshots/ui-cut-waste.png",
    fullPage: true,
  });
});

test("same-color depth stack has visible shadows without changing material colors or revision", async ({
  page,
}) => {
  const original = await openDepthReference(page);
  const svg = page.getByTestId("flat-preview");
  await expect(svg).toHaveAttribute("data-display-mode", "stack");
  const shaded = await raster(page);
  await page.getByRole("checkbox", { name: "Đổ bóng giữa các lớp" }).uncheck();
  const flat = await raster(page);
  expect(shaded.dark).toBeGreaterThan(flat.dark + 500);
  await page.getByRole("checkbox", { name: "Đổ bóng giữa các lớp" }).check();
  const current = await (
    await page.request.get(`/projects/${original.id}`)
  ).json();
  expect(current.revision_id).toBe(original.revision_id);
  expect(current.layers.map((l: any) => l.color)).toEqual(
    original.layers.map((l: any) => l.color),
  );
  await page.screenshot({
    path: "../docs/screenshots/ui-depth-shadows.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Xem lắp ráp" }).click();
  await expect(page.locator("canvas")).toBeVisible();
  await page.locator("canvas").evaluate(
    () =>
      new Promise((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve(null)));
      }),
  );
  await page.screenshot({
    path: "../docs/screenshots/ui-depth-assembled.png",
    fullPage: true,
  });
});
