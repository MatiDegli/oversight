import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

// Exercise the gate's actual request filter with negative inputs: a successful browser run
// alone cannot prove that the assertion would catch a future request outside the base.
void test("the subpath e2e gate rejects every request outside the deployment base", () => {
  const source = ts.createSourceFile(
    "editor-subpath.spec.ts",
    readFileSync(new URL("../e2e/editor-subpath.spec.ts", import.meta.url), "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );
  const filters: string[] = [];
  function visit(node: ts.Node): void {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "toEqual" &&
      ts.isCallExpression(node.expression.expression) &&
      node.expression.expression.expression.getText(source) === "expect"
    ) {
      const argument = node.expression.expression.arguments[0]?.getText(source);
      if (argument?.includes("requested.filter")) filters.push(argument);
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.equal(filters.length, 1, "The complete request list must have one base assertion");
  const filter = filters[0];
  assert.ok(filter);
  const helpers = source.statements
    .filter(ts.isFunctionDeclaration)
    .map((node) => ts.transpile(node.getText(source)));
  const origin = "http://localhost:5710";
  const allowed = `${origin}/oversight/assets/app.js`;
  for (const forbidden of [
    `${origin}/outside.js`,
    "https://other.test/oversight/app.js",
    `blob:${origin}/owned-graph-image`,
    "blob:https://other.test/foreign-image",
  ]) {
    const rejected = runInNewContext(`${helpers.join("\n")}\n${filter}`, {
      URL,
      origin,
      requested: [allowed, forbidden],
    }) as string[];
    assert.deepEqual(Array.from(rejected), [forbidden]);
  }
});
