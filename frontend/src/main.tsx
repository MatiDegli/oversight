import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app";
import { applyAppearance } from "./appearance";
import "./theme/index.css";

// Before React mounts, so the first paint, the graph and the editors read the chosen theme.
applyAppearance();

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
