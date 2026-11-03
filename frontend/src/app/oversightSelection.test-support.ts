import { Children, isValidElement, type ReactNode } from "react";
import { vi } from "vitest";

/** SSR has no DOM: activate the actual selector's click handler during the hook's render.
 * React applies the resulting render-phase updates before emitting the static markup.
 * Browser and e2e harnesses click the DOM button instead; no context default is replaced.
 */
vi.mock("./shell/useShell", async (original) => {
  const module = await original<typeof import("./shell/useShell")>();
  const { TopBar } = await import("./TopBar");
  const { createInitialState } = await import("../state");
  function find(node: ReactNode, matches: (props: Record<string, unknown>) => boolean): unknown {
    for (const child of Children.toArray(node)) {
      if (!isValidElement<Record<string, unknown>>(child)) continue;
      if (matches(child.props) && typeof child.props.onClick === "function")
        return child.props.onClick;
      const nested = find(child.props.children as ReactNode, matches);
      if (nested) return nested;
    }
    return undefined;
  }
  return {
    ...module,
    useShell(...args: Parameters<typeof module.useShell>) {
      const shell = module.useShell(...args);
      if (shell.layout === "Chat") {
        const bar = TopBar({
          state: createInitialState(),
          layout: shell.layout,
          onLayout: shell.setLayout,
        });
        const click = find(bar, (props) =>
          Children.toArray(props.children as ReactNode).includes("Oversight"),
        );
        if (typeof click !== "function") throw new Error("Missing Oversight view selector");
        Reflect.apply(click, undefined, []);
      }
      return shell;
    },
  };
});
