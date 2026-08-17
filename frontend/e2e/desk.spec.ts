import { expect, test } from "@playwright/test";

const setup = {
  client_configured: false,
  google_connected: false,
  google_email: null,
  source_folder_configured: false,
  kindle_destination_configured: false,
  ready: false,
};

test("compiled SPA exposes setup and review journeys", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const payload: Record<string, unknown> = {
      "/api/setup/status": setup,
      "/api/settings": {
        google_email: null,
        source_folder_id: null,
        source_folder_name: null,
        kindle_email: null,
        preset: {
          kindle_profile: "KPW6",
          reading_direction: "rtl",
          spread_mode: "both",
          crop_mode: "margins_and_page_numbers",
        },
      },
      "/api/scans": [],
      "/api/jobs": [],
      "/api/candidates": [],
      "/api/kindle-profiles": [{ id: "KPW6", name: "Kindle Paperwhite 6" }],
    };
    await route.fulfill({ json: payload[path] ?? {} });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Your reading dispatch desk/i })).toBeVisible();
  await expect(page.getByText("Finish the shelf setup")).toBeVisible();

  await page.getByRole("link", { name: /Review/ }).click();
  await expect(page.getByRole("heading", { name: /Choose what reaches the next shelf/i })).toBeVisible();
  await expect(page.getByText("No candidates are waiting")).toBeVisible();
});
