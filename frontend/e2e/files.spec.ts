import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { ServerEvent } from "@contracts";

const events = readFileSync(
  new URL("../../fixtures/events/recorded-demo.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const update = events.find((event) => event.type === "change.updated");
if (!update) throw new Error("Recording has no file edit");
const path = update.payload.file.path;

test("the static Files tree opens recorded source read-only without backend requests", async ({
  page,
}) => {
  const backend: string[] = [];
  const errors: string[] = [];
  page.on("request", (request) => {
    if (
      /^\/(?:oversight\/)?(?:tree|files|graph|events|health)(?:[/?]|$)/.test(
        new URL(request.url()).pathname,
      )
    )
      backend.push(request.url());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/oversight/");
  await expect(page.locator("#root")).toHaveAttribute("data-seq", String(events.at(-1)?.seq), {
    timeout: 60_000,
  });
  await page.getByRole("button", { name: "Files", exact: true }).click();
  const sidebar = page.getByRole("complementary", { name: "Files sidebar" });
  await expect(sidebar).toContainText("Recorded files");
  await expect(sidebar).not.toContainText(/\blive\b/i);
  await sidebar.getByRole("treeitem", { name: path.split("/").at(-1), exact: true }).click();
  const file = page.getByRole("complementary", { name: "File view" });
  await expect(file.getByRole("heading", { name: path, exact: true })).toBeVisible();
  await expect(file).toContainText("Read-only · Recorded file");
  await expect(file.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
  await expect(file.locator(".monaco-editor .view-lines").first()).toContainText("def ");
  await expect(file.getByRole("alert")).toHaveCount(0);
  expect({ backend, errors }).toEqual({ backend: [], errors: [] });
});
