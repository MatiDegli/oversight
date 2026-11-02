import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    server: { host: "127.0.0.1", port: 0 },
  });
  html = await server.transformIndexHtml(
    "/",
    `<!doctype html><html><body>
      <div style="display:flex;width:240px;gap:8px">
        <span class="file-agent-mark"></span>
        <span class="usage-chip"></span>
        <span class="usage-chip is-small"></span>
        <span class="review-header-badge"></span>
        <div class="where-they-work"><h3><span class="work-badge"></span></h3></div>
      </div>
      <div style="position:relative;width:300px;height:80px"><canvas></canvas></div>
      <script type="module">
        import '/src/theme/index.css';
        import '/src/app/shell/shell.css';
        import '/src/app/usage/usage.css';
        import '/src/app/shell/whereTheyWork.css';
        import '/src/review/reviewHeader.css';
        import { MarkerLayer } from '/src/graph/field/markerLayer.ts';
        const layer = new MarkerLayer(document.querySelector('canvas'));
        window.drawBadge = label => {
          document.querySelectorAll('span').forEach(node => node.textContent = label);
          layer.draw([{id:'node', marker:'agent', label, source:'agent-a', weight:1, pulses:false}],
            new Map([['agent\\u0000node', {x1:100,y1:10,x2:200,y2:50}]]), [], 300, 80);
        };
      </script></body></html>`,
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each(["dark", "day"])(
  "fits full team badges with inline breathing room in %s",
  async (theme) => {
    const page = await browser.newPage();
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "drawBadge" in window);
      await page.evaluate(async () => {
        await document.fonts.load('700 15px "Atkinson Hyperlegible Next"');
        await document.fonts.ready;
      });
      for (const label of ["A", "A·12", "AA"]) {
        await page.evaluate(
          ({ label, theme }) => {
            document.documentElement.dataset.theme = theme;
            (window as unknown as { drawBadge(label: string): void }).drawBadge(label);
          },
          { label, theme },
        );
        for (const badge of await page.locator("span").all()) {
          await check(badge).toHaveText(label);
          const fit = await badge.evaluate((node) => {
            const range = document.createRange();
            range.selectNodeContents(node);
            const text = range.getBoundingClientRect(),
              box = node.getBoundingClientRect();
            return {
              scroll: node.scrollWidth,
              client: node.clientWidth,
              gap: Math.min(text.left - box.left, box.right - text.right),
            };
          });
          expect
            .soft(fit.scroll, `${label}: scrollWidth ≤ clientWidth`)
            .toBeLessThanOrEqual(fit.client);
          expect.soft(fit.gap, `${label}: inline padding`).toBeGreaterThanOrEqual(3);
        }
        const fit = await page.locator("[data-field-markers] g").evaluate((group) => {
          const shape = group.children[0] as SVGGraphicsElement;
          const text = group.children[1] as SVGGraphicsElement;
          const outer = shape.getBBox(),
            inner = text.getBBox();
          return Math.min(inner.x - outer.x, outer.x + outer.width - inner.x - inner.width);
        });
        expect.soft(fit, `${label}: field marker padding`).toBeGreaterThanOrEqual(5);
      }
    } finally {
      await page.close();
    }
  },
);
