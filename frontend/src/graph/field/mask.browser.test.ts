import { launchBrowser } from "../../browserLaunch.support";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { expect, it } from "vitest";

it("masks LEDs, resting guides and travelling markers from rendered DOM rectangles", async () => {
  const server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    server: { host: "127.0.0.1", port: 0 },
  });
  const browser = await launchBrowser({ webGL: true });
  try {
    const html = await server.transformIndexHtml(
      "/",
      `<!doctype html><html><body style="margin:0">
      <main aria-label="Graph stage" style="position:relative;width:400px;height:300px">
        <div style="position:absolute;inset:0"><canvas style="width:400px;height:300px"></canvas></div>
        <div id="mask" class="pointer-events-auto" style="position:absolute;left:0;top:0;width:56px;height:56px"></div>
      </main><script type="module">
      import '/src/theme/index.css';
      import { createFieldController } from '/src/graph/field/controller.ts';
      const canvas = document.querySelector('canvas');
      const boxes = new Map([['a',{x1:0,y1:0,x2:40,y2:20}], ['b',{x1:200,y1:100,x2:240,y2:120}]]);
      const source = { boxes: ids => new Map(ids.map(id => [id,boxes.get(id)])), zoom:()=>1,
        subscribe:()=>()=>{}, route:()=>[{x:0,y:10},{x:0,y:110},{x:200,y:110}] };
      const controller = createFieldController(canvas, source);
      window.r4 = { canvas, controller };
      </script></body></html>`,
    );
    await server.listen();
    const page = await browser.newPage();
    await page.route(/\/$/, (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(server.resolvedUrls?.local[0] ?? "");
    await page.waitForFunction(() => "r4" in window);
    const resting = await page.evaluate(() => {
      const host = (
        window as unknown as {
          r4: {
            canvas: HTMLCanvasElement & {
              oversightField: { pixel(x: number, y: number): number[] };
            };
          };
        }
      ).r4;
      return [host.canvas.oversightField.pixel(3, 3), host.canvas.oversightField.pixel(59, 3)];
    });
    expect(resting[0]?.[3]).toBe(0);
    expect(resting[1]?.[3]).toBe(255);
    await page.evaluate(async () => {
      const host = (window as unknown as { r4: { controller: { update(input: unknown): void } } })
        .r4;
      host.controller.update({
        mode: "watch",
        lights: [
          { id: "a", source: "agent-a", marker: "agent-1", steady: true, weight: 1, pulses: true },
        ],
      });
      await new Promise((resolve) => setTimeout(resolve, 260));
    });
    const marker = page.locator("[data-field-markers] g");
    await expect.poll(() => marker.getAttribute("opacity")).toBe("1");
    const maskPath = await page.locator("[data-field-markers] clipPath path").getAttribute("d");
    expect(maskPath).toContain("M0,56H56V300H0Z");
    // Moving an existing overlay must refresh even a resting field; no graph event is needed.
    await page.evaluate(() => {
      document.getElementById("mask")?.style.setProperty("left", "100px");
    });
    await expect
      .poll(async () => page.locator("[data-field-markers] clipPath path").getAttribute("d"))
      .toContain("M100,56H156V300H100Z");
    await page.evaluate(() => {
      const host = (window as unknown as { r4: { controller: { update(input: unknown): void } } })
        .r4;
      host.controller.update({ mode: "watch", lights: [] });
    });
    await expect.poll(() => marker.count(), { timeout: 5_000 }).toBe(0);
    await page.close();
  } finally {
    await browser.close();
    await server.close();
  }
}, 30_000);
