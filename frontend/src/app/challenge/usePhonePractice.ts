import { useSyncExternalStore } from "react";
const query = "(width < 1280px)";
const subscribe = (notify: () => void) => {
  const media = window.matchMedia(query);
  media.addEventListener("change", notify);
  return () => {
    media.removeEventListener("change", notify);
  };
};
export function usePhonePractice(active: boolean) {
  const narrow = useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
  return active && narrow;
}
