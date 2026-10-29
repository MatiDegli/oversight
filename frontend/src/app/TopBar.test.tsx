import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { through } from "./replay.fixture";
import { TopBar } from "./TopBar";

const render = (seq: number, onOpenQueue?: () => void) =>
  renderToStaticMarkup(
    <TopBar state={through(seq)} onOpenQueue={onOpenQueue} onOpenPalette={vi.fn()} />,
  );

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
    const button = /<button type="button" title="Open the review queue[^>]*>/.exec(markup)?.[0];
    expect(button).toContain("rounded-control");
    expect(button).not.toContain("rounded-full");
    expect(button).not.toContain("disabled");
    expect(markup).toContain("Decisions ·");
    expect(markup).toContain('class="font-mono tabular-nums">1</span>');
    expect(markup).toContain('aria-label="Decisions: 1 pending. Open review queue"');
    expect(render(14)).toContain("Nothing needs you");
    expect(render(14)).toContain("text-muted");
    expect(render(14)).toContain('aria-label="Decisions: 0 pending. Open review queue"');
    expect(render(14)).not.toContain("status-lamp-size");
  });

  it("shows search and its Ctrl K hint while leaving out history and settings", () => {
    const markup = render(14);
    for (const unbuilt of ["Earlier today", "Settings"]) expect(markup).not.toContain(unbuilt);
    expect(markup).not.toContain("disabled");
    expect(markup).toContain("Find a function, file or command");
    expect(markup).toContain("Ctrl K</kbd>");
    expect(markup).toContain("lucide-search");
    expect(markup).toContain('aria-haspopup="dialog"');
    expect(markup).not.toContain("lucide-history");
    expect(markup).not.toContain("lucide-settings");
  });

  it("keeps the queue counter and the spend at the right edge beside search", () => {
    const markup = render(20, vi.fn());
    expect(markup.indexOf('aria-label="Review queue"')).toBeLessThan(markup.indexOf("Spent"));
    expect(markup).toMatch(/aria-label="Review queue" class="ml-auto /);
  });
});
