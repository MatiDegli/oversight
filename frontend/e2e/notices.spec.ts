import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

test("static replay ships the exact third-party notices as a plain root file", async ({
  request,
}) => {
  const response = await request.get("/THIRD_PARTY_NOTICES.md");
  expect(response.status()).toBe(200);
  expect(await response.text()).toBe(
    readFileSync(new URL("../../THIRD_PARTY_NOTICES.md", import.meta.url), "utf8"),
  );
  const monaco = await request.get("/monaco-editor.ThirdPartyNotices.txt");
  expect(monaco.status()).toBe(200);
  expect(await monaco.text()).toBe(
    readFileSync(
      new URL("../node_modules/monaco-editor/ThirdPartyNotices.txt", import.meta.url),
      "utf8",
    ),
  );
});
