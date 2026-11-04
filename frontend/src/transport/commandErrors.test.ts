import { expect, it, vi } from "vitest";
import { CommandError, createCommandClient } from "./commands";

async function refusal(missing: unknown) {
  const request = vi.fn<typeof fetch>(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          error: { code: "not_configured", message: "Server setup is incomplete.", missing },
        }),
        { status: 503 },
      ),
    ),
  );
  return createCommandClient(request)
    .startAgent({ task: "Keep this task" })
    .catch((error: unknown) => error);
}

it("keeps only setting names and problems, including unknown names, without values", async () => {
  const error = await refusal([
    { name: "OVERSIGHT_BUDGET_USD", problem: "missing", slots: ["A"] },
    { name: "FUTURE_SETTING", problem: "invalid", value: "must never be displayed" },
  ]);
  expect(error).toBeInstanceOf(CommandError);
  expect(error).toMatchObject({
    status: 503,
    code: "not_configured",
    message: "Server setup is incomplete.",
    missing: [
      { name: "OVERSIGHT_BUDGET_USD", problem: "missing" },
      { name: "FUTURE_SETTING", problem: "invalid" },
    ],
  });
  expect(JSON.stringify(error)).not.toContain("must never be displayed");
});

it.each([undefined, null, "missing", 3, {}, { name: "META_API_KEY", problem: "missing" }])(
  "ignores a non-array missing field: %j",
  async (missing) => {
    const error = await refusal(missing);
    expect(error).toBeInstanceOf(CommandError);
    expect(error).toHaveProperty("missing", undefined);
  },
);

it("ignores malformed entries while retaining valid entries in order", async () => {
  const error = await refusal([
    null,
    "META_API_KEY",
    5,
    [],
    {},
    { name: 5, problem: "missing" },
    { name: "META_API_KEY" },
    { name: "META_API_KEY", problem: "other" },
    { name: "META_API_KEY", problem: null },
    { name: "BASETEN_API_KEY", problem: "missing" },
  ]);
  expect(error).toHaveProperty("missing", [{ name: "BASETEN_API_KEY", problem: "missing" }]);
});
