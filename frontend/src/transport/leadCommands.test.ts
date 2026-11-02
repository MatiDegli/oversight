import { expect, it, vi } from "vitest";
import { createCommandClient } from "./commands";

it("starts with the backend default model and returns the created identity", async () => {
  const request = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ agent_id: "lead-1" }, { status: 201 }));
  await expect(createCommandClient(request).startLead({ task: "Coordinate" })).resolves.toEqual({
    agent_id: "lead-1",
  });
  expect(request).toHaveBeenCalledWith(
    "/leads",
    expect.objectContaining({ method: "POST", body: '{"task":"Coordinate"}' }),
  );
});
it.each(["messageLead", "stopLead"] as const)(
  "%s posts encoded lead IDs and accepts empty 202",
  async (method) => {
    const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 202 }));
    const client = createCommandClient(request);
    if (method === "messageLead") await client.messageLead("id/ ?#", { text: "Next" });
    else await client.stopLead("id/ ?#", { revision: 9 });
    expect(request).toHaveBeenCalledExactlyOnceWith(
      `/leads/id%2F%20%3F%23/${method === "messageLead" ? "message" : "stop"}`,
      {
        method: "POST",
        signal: undefined,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(method === "messageLead" ? { text: "Next" } : { revision: 9 }),
      },
    );
  },
);
it.each([404, 409, 501, "network"])(
  "preserves %s refusals without retrying any lead command",
  async (status) => {
    const request = vi.fn<typeof fetch>();
    if (status === "network") request.mockRejectedValue(new TypeError("Network unavailable"));
    else
      request.mockImplementation(() =>
        Promise.resolve(
          Response.json(
            { error: { code: "wrong_state", message: "Lead command refused" } },
            { status: Number(status) },
          ),
        ),
      );
    const client = createCommandClient(request);
    for (const command of [
      () => client.startLead({ task: "Coordinate" }),
      () => client.messageLead("lead", { text: "Next" }),
      () => client.stopLead("lead", { revision: 7 }),
    ]) {
      await expect(command()).rejects.toThrow(
        status === "network" ? "Network unavailable" : "Lead command refused",
      );
    }
    expect(request).toHaveBeenCalledTimes(3);
  },
);
