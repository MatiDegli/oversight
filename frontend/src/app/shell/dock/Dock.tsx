import { useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { useStore } from "zustand";
import { stateStore } from "../../../state";
import { Activity } from "./Activity";
import { Tests } from "./Tests";
import "./dock.css";

const tabs = ["Terminal", "Tests", "Activity log"] as const;

export function Dock({ onClose }: { onClose: () => void }) {
  const [active, setActive] = useState<(typeof tabs)[number]>("Tests");
  const state = useStore(stateStore, (store) => store.state);
  const id = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <section aria-label="Bottom dock" className="bottom-dock">
      <div className="dock-header">
        <div
          role="tablist"
          aria-label="Dock"
          onKeyDown={(event) => {
            const index = tabs.indexOf(active);
            const target = {
              ArrowRight: (index + 1) % tabs.length,
              ArrowLeft: (index + tabs.length - 1) % tabs.length,
              Home: 0,
              End: tabs.length - 1,
            }[event.key];
            if (target === undefined || event.ctrlKey || event.metaKey || event.altKey) return;
            event.preventDefault();
            const next = tabs[target];
            if (!next) return;
            setActive(next);
            buttons.current[target]?.focus();
          }}
        >
          {tabs.map((label, index) => (
            <button
              key={label}
              ref={(node) => {
                buttons.current[index] = node;
              }}
              type="button"
              role="tab"
              id={`${id}-tab-${String(index)}`}
              aria-selected={active === label}
              aria-controls={`${id}-panel-${String(index)}`}
              tabIndex={active === label ? 0 : -1}
              onClick={() => {
                setActive(label);
              }}
              className="dock-tab"
            >
              {label}
            </button>
          ))}
        </div>
        <button type="button" aria-label="Close dock" onClick={onClose} className="dock-close">
          <X aria-hidden="true" />
        </button>
      </div>
      {tabs.map((label, index) => (
        <div
          key={label}
          id={`${id}-panel-${String(index)}`}
          role="tabpanel"
          aria-labelledby={`${id}-tab-${String(index)}`}
          hidden={active !== label}
          tabIndex={0}
          className="dock-panel"
        >
          {active === label &&
            (label === "Tests" ? (
              <Tests state={state} />
            ) : label === "Activity log" ? (
              <Activity state={state} />
            ) : (
              <p className="dock-empty">main · Terminal comes later. No shell is available.</p>
            ))}
        </div>
      ))}
    </section>
  );
}
