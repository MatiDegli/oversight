import { useEffect, useState } from "react";
import type { TreeResponse } from "@contracts";
import { CommandError } from "../../transport";
import { useFilesNavigation } from "../FilesContext";
import { recordedTree } from "../filesTree";

export type FilesNavigation = NonNullable<ReturnType<typeof useFilesNavigation>>;

export function useTreeListing({ state, loadTree }: FilesNavigation) {
  const [listing, setListing] = useState<TreeResponse | null>(null);
  const [failure, setFailure] = useState(false);
  const [fallback, setFallback] = useState(false);
  const [retry, setRetry] = useState(0);
  const merges = Object.values(state.changes)
    .filter((change) => change.resolution?.merge === "merged")
    .map((change) => change.change_id)
    .sort()
    .join(",");
  const version = state.graph?.version;
  const run = state.run?.run_id;
  useEffect(() => {
    if (!loadTree || !run) return;
    const controller = new AbortController();
    void loadTree(controller.signal)
      .then((tree) => {
        if (controller.signal.aborted) return;
        setListing(tree);
        setFailure(false);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof CommandError && error.status === 501) setFallback(true);
        else setFailure(true);
      });
    return () => {
      controller.abort();
    };
  }, [loadTree, run, version, merges, retry]);
  const recorded = !loadTree || fallback;
  return {
    recorded,
    failure,
    tree: recorded ? recordedTree(state) : listing?.run_id === run ? listing : null,
    retry: () => {
      setRetry((value) => value + 1);
    },
  };
}
