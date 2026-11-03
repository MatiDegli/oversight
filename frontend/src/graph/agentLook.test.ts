import { mark, marks as rendererMarks } from "./marks.testSupport";
import { it, expect } from "vitest";
import { agentLook, agentSlots, themes } from "../theme";
import { graphStyles } from "./styles";
import { marksLayer } from "./layers";

it.each(Object.entries(themes))(
  "paints every palette family's borders, fill and letters from the %s palette",
  (_, theme) => {
    const styles = graphStyles(theme);
    for (const slot of agentSlots) {
      const { family, text } = agentLook[slot];
      for (const selector of [
        `node.pending[paint = "${slot}"]`,
        `node.focus-read[paint = "${slot}"], node.focus-edit[paint = "${slot}"]`,
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
          { ...rendererMarks(slot), badges: [mark(slot)] },
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
