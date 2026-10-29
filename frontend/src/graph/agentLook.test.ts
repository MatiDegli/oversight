import { it, expect } from "vitest";
import { agentLook, agentSlots, themes } from "../theme";
import { graphStyles } from "./styles";
import { marksLayer } from "./layers";

it.each(Object.entries(themes))(
  "paints every slot's borders, fill and letters from the %s palette",
  (_, theme) => {
    const styles = graphStyles(theme);
    for (const slot of agentSlots) {
      const { family, text } = agentLook[slot];
      for (const selector of [
        `node.pending-${slot}`,
        `node.focus-${slot}-read`,
        `node.focus-${slot}-edit`,
      ]) {
        const entry = styles.find((entry) => entry.selector === selector);
        expect(
          entry && "style" in entry && "border-color" in entry.style
            ? entry.style["border-color"]
            : undefined,
        ).toBe(theme[family]);
      }
      const svg = decodeURIComponent(
        marksLayer(
          theme,
          { slot, word: "changed", badges: [{ slot, filled: true }] },
          { width: 282, height: 46 },
        ).image,
      );
      expect(svg).toContain(`fill="${theme[family]}"`);
      expect(svg).toContain(`fill="${theme[text]}">changed</text>`);
      expect(svg).toContain(`>${slot}</text>`);
      expect(svg).toContain(`fill="${theme["on-agent"]}"`);
    }
  },
);
