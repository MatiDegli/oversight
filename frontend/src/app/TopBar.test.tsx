import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { through } from "./replay.fixture";
import { TopBar } from "./TopBar";

const render = (seq: number, onOpenQueue?: () => void) =>
  renderToStaticMarkup(<TopBar state={through(seq)} onOpenQueue={onOpenQueue} />);

describe("the top bar", () => {
  it("shows the name, the repository, the branch, the short commit and the spend", () => {
    const markup = render(14);
    for (const text of ["Oversight", ">demo-repo<", " main", ">8cbf753<", "Spent"])
      expect(markup).toContain(text);
    expect(markup).toContain("lucide-folder");
    expect(markup).toContain("lucide-git-branch");
  });

  it("puts the review queue counter on the control radius with its count in the label's type", () => {
    const markup = render(20, vi.fn());
    const button = /<button type="button" title="Open the next change[^>]*>/.exec(markup)?.[0];
    expect(button).toContain("rounded-control");
    expect(button).not.toContain("rounded-full");
    expect(button).not.toContain("disabled");
    expect(markup).toContain("1 change ready for review");
    expect(render(14)).not.toContain("ready for review");
  });

  it("shows search, history and settings as unavailable, never as live controls", () => {
    const markup = render(14);
    // Each one is a disabled control that says why, and none promises a shortcut that does nothing.
    for (const [label = "", title = ""] of [
      ["Find a function or file", "Search: not built yet"],
      ["Earlier today", "Earlier today: not built yet"],
      ["Settings", "Settings: not built yet"],
    ]) {
      const tag = new RegExp(`<[a-z]+ [^>]*aria-label="${label}"[^>]*>`).exec(markup)?.[0];
      expect(tag, label).toContain("disabled");
      expect(tag, label).toContain(`title="${title}"`);
    }
    expect(markup).not.toContain("Ctrl");
    expect(markup).not.toContain("hover:bg");
  });
});
