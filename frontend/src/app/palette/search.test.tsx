import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { through } from "../replay.fixture";
import { recordedTree } from "../filesTree";
import { TopBar } from "../TopBar";
import { ShortcutsSheet } from "../shell/Navigation";
import { paletteCommands } from "./commands";
import { groups, paletteItems, searchItems } from "./search";

const commands = () => paletteCommands({ toggleSidebar: vi.fn(), toggleDock: vi.fn() }, false);
const catalog = (seq = 20) => {
  const state = through(seq);
  return paletteItems(
    state,
    recordedTree(state).files.map((file) => file.path),
    vi.fn(),
    vi.fn(),
    commands(),
  );
};

describe("palette catalog", () => {
  it("searches three groups, all graph symbols and the recorded Files listing", () => {
    const state = through(20);
    const items = catalog();
    expect([...new Set(items.map((item) => item.group))]).toEqual([...groups]);
    expect(items.filter((item) => item.group === groups[0]).map((item) => item.id)).toEqual(
      state.graph?.nodes.filter((node) => node.kind !== "module").map((node) => `node:${node.id}`),
    );
    expect(
      new Set(items.filter((item) => item.group === groups[0]).map((item) => item.kind)),
    ).toEqual(new Set(["function", "method", "class"]));
    expect(items.filter((item) => item.group === groups[1]).map((item) => item.detail)).toEqual(
      recordedTree(state).files.map((file) => file.path),
    );
  });

  it("allows exactly six navigation commands, never a decision or destructive action", () => {
    const list = commands();
    expect(list.map((entry) => entry.label)).toEqual([
      "Toggle sidebar",
      "Toggle bottom dock",
      "Close focus tab",
      "Maximize focus panel",
      "Rescan graph",
      "Focus task box",
    ]);
    expect(list.map((entry) => entry.label).join(" ")).not.toMatch(
      /approve|request changes|reject|stop|discard/i,
    );
    expect(list.find((entry) => entry.id === "rescan")?.unavailable).toBe("Unavailable in replay");
    expect(list.filter((entry) => entry.unavailable).map((entry) => entry.id)).toEqual([
      "close",
      "maximize",
      "rescan",
      "task",
    ]);
  });

  it("ranks fuzzy names before paths, ignores case and supports empty and absent matches", () => {
    const items = catalog();
    const base = items[0];
    if (!base) throw new Error("Missing symbol");
    const candidates = [
      { ...base, id: "path", name: "another", detail: "shop/compute_price.py" },
      { ...base, id: "fuzzy", name: "compute_price", detail: "shop/pricing.py" },
      { ...base, id: "prefix", name: "cmpr_extra", detail: "shop/misc.py" },
    ];
    expect(searchItems(candidates, " CMPR ").map((item) => item.id)).toEqual([
      "prefix",
      "fuzzy",
      "path",
    ]);
    expect(searchItems(items, "")).toHaveLength(items.length);
    expect(searchItems(items, "no-such-symbol")).toEqual([]);
    expect(searchItems(items, "CART.ADD")[0]?.detail).toContain("Cart.add");
    expect(searchItems(items, "shop/pricing.py")[0]?.kind).toBe("file");
    expect(searchItems(items, "Rescan graph")[0]?.kind).toBe("command");
  });

  it("refreshes symbols from state and invokes the supplied node/file navigation", () => {
    const state = through(20);
    const node = vi.fn();
    const file = vi.fn();
    const items = paletteItems(state, ["docs/guide.md"], node, file, []);
    const symbol = items.find((item) => item.kind === "method");
    symbol?.run();
    expect(node).toHaveBeenCalledWith(symbol?.id.slice(5));
    items.find((item) => item.kind === "file")?.run();
    expect(file).toHaveBeenCalledExactlyOnceWith("docs/guide.md");
    expect(paletteItems({ ...state, graph: null }, [], node, file, [])).toEqual([]);
  });

  it("connects every command to its shell action, including restore", () => {
    const actions = {
      toggleSidebar: vi.fn(),
      toggleDock: vi.fn(),
      closeTab: vi.fn(),
      toggleMaximize: vi.fn(),
      rescan: vi.fn(),
      focusTask: vi.fn(),
    };
    const list = paletteCommands(actions, true);
    expect(list.find((entry) => entry.id === "maximize")?.label).toBe("Restore graph");
    for (const command of list) {
      expect(command.unavailable).toBeUndefined();
      command.run();
    }
    for (const action of Object.values(actions)) expect(action).toHaveBeenCalledOnce();
  });
});

it("mounts an operable search trigger and documents Ctrl K in the shortcuts sheet", () => {
  const markup = renderToStaticMarkup(
    <TopBar state={through(20)} onOpenQueue={vi.fn()} onOpenPalette={vi.fn()} />,
  );
  expect(markup).toContain("Find a function, file or command");
  expect(markup).toContain('aria-keyshortcuts="Control+k"');
  expect(markup).toContain('aria-haspopup="dialog"');
  expect(markup).toContain("Ctrl K</kbd>");
  expect(markup).not.toContain("disabled");
  const shortcuts = renderToStaticMarkup(<ShortcutsSheet onClose={vi.fn()} />);
  expect(shortcuts).toContain("Find a function, file or command");
  expect(shortcuts).toContain("Ctrl K</kbd>");
});
