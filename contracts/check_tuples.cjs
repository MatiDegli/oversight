/* Discover changed array constraints from schemas; inspect generated types with TypeScript. */
const fs = require("node:fs");
const path = require("node:path");

function main() {
  const args = process.argv.slice(2);
  const options = {};
  const names = ["--base", "--schema", "--types", "--node-modules"];
  for (let i = 0; i < args.length; i += 2) {
    if (!names.includes(args[i]) || !args[i + 1] || options[args[i]])
      throw new Error("required arguments: --base --schema --types --node-modules");
    options[args[i]] = args[i + 1];
  }
  if (Object.keys(options).length !== 4) throw new Error("all four arguments are required");
  const base = JSON.parse(fs.readFileSync(options["--base"], "utf8"));
  const schema = JSON.parse(fs.readFileSync(options["--schema"], "utf8"));
  const ts = require(path.resolve(options["--node-modules"], "typescript"));
  const types = path.resolve(options["--types"]);
  const program = ts.createProgram([types], { strict: true, noEmit: true, skipLibCheck: true });
  const errors = ts.getPreEmitDiagnostics(program);
  if (errors.length) throw new Error(ts.formatDiagnosticsWithColorAndContext(errors, {
    getCurrentDirectory: () => process.cwd(), getCanonicalFileName: x => x,
    getNewLine: () => "\n",
  }));
  const checker = program.getTypeChecker();
  const source = program.getSourceFile(types);
  const exports = checker.getExportsOfModule(checker.getSymbolAtLocation(source));
  const symbols = new Map(exports.map(symbol => [symbol.name, symbol]));
  const constraints = new Set(["minItems", "maxItems", "prefixItems"]);
  function restrictions(node, document, seen = new Set()) {
    if (!node || typeof node !== "object") return [];
    if (Array.isArray(node)) return node.flatMap(item => restrictions(item, document, seen));
    let result = [];
    if (node.$ref) {
      if (!node.$ref.startsWith("#/")) throw new Error("only local schema references are supported");
      if (!seen.has(node.$ref)) {
        const next = new Set(seen).add(node.$ref);
        const target = node.$ref.slice(2).split("/").reduce((at, key) => at[key.replace(/~1/g,"/").replace(/~0/g,"~")], document);
        result = restrictions(target, document, next);
      }
    }
    for (const [key, value] of Object.entries(node)) {
      if (constraints.has(key)) result.push([key, value]);
      else if (key !== "$ref" && typeof value === "object")
        result.push(...restrictions(value, document, seen));
    }
    return result;
  }
  function containsTuple(type, seen = new Set()) {
    if (seen.has(type)) return false;
    seen.add(type);
    if (checker.isTupleType(type)) return true;
    if (type.isUnionOrIntersection()) return type.types.some(branch => containsTuple(branch, seen));
    if (type.objectFlags & ts.ObjectFlags.Reference)
      return checker.getTypeArguments(type).some(argument => containsTuple(argument, seen));
    return false;
  }
  const failures = [];
  for (const [name, definition] of Object.entries(schema.$defs || {})) {
    for (const [field, property] of Object.entries(definition.properties || {})) {
      const before = base.$defs?.[name]?.properties?.[field];
      const after = restrictions(property, schema);
      if (!after.length || JSON.stringify(after) === JSON.stringify(restrictions(before, base))) continue;
      const symbol = symbols.get(name);
      if (!symbol) throw new Error(`missing generated type ${name}`);
      const type = checker.getDeclaredTypeOfSymbol(symbol);
      const member = checker.getPropertyOfType(type, field);
      if (!member) throw new Error(`missing generated property ${name}.${field}`);
      if (containsTuple(checker.getTypeOfSymbolAtLocation(member, source))) failures.push(`${name}.${field}`);
    }
  }
  if (failures.length) {
    process.stderr.write(`Tuple regression: ${failures.join(", ")}\n`);
    return 1;
  }
  process.stdout.write("Array constraints preserve ordinary generated arrays\n");
  return 0;
}
try { process.exitCode = main(); }
catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 2; }
