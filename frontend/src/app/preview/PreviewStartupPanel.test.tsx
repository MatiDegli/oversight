import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PreviewStartupPanel } from "./PreviewStartupPanel";
import type { PreviewStartupReport, PreviewStartupResult } from "./previewStartup";

const report: PreviewStartupReport = {
  version: 1,
  argv: [
    ["pnpm", "run", "build"],
    ["node", "a file.js", "--flag=two words"],
  ],
  cwd: "/工作/é🙂/" + "long".repeat(30),
  reason: "Selected scripts\n  with whitespace preserved ",
  dependencies: ["Prepared node_modules", "Prepared offline store"],
  limits: "  256 MiB; 300 s\nprocesses: 8  ",
  platform_residual: "Windows process isolation remains external",
  build_log: "/runtime/" + "long/".repeat(30) + "build.log",
  enable_hint: "Set OVERSIGHT_PREVIEW_ENABLED=1",
  agents_label: "Agent A · candidate",
};
const render = (result: PreviewStartupResult) =>
  renderToStaticMarkup(<PreviewStartupPanel result={result} headingId="fixture-startup" />);

it("labels its region and lists every value verbatim with all ten explicit labels", () => {
  const html = render({ kind: "valid", report });
  expect(html).toContain('<section class="preview-startup" aria-labelledby="fixture-startup">');
  expect(html).toContain('<h2 id="fixture-startup">Preview startup</h2>');
  const values: [string, string][] = [
    ["Version", "1"],
    ["Working directory", `<code>${report.cwd}</code>`],
    ["Selection reason", report.reason],
    ["Limits", report.limits],
    ["Platform residual", report.platform_residual],
    ["Build log", `<code>${report.build_log}</code>`],
    ["Enable hint", report.enable_hint],
    ["Agents", report.agents_label],
  ];
  for (const [label, value] of values) expect(html).toContain(`<dt>${label}</dt><dd>${value}</dd>`);
  expect(html.match(/<dt>/gu)).toHaveLength(10);
  expect(html).toContain("<dt>Commands (argv)</dt>");
  expect(html).toContain("<dt>Prepared dependencies</dt>");
  for (const dependency of report.dependencies) expect(html).toContain(`<li>${dependency}</li>`);
});

it("preserves execution order and argument boundaries, including arguments containing spaces", () => {
  const html = render({ kind: "valid", report });
  const commands = [
    ...html.matchAll(/<li><div class="preview-startup-arguments">(.*?)<\/div><\/li>/gu),
  ];
  expect(commands).toHaveLength(2);
  commands.forEach((command, index) => {
    expect(
      [...(command[1] ?? "").matchAll(/<code class="preview-startup-token">(.*?)<\/code>/gu)].map(
        (token) => token[1],
      ),
    ).toEqual(report.argv[index]);
  });
  expect(html).toContain('<ol class="preview-startup-commands">');
  expect(html).not.toContain("pnpm run build");
});

it("renders hostile strings only as text without links, handlers or injected elements", () => {
  const hostile = '</script><!-- <img src=x onerror="alert(1)"> & \\ \u2028\u2029';
  const html = render({
    kind: "valid",
    report: {
      version: 1,
      argv: [[hostile]],
      cwd: hostile,
      reason: hostile,
      dependencies: [hostile],
      limits: hostile,
      platform_residual: hostile,
      build_log: hostile,
      enable_hint: hostile,
      agents_label: hostile,
    },
  });
  expect(
    html.match(/&lt;\/script&gt;&lt;!-- &lt;img src=x onerror=&quot;alert\(1\)&quot;&gt; &amp;/gu),
  ).toHaveLength(9);
  expect(html).not.toMatch(/<script|<img|<a\b| onerror="|onclick=|autofocus|<button/iu);
});

it("keeps an empty residual and dependencies empty", () => {
  const html = render({
    kind: "valid",
    report: { ...report, platform_residual: "", dependencies: [] },
  });
  expect(html).toContain("<dt>Platform residual</dt><dd></dd>");
  expect(html).toContain('<ul class="preview-startup-dependencies"></ul>');
});

it.each([
  ["missing", "Not enabled on this server."],
  ["invalid", "The preview startup report is invalid, so reviewed-app previews stay off."],
] as const)("shows the single %s notice without controls", (kind, message) => {
  const html = render({ kind });
  expect(html).toContain(`<p class="preview-startup-notice">${message}</p>`);
  expect(html).not.toMatch(/<dl|<button|<details|autofocus|role="alert"/u);
});
