import { expect, it, vi } from "vitest";
import { createCommandClient } from "./commands";

it.each(["startPlan", "rejectPlan", "cancelQueue"] as const)(
  "%s encodes its target, posts its body, accepts empty 202 and retains refusals",
  async (method) => {
    const request = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 202 }))
      .mockResolvedValueOnce(
        Response.json(
          { error: { code: "wrong_state", message: "The observed revision differs" } },
          { status: 409 },
        ),
      );
    const client = createCommandClient(request);
    const body = method === "rejectPlan" ? { revision: 9, comment: "Later" } : { revision: 9 };
    await expect(client[method]("id/ ?#", body)).resolves.toBeUndefined();
    const path =
      method === "cancelQueue"
        ? "/queue/id%2F%20%3F%23/cancel"
        : `/plans/id%2F%20%3F%23/${method === "startPlan" ? "start" : "reject"}`;
    expect(request).toHaveBeenCalledWith(path, {
      method: "POST",
      signal: undefined,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await expect(client[method]("id", body)).rejects.toMatchObject({
      status: 409,
      message: "The observed revision differs",
    });
  },
);
