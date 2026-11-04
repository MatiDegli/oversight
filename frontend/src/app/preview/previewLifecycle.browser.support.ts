import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { IncomingMessage, ServerResponse } from "node:http";
import { expect as check, type BrowserContext, type Page } from "@playwright/test";
import type { PreviewStatus, StartPreview } from "@contracts";
import { chatEvents, previewChatSupport } from "./previewChat.browser.support";
import { captureCheckpoint } from "./previewPanel.browser.support";

export const lifecycleDirectory = fileURLToPath(
  new URL("../../../../.shots/preview-lifecycle/", import.meta.url),
);
type Kind = "start" | "details" | "renew" | "stop";
interface Call {
  kind: Kind;
  previewId: string | null;
  authenticated: boolean;
  delivered: boolean;
}
interface Dispatch {
  kind: Kind;
  keepalive: boolean;
  aborted: boolean;
  unload: boolean;
}
export interface LifecyclePage {
  page: Page;
  context: BrowserContext;
  viewport: { width: number; height: number };
  theme: string;
  pageErrors: string[];
}
const base: StartPreview = {
  run_id: "run-20261027-140312",
  agent_id: "agent-1",
  revision: 1,
  head_commit: "head",
};
function latch() {
  let resolve!: () => void;
  const promise = new Promise<void>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
/** Fake HTTP manager: no executable app, backend, model or credits. Tokens never enter evidence. */
class FixtureAuthority {
  #token = "";
  #serial = 0;
  #status: PreviewStatus | null = null;
  #send: ((status: PreviewStatus) => void) | undefined;
  #started = latch();
  loseNextStop = false;
  calls: Call[] = [];
  invalid: string[] = [];
  reset() {
    this.#status = null;
    this.#token = "";
    this.#serial = 0;
    this.#started = latch();
    this.loseNextStop = false;
    this.calls = [];
    this.invalid = [];
  }
  status() {
    return this.#status;
  }
  arrive() {
    if (this.#status) this.#send?.(this.#status);
  }
  async ready() {
    await this.#started.promise;
    this.#started = latch();
    if (!this.#status) throw new Error("No fixture preview");
    this.#status = { ...this.#status, state: "ready", reason: null };
    this.arrive();
  }
  #reply(response: ServerResponse, value: unknown, code = 200) {
    response.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    response.end(JSON.stringify(value));
  }
  #refuse(response: ServerResponse, reason: string, code = 403) {
    this.invalid.push(reason);
    this.#reply(
      response,
      { error: { code: "forbidden", message: "Fixture refused request" } },
      code,
    );
  }
  handle = (
    request: IncomingMessage,
    response: ServerResponse,
    send: (status: PreviewStatus) => void,
  ) => {
    this.#send = send;
    const path = request.url?.split("?")[0] ?? "";
    const operation =
      path === "/previews"
        ? "start"
        : path.endsWith("/stop")
          ? "stop"
          : path.endsWith("/lease")
            ? "renew"
            : "details";
    let raw = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => {
      raw += chunk;
    });
    request.on("error", () => {});
    request.on("end", () => {
      let input: unknown;
      try {
        input = raw ? JSON.parse(raw) : null;
      } catch {
        this.#refuse(response, "shape");
        return;
      }
      if (operation === "start") {
        this.#start(request, response, input, send);
        return;
      }
      const id = path.split("/")[2];
      if (
        !this.#status ||
        id !== this.#status.preview_id ||
        path !==
          `/previews/${id}${operation === "stop" ? "/stop" : operation === "renew" ? "/lease" : ""}`
      ) {
        this.#refuse(response, "identity", 404);
        return;
      }
      const call: Call = { kind: operation, previewId: id, authenticated: false, delivered: true };
      if (operation === "details") {
        if (request.method !== "GET" || raw) {
          this.#refuse(response, "details method");
          return;
        }
        this.calls.push(call);
        this.#reply(response, {
          status: this.#status,
          url: this.#status.state === "ready" ? "http://localhost:19800/" : null,
        });
        return;
      }
      if (
        request.method !== "POST" ||
        !input ||
        typeof input !== "object" ||
        Array.isArray(input) ||
        Object.keys(input).join() !== "lease_token" ||
        !("lease_token" in input) ||
        input.lease_token !== this.#token
      ) {
        this.#refuse(response, "authentication");
        return;
      }
      call.authenticated = true;
      this.calls.push(call);
      if (operation === "renew") {
        if (this.#status.state !== "ready" && this.#status.state !== "starting") {
          this.#refuse(response, "terminal renewal", 409);
          return;
        }
      } else if (this.loseNextStop) {
        this.loseNextStop = false;
        call.delivered = false;
        // Drop at the fake authority boundary. No closure receipt; a network reset would
        // let Chromium retry a keepalive request and accidentally deliver the command.
        response.writeHead(204, { "Cache-Control": "no-store" });
        response.end();
        return;
      } else {
        this.#status = { ...this.#status, state: "stopped", reason: "closed" };
        send(this.#status);
      }
      this.#reply(response, this.#status);
    });
  };
  #start(
    request: IncomingMessage,
    response: ServerResponse,
    input: unknown,
    send: (status: PreviewStatus) => void,
  ) {
    if (
      request.method !== "POST" ||
      !input ||
      typeof input !== "object" ||
      Array.isArray(input) ||
      Object.keys(input).sort().join() !== "agent_id,head_commit,revision,run_id" ||
      Object.entries(base).some(([key, value]) => Reflect.get(input, key) !== value)
    ) {
      this.#refuse(response, "candidate");
      return;
    }
    if (this.#status && !["stopped", "failed"].includes(this.#status.state)) {
      this.#refuse(response, "owner-conflicting start", 409);
      return;
    }
    this.#status = {
      ...base,
      change_id: "change-1",
      preview_id: `preview-${(++this.#serial).toString(16).padStart(32, "0")}`,
      state: "starting",
      reason: null,
    };
    this.#token = String.fromCharCode(64 + this.#serial).repeat(43);
    this.calls.push({
      kind: "start",
      previewId: this.#status.preview_id,
      authenticated: false,
      delivered: true,
    });
    this.#reply(response, { preview_id: this.#status.preview_id, lease_token: this.#token }, 202);
    send(this.#status);
    this.#started.resolve();
  }
}
export async function ownershipPresent(page: Page) {
  return page.evaluate(() =>
    Object.keys(sessionStorage).some((key) => key.startsWith("oversight.preview.owner:")),
  );
}
export function previewLifecycleSupport() {
  const authority = new FixtureAuthority();
  const dispatches: Dispatch[] = [];
  const byPage = new Map<Page, Dispatch[]>();
  const pagehides = new Map<Page, number>();
  const actualRequests: { kind: Kind; method: string }[] = [];
  const instrument = async (page: Page) => {
    const pageDispatches: Dispatch[] = [];
    byPage.set(page, pageDispatches);
    await page.clock.install();
    await page.exposeBinding("previewLifecycleDispatch", (_source, value: Dispatch) => {
      dispatches.push(value);
      pageDispatches.push(value);
    });
    await page.exposeBinding("previewLifecyclePagehides", (_source, count: number) => {
      pagehides.set(page, count);
    });
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith("/previews"))
        actualRequests.push({
          kind:
            path === "/previews"
              ? "start"
              : path.endsWith("/stop")
                ? "stop"
                : path.endsWith("/lease")
                  ? "renew"
                  : "details",
          method: request.method(),
        });
    });
    await page.addInitScript(() => {
      const capture = Reflect.get(window, "previewLifecycleDispatch") as (
        value: Dispatch,
      ) => Promise<void>;
      const key = "fixture.lifecycle.unload-dispatch";
      const hidesKey = "fixture.lifecycle.pagehides";
      const reportHides = Reflect.get(window, "previewLifecyclePagehides") as (
        count: number,
      ) => Promise<void>;
      try {
        void reportHides(Number(sessionStorage.getItem(hidesKey) ?? 0)).catch(() => {});
        const prior = sessionStorage.getItem(key);
        if (prior) {
          sessionStorage.removeItem(key);
          void capture(JSON.parse(prior) as Dispatch).catch(() => {});
        }
      } catch {
        /* Fixture instrumentation must not affect the real port. */
      }
      let unload = false;
      addEventListener(
        "pagehide",
        () => {
          unload = true;
          try {
            sessionStorage.setItem(
              hidesKey,
              String(Number(sessionStorage.getItem(hidesKey) ?? 0) + 1),
            );
          } catch {
            /* Fixture proof must not affect disposal. */
          }
        },
        { capture: true },
      );
      // eslint-disable-next-line no-restricted-globals -- Test-only metadata instrumentation of the actual HTTP port.
      const request = window.fetch.bind(window);
      // eslint-disable-next-line no-restricted-globals -- No token/body capture and no replacement product transport.
      window.fetch = (input, init) => {
        const url = input instanceof Request ? input.url : String(input);
        const path = new URL(url, location.href).pathname;
        if (path.startsWith("/previews")) {
          const metadata: Dispatch = {
            kind:
              path === "/previews"
                ? "start"
                : path.endsWith("/stop")
                  ? "stop"
                  : path.endsWith("/lease")
                    ? "renew"
                    : "details",
            keepalive: init?.keepalive === true,
            aborted: init?.signal?.aborted === true,
            unload,
          };
          // Binding delivery can disappear with the document. Persist only metadata,
          // synchronously, so the next document can report the actual fetch options.
          if (metadata.kind === "stop" && unload) {
            try {
              sessionStorage.setItem(key, JSON.stringify(metadata));
            } catch {
              /* Best effort proof. */
            }
          } else void capture(metadata).catch(() => {});
        }
        return request(input, init);
      };
    });
  };
  const chat = previewChatSupport(false, true, {
    previews: authority.handle,
    beforeNavigate: instrument,
  });
  const lead = async (page: Page) => {
    await check(page.locator(".chat-lead-row")).toHaveCount(1);
    await page.locator(".chat-lead-row").click();
    await check(page.getByRole("button", { name: "Preview app", exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
  };
  return {
    reset() {
      authority.reset();
      dispatches.length = actualRequests.length = 0;
      chat.events = chatEvents();
    },
    open: (viewport: { width: number; height: number }, theme: string) =>
      chat.session(viewport, theme),
    lead,
    async observer(owner: LifecyclePage, duplicate = false): Promise<LifecyclePage> {
      const page = await owner.context.newPage();
      const pageErrors: string[] = [];
      page.on("pageerror", (error) => pageErrors.push(error.message));
      await instrument(page);
      if (duplicate) {
        const records = await owner.page.evaluate(() =>
          Object.keys(sessionStorage)
            .filter((key) => key.startsWith("oversight.preview.owner:"))
            .map((key) => [key, sessionStorage.getItem(key) ?? ""] as const),
        );
        await page.addInitScript((copied) => {
          const seeded = "fixture.lifecycle.duplicate-seeded";
          if (sessionStorage.getItem(seeded)) return;
          for (const [key, value] of copied) sessionStorage.setItem(key, value);
          sessionStorage.setItem(seeded, "true");
        }, records);
      }
      await page.goto(owner.page.url());
      await lead(page);
      return { ...owner, page, pageErrors };
    },
    sequence: () => chat.events.length,
    arrive: () => {
      authority.arrive();
    },
    ready: () => authority.ready(),
    previewId: () => authority.status()?.preview_id,
    state: () => authority.status()?.state,
    count: (kind: Kind) => authority.calls.filter((call) => call.kind === kind).length,
    mutationCount: () => authority.calls.filter((call) => call.kind !== "details").length,
    pagehideCount: (page: Page) => pagehides.get(page) ?? 0,
    sourceDispatches: (page: Page) => byPage.get(page) ?? [],
    sourceMutations: (page: Page) =>
      (byPage.get(page) ?? []).filter((call) => call.kind !== "details"),
    stops: () => authority.calls.filter((call) => call.kind === "stop"),
    invalidRequests: () => authority.invalid,
    unloadDispatches: () => dispatches.filter((call) => call.kind === "stop" && call.unload),
    loseUnloadStop: () => {
      authority.loseNextStop = true;
    },
    geometry: (page: Page) =>
      page.evaluate(() => ({
        composer: document
          .querySelector(".conversation-composer")
          ?.getBoundingClientRect()
          .toJSON() as unknown,
        conversation: document
          .querySelector(".conversation-panel")
          ?.getBoundingClientRect()
          .toJSON() as unknown,
        scroll: [...document.querySelectorAll(".conversation-timeline, .conversation-entries")].map(
          (node) => node.scrollTop,
        ),
        window: [scrollX, scrollY],
      })),
    async diagnostics(session: LifecyclePage) {
      await mkdir(lifecycleDirectory, { recursive: true });
      const publicState = await session.page.evaluate(() => ({
        run: window.coldStartTrace.state.run,
        cursor: window.coldStartTrace.state.lastSeq,
        agents: window.coldStartTrace.state.agents,
        leadViews: window.coldStartTrace.state.leadViews,
        storagePresent: Object.keys(sessionStorage).some((key) =>
          key.startsWith("oversight.preview.owner:"),
        ),
      }));
      await writeFile(
        `${lifecycleDirectory}diagnostic-${String(session.viewport.width)}-${session.theme}.json`,
        JSON.stringify(
          {
            ...publicState,
            calls: authority.calls,
            dispatches,
            actualRequests,
            errors: session.pageErrors,
          },
          null,
          2,
        ),
      );
    },
    async capture(session: LifecyclePage, scene: string) {
      const { page, viewport, theme, pageErrors } = session;
      const overflow = await page.evaluate(() => {
        const over = (node: Element | null) =>
          node?.clientWidth ? Math.max(0, node.scrollWidth - node.clientWidth) : 0;
        return {
          document: over(document.documentElement),
          body: over(document.body),
          conversation: over(document.querySelector(".conversation-panel")),
          panel: over(document.querySelector(".preview-panel")),
        };
      });
      const frame = page.frames().find((item) => item.parentFrame());
      const app = frame
        ? await frame.evaluate(() =>
            Math.max(
              0,
              document.documentElement.scrollWidth - document.documentElement.clientWidth,
            ),
          )
        : 0;
      await mkdir(lifecycleDirectory, { recursive: true });
      const name = `${scene}-${String(viewport.width)}-${theme}`;
      await page.screenshot({ path: `${lifecycleDirectory}${name}.png` });
      const manifest = {
        png: `${name}.png`,
        scene,
        viewport,
        theme,
        ...(await captureCheckpoint()),
        provenance:
          "Real App Chat fixture / scripted inputs and shop / shared fake HTTP authority / Linux Chromium",
        previewId: authority.status()?.preview_id,
        state: authority.status()?.state,
        pageErrors: [...pageErrors],
        overflow: { ...overflow, app },
        authority: [...authority.calls],
        dispatches: [...dispatches],
        requests: [...actualRequests],
      };
      await writeFile(
        `${lifecycleDirectory}${name}.json`,
        JSON.stringify(manifest, null, 2) + "\n",
      );
      return manifest;
    },
  };
}
