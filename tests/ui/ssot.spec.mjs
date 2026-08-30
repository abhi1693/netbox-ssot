import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const username = process.env.NETBOX_USERNAME;
const password = process.env.NETBOX_PASSWORD;

const pages = [
  ["overview", "/plugins/ssot/"],
  ["sources", "/plugins/ssot/sources/"],
  ["reconciliations", "/plugins/ssot/reconciliations/?period=30"],
  ["agents", "/plugins/ssot/agents/"],
  ["activity", "/plugins/ssot/activity/"]
];

test.beforeEach(async ({ page }) => {
  test.skip(!username || !password, "Set NETBOX_USERNAME and NETBOX_PASSWORD to run authenticated UI checks.");
  await page.goto("/login/");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.endsWith("/login/")),
    page.getByRole("button", { name: "Sign In" }).click()
  ]);
});

for (const [name, path] of pages) {
  test(`${name} has a clear landmark structure and no serious accessibility violations`, async ({ page }, testInfo) => {
    await page.goto(path);
    await expect(page.locator("h1")).toBeVisible();
    await expect(page.locator("main, #page-content").first()).toBeVisible();

    const results = await new AxeBuilder({ page }).analyze();
    const serious = results.violations.filter(({ impact }) => impact === "critical" || impact === "serious");
    expect(serious).toEqual([]);

    await page.screenshot({
      path: testInfo.outputPath(`${name}.png`),
      fullPage: true
    });
  });
}
