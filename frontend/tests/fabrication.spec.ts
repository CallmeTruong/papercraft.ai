import { expect, test } from "@playwright/test";

test("forest reference exposes five sheets, prints PDF, and invalidates cut-ready after an edit", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Mẫu rừng–hươu 5 lớp" }).click();
  await expect(page.locator('[data-testid="layer-row"]')).toHaveCount(5);
  await page.getByText("Tùy chỉnh nâng cao").click();
  await expect(page.getByLabel("Vai trò deer", { exact: true })).toHaveValue(
    "retain",
  );
  await expect(
    page.getByLabel("Khóa vật thể deer", { exact: true }),
  ).toBeChecked();
  const cutReady = page.getByRole("button", {
    name: "Xuất cut-ready",
    exact: true,
  });
  await expect(cutReady).toBeEnabled();
  await page.getByLabel("Khổ giấy in").selectOption("A3");
  await page.getByLabel("Mặt in", { exact: true }).selectOption("back");
  const pending = page.waitForEvent("download");
  await page.getByLabel("Định dạng xuất thêm").selectOption("pdf");
  expect((await pending).suggestedFilename()).toContain(".pdf");
  await page.getByLabel("Màu lớp layer-01").fill("#aabbcc");
  await expect(cutReady).toBeDisabled();
  await page
    .getByRole("button", { name: "Kiểm tra thiết kế", exact: true })
    .click();
  await expect(cutReady).toBeEnabled();
  await page.reload();
  await expect(page.getByLabel("Màu lớp layer-01")).toHaveValue("#aabbcc");
  await expect(page.getByLabel("Viền khung (mm)")).toHaveValue("10");
});

test("stencil family exposes styles and frame-connected fabrication controls on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Stencil", exact: true }).click();
  await page.getByLabel("Phong cách", { exact: true }).selectOption("detailed");
  await page.getByLabel("Viền khung (mm)").fill("12");
  await expect(page.getByLabel("Cách xếp lớp")).toHaveValue("flat");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
