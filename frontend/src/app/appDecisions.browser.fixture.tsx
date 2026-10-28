// Browser-only startup module: let Vite refresh dependency URLs in its module graph.
import { createRoot } from "react-dom/client";
import type { ServerEvent } from "@contracts";

import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { stateStore } from "../state";
import { App } from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("Missing app fixture root");
stateStore.getState().applyEvents(
  log
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent)
    .filter((event) => event.seq <= 20),
);
Object.assign(window, { appStore: stateStore });
createRoot(root).render(<App />);
