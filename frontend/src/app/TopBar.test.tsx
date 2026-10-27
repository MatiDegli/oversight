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

  it("leaves out search, history and settings until each is built, with no hint of a shortcut", () => {
    const markup = render(14);
    for (const unbuilt of ["Find a function or file", "Earlier today", "Settings", "search"])
      expect(markup).not.toContain(unbuilt);
    expect(markup).not.toContain("disabled");
    expect(markup).not.toContain("Ctrl");
    expect(markup).not.toContain("lucide-search");
    expect(markup).not.toContain("lucide-history");
    expect(markup).not.toContain("lucide-settings");
  });

  it("keeps the queue counter and the spend at the right edge without them", () => {
    const markup = render(20, vi.fn());
    expect(markup.indexOf('aria-label="Review queue"')).toBeLessThan(markup.indexOf("Spent"));
    expect(markup).toMatch(/aria-label="Review queue" class="ml-auto /);
  });
});
