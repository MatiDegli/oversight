import { describe, expect, it } from "vitest";
import {
  CONFIGURATION_ATTRIBUTION_PREFIX as prefix,
  partitionConfigurationAttribution,
} from "./configurationAttribution";

describe("configuration attribution partition", () => {
  it.each([
    { input: [`${prefix}unchanged`], attributions: [`${prefix}unchanged`], problems: [] },
    {
      input: ["first", `${prefix}one`, "second", `${prefix}two`, `${prefix}one`, "first"],
      attributions: [`${prefix}one`, `${prefix}two`, `${prefix}one`],
      problems: ["first", "second", "first"],
    },
    { input: [prefix], attributions: [prefix], problems: [] },
    { input: [], attributions: [], problems: [] },
    { input: undefined, attributions: [], problems: [] },
    {
      input: [
        " configuration attribution: one",
        "Configuration attribution: two",
        "configuration attribution:",
        "ordinary",
      ],
      attributions: [],
      problems: [
        " configuration attribution: one",
        "Configuration attribution: two",
        "configuration attribution:",
        "ordinary",
      ],
    },
  ])("partitions $input losslessly", ({ input, attributions, problems }) => {
    const original = input && [...input];
    if (input) Object.freeze(input);
    expect(partitionConfigurationAttribution(input)).toEqual({ attributions, problems });
    expect(input).toEqual(original);
  });
});
