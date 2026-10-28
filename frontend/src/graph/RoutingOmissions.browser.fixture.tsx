import type { ImpactCone } from "@contracts";
import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { ImpactConeCard } from "../review";
import { RoutingOmissions } from "./RoutingOmissions";

const cone: ImpactCone = {
  seeds: [],
  entries: [],
  edges_followed: "exact",
  max_depth: 3,
  max_nodes: 20,
  tests_reaching: 0,
  omitted_by_depth: 0,
  omitted_by_limit: 0,
};
const root = createRoot(document.body.appendChild(document.createElement("div")));
let showCard: (show: boolean) => void;
let report: (count: number) => void;

function Harness() {
  const hostRef = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(true);
  const [count, setCount] = useState(0);
  useEffect(() => {
    showCard = setShown;
    report = (next) =>
      hostRef.current?.dispatchEvent(new CustomEvent("routingomissions", { detail: next }));
  }, []);
  return (
    <>
      <div ref={hostRef} />
      <RoutingOmissions hostRef={hostRef} inCone={shown} onCount={setCount} />
      {shown && <ImpactConeCard cone={cone} nodes={[]} routingOmissions={count} />}
    </>
  );
}

flushSync(() => {
  root.render(<Harness />);
});
export const omissionHarness = {
  report(count: number) {
    flushSync(() => {
      report(count);
    });
  },
  show(shown: boolean) {
    flushSync(() => {
      showCard(shown);
    });
  },
};
