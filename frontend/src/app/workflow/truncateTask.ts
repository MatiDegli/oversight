/** Keep short tasks verbatim; reserve one character for the ellipsis on longer tasks. */
export function truncateTask(task: string): string {
  const characters = Array.from(task);
  if (characters.length <= 48) return task;
  const prefix = characters.slice(0, 47).join("");
  if (/\s/u.test(characters[47] ?? "")) return `${prefix.trimEnd()}…`;
  const boundary = prefix.search(/\s+\S*$/u);
  return `${boundary > 0 ? prefix.slice(0, boundary) : prefix}…`;
}
