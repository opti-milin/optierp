import { test, expect } from "@playwright/test";

// Run against the Docker UI (http://localhost:8080) with the secretarial scenario seeded.
//   cd e2e && BASE_URL=http://localhost:8080 npx playwright test tests/secretarial.spec.ts

test.use({ baseURL: process.env.BASE_URL ?? "http://localhost:8080" });

async function signIn(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
}

test("business shell: Secretarial tile, directors, registers, calendar, meeting", async ({ page }) => {
  await signIn(page, "owner@mangoappliances.com", "Demo!Pass123");

  await expect(page.getByRole("button", { name: "Secretarial" })).toBeVisible();
  await page.getByRole("button", { name: "Secretarial" }).click();
  await page.waitForURL(/\/secretarial/);

  await expect(page.getByText(/Secretarial & Compliance|Mango Appliances/i).first()).toBeVisible();
  await expect(page.getByText("Directors & KMP")).toBeVisible();

  await page.goto("/secretarial/directors");
  await expect(page.getByText("Priya Sharma")).toBeVisible();
  await expect(page.getByText("Rahul Mehta")).toBeVisible();
  await expect(page.getByText("07123456")).toBeVisible();

  await page.goto("/secretarial/registers/members");
  await expect(page.getByRole("button", { name: "Add row" })).toBeVisible();
  await expect(page.getByText("M-001")).toBeVisible();

  await page.goto("/secretarial/compliance");
  await expect(page.getByText(/AGM|MGT-7|AOC-4|Generate/i).first()).toBeVisible();

  await page.goto("/secretarial/meetings");
  await expect(page.getByText("Board meeting — Q2 FY 2025-26")).toBeVisible();

  await page.goto("/secretarial/circulars");
  await expect(page.getByText("Engagement of interior consultant")).toBeVisible();
  await expect(page.getByText("Approval of financial statements (blocked demo)")).toBeVisible();

  await page.goto("/secretarial/filings");
  await expect(page.getByText("DIR-12")).toBeVisible();

  await page.goto("/secretarial/documents");
  await expect(page.getByText("director-appointment")).toBeVisible();
  await expect(page.getByRole("button", { name: "New version" })).toBeVisible();

  await page.goto("/secretarial/facts");
  await expect(page.getByRole("heading", { name: "Financial figures", level: 1 })).toBeVisible();
});

test("practice shell: client roster, no Company details menu", async ({ page }) => {
  await signIn(page, "cs@optireachsecretarial.com", "Demo!Pass123");

  await expect(page.getByRole("button", { name: "Secretarial" })).toBeVisible();
  await page.getByRole("button", { name: "Secretarial" }).click();
  await page.waitForURL(/\/secretarial/);

  await expect(page.getByRole("link", { name: "Clients" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Company details" })).toHaveCount(0);

  await page.goto("/secretarial/clients");
  await expect(page.getByRole("table").getByText("Sunrise Textiles Pvt Ltd")).toBeVisible();
  await expect(page.getByRole("table").getByText("Mehta Advisory LLP")).toBeVisible();
});
