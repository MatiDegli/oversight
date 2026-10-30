export function sentenceList(items: string[]) {
  return items.length < 2
    ? items.join("")
    : `${items.slice(0, -1).join(", ")} and ${items.at(-1) ?? ""}`;
}
export const impactText = (count: number) =>
  count ? `${String(count)} possibly affected entries` : "No possibly affected entries";
export function omittedText(impact: { omitted_by_depth: number; omitted_by_limit: number }) {
  return [
    ...(impact.omitted_by_depth
      ? [`${String(impact.omitted_by_depth)} more omitted by depth.`]
      : []),
    ...(impact.omitted_by_limit
      ? [`${String(impact.omitted_by_limit)} more omitted by limit.`]
      : []),
  ].join(" ");
}
