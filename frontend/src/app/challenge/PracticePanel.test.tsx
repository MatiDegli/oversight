import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PracticePanel } from "./PracticePanel";
import { freshAttempt } from "./attempt";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";

const practice = validateManifest(manifest, recording);
it.each([
  freshAttempt(),
  { ...freshAttempt(), started: true },
  { ...freshAttempt(), started: true, revealed: true },
])("preserves the content-sized desktop section for attempt %j", (attempt) => {
  const html = renderToStaticMarkup(
    <PracticePanel
      practice={practice}
      attempt={attempt}
      onStart={() => {}}
      onAnswer={() => {}}
      onNext={() => {}}
      onReveal={() => {}}
      onRestart={() => {}}
      onExit={() => {}}
    />,
  );
  expect(html).toContain('class="practice-panel h-full overflow-auto p-6 text-text"');
});
