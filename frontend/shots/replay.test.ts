import assert from "node:assert/strict";
import type { ServerResponse } from "node:http";
import { test } from "node:test";

import { streamReplay } from "./replay.ts";

void test("the editing player streams the cut by cursor and stays open until the browser disconnects", () => {
  const writes: string[] = [];
  let status: number | undefined;
  let headers: unknown;
  let disconnect: (() => void) | undefined;
  const response = {
    writeHead: (code: number, values: unknown) => {
      status = code;
      headers = values;
    },
    write: (text: string) => {
      writes.push(text);
    },
    on: (event: string, callback: () => void) => {
      assert.equal(event, "close");
      disconnect = callback;
    },
    end: () => assert.fail("SSE must not end while capturing the scene"),
  };
  streamReplay(response as unknown as ServerResponse, '{"seq":1}\n{"seq":2}\n', 1);
  try {
    assert.equal(status, 200);
    assert.deepEqual(headers, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Access-Control-Allow-Origin": "*",
    });
    assert.deepEqual(writes, ['id: 2\ndata: {"seq":2}\n\n']);
    assert.ok(disconnect);
  } finally {
    disconnect?.();
  }
});
