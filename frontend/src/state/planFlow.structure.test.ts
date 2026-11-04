import { readFileSync } from "node:fs";
import ts from "typescript";
import { expect, it } from "vitest";

it.each([
  ["./planFlow.ts", "projectRow"],
  ["../app/planFlow.browser.fixture.tsx", "flowEvents"],
])("keeps %s's %s within the 80-line function budget", (path, name) => {
  const source = ts.createSourceFile(
    path,
    readFileSync(new URL(path, import.meta.url), "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );
  const declaration = source.statements.find(
    (node) => ts.isFunctionDeclaration(node) && node.name?.text === name,
  );
  expect(declaration, `Missing function ${name}`).toBeDefined();
  if (!declaration) throw new Error(`Missing function ${name}`);
  const start = source.getLineAndCharacterOfPosition(declaration.getStart(source)).line;
  const end = source.getLineAndCharacterOfPosition(declaration.getEnd()).line;
  expect(end - start + 1).toBeLessThanOrEqual(80);
});
