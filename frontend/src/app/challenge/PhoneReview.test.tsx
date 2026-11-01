import { Suspense } from "react";
import { renderToReadableStream, renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PhoneReview } from "./PhoneReview";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";

it("keeps cold task rows behind the phone frame until their evidence is ready", async () => {
  const practice = validateManifest(manifest, recording);
  const entry = practice.cases[0];
  if (!entry) throw new Error("Missing case");
  const panel = (
    <Suspense fallback={<section aria-label="Phone frame" />}>
      <PhoneReview state={practice.cut} changeId={entry.changeId} title={entry.title} slot="B" />
    </Suspense>
  );
  const pending = renderToStaticMarkup(panel);
  expect(pending).toContain('aria-label="Phone frame"');
  expect(pending).not.toContain('class="pp-decision-rows"');
  const stream = await renderToReadableStream(panel);
  await stream.allReady;
  const html = await new Response(stream).text();
  expect(html).toContain('aria-label="Review context"');
  expect(html).toContain('aria-label="Review lead"');
  expect(html).toContain('aria-label="Agent note"');
  expect(html.indexOf('aria-label="Agent note"')).toBeLessThan(
    html.indexOf('class="pp-decision-rows"'),
  );
});
