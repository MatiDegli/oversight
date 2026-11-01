import type { ReactNode } from "react";

export function SettingsFrame({ children }: { children?: ReactNode }) {
  return (
    <section
      aria-labelledby="merge-mode-heading"
      className="@container min-h-0 w-full min-w-0 overflow-y-auto p-8"
    >
      <h2 id="merge-mode-heading" className="text-(length:--title-size) font-bold">
        Merge mode
      </h2>
      <p className="mt-1 text-sm text-muted">Who lets a finished change into main.</p>
      {children}
    </section>
  );
}
