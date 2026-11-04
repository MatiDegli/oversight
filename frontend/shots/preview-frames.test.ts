import assert from "node:assert/strict";
import { IncomingMessage, ServerResponse } from "node:http";
import { Socket } from "node:net";
import { resolve } from "node:path";

import { resolveConfig, type Connect, type ViteDevServer } from "vite";

// Keep the shots Node test gate and allow focused Vitest runs of the same assertions.
const test: (name: string, run: () => Promise<void>) => unknown = process.env.VITEST
  ? (await import("vitest")).test
  : (await import("node:test")).test;
const configFile = resolve(import.meta.dirname, "../vite.config.ts");

async function middleware(mode: string, pool: string, policy?: string) {
  const before = process.env.OVERSIGHT_PREVIEW_PORTS;
  process.env.OVERSIGHT_PREVIEW_PORTS = pool;
  try {
    const config = await resolveConfig(
      {
        configFile,
        configLoader: "runner",
        envDir: false,
        appType: policy === undefined ? "spa" : "custom",
        server: { headers: policy === undefined ? {} : { "Content-Security-Policy": policy } },
      },
      "serve",
      mode,
    );
    const plugin = config.plugins.find((entry) => entry.name === "preview-frame-authorities");
    assert.equal(typeof plugin?.configureServer, "function");
    assert.ok(plugin && typeof plugin.configureServer === "function");
    let handler: Connect.NextHandleFunction | undefined;
    await plugin.configureServer.call(
      {} as never,
      {
        config,
        middlewares: {
          use(callback: Connect.NextHandleFunction) {
            handler = callback;
          },
        },
      } as unknown as ViteDevServer,
    );
    assert.ok(handler);
    return { handler, config };
  } finally {
    if (before === undefined) delete process.env.OVERSIGHT_PREVIEW_PORTS;
    else process.env.OVERSIGHT_PREVIEW_PORTS = before;
  }
}

function request(url: string, hosts: string[], origin = "http://foreign.invalid") {
  const incoming = new IncomingMessage(new Socket());
  incoming.url = url;
  incoming.headers = { host: hosts[0], origin };
  incoming.rawHeaders = hosts.flatMap((host) => ["Host", host]);
  return incoming;
}

for (const [mode, pool, custom] of [
  ["development", "", undefined],
  ["development", "19501,19502", undefined],
  ["development", "19501", "frame-src https://example.test"],
  ["replay", "19501", undefined],
  ["replay", "19501", "frame-src https://example.test"],
] as const) {
  void test(`document authority and CSP: ${mode}, ${pool}, ${String(custom)}`, async () => {
    const { handler, config } = await middleware(mode, pool, custom);
    const vite = String(config.server.port);
    for (const hosts of [["127.0.0.1:1"], [], [`localhost:${vite}`, `localhost:${vite}`]]) {
      const incoming = request("/index.html", hosts);
      const response = new ServerResponse(incoming);
      handler(incoming, response, () => assert.fail("invalid document authority passed"));
      assert.equal(response.statusCode, 403);
      assert.ok(response.writableEnded);
      assert.equal(response.getHeader("Content-Security-Policy"), "frame-src 'none'");
    }
    for (const parent of ["localhost", "127.0.0.1"]) {
      const incoming = request("/index.html", [`${parent}:${vite}`]);
      const response = new ServerResponse(incoming);
      let passed = 0;
      handler(incoming, response, () => passed++);
      const frame = parent === "localhost" ? "127.0.0.1" : "localhost";
      const expected =
        mode === "replay"
          ? "frame-src 'none'"
          : custom ||
            (pool &&
              `frame-src ${pool
                .split(",")
                .map((p) => `http://${frame}:${p}`)
                .join(" ")}`) ||
            "frame-src 'none'";
      assert.equal(response.getHeader("Content-Security-Policy"), expected);
      assert.equal(passed, 1);
      assert.equal(response.writableEnded, false);
    }
  });

  void test(`proxy requests pass untouched: ${mode}, ${pool}, ${String(custom)}`, async () => {
    const { handler, config } = await middleware(mode, pool, custom);
    for (const path of Object.keys(config.server.proxy ?? {})) {
      // Vite matches prefixes, including descendants, query strings and suffixes.
      for (const suffix of ["", "/child", "?after=1", "-suffix"]) {
        const incoming = request(path + suffix, ["127.0.0.1:1"]);
        const response = new ServerResponse(incoming);
        const headers = { ...incoming.headers };
        const rawHeaders = [...incoming.rawHeaders];
        let passed = 0;
        handler(incoming, response, () => passed++);
        assert.equal(passed, 1);
        assert.equal(response.writableEnded, false);
        assert.deepEqual(response.getHeaderNames(), []);
        assert.deepEqual(incoming.headers, headers);
        assert.deepEqual(incoming.rawHeaders, rawHeaders);
        assert.equal(incoming.url, path + suffix);
      }
    }
  });
}
