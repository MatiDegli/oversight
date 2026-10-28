import { stateStore } from "../../state";
import type { State } from "../../state";

export function freezePractice(
  stop: () => void,
  cut: State,
  root: Pick<HTMLElement, "dataset"> | null,
) {
  stop();
  stateStore.setState({ state: cut });
  if (root) root.dataset.seq = String(cut.lastSeq);
}
