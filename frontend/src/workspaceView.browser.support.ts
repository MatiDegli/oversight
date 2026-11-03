import type { Page } from "@playwright/test";

/** Enter the graph through the same view selector as the person, including retained fixtures. */
export async function openOversight(page: Page) {
  const button = page
    .getByRole("group", { name: "Workspace view" })
    .getByRole("button", { name: "Oversight", exact: true });
  if ((await button.getAttribute("aria-pressed")) !== "true") await button.click();
}
