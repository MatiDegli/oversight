/* Validate serialized legacy logs using the existing, lock-pinned ESLint Ajv dependency. */
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const eslintRequire = createRequire(fs.realpathSync("frontend/node_modules/eslint/package.json"));
const Ajv = eslintRequire("ajv");

// Ajv 6 implements draft 7. Our generated vocabulary differs only in the fixed-tuple keyword:
// preserve every constraint, moving prefixItems to draft 7's tuple-form items. Local $defs
// references resolve as ordinary JSON pointers. Do not reuse the TypeScript projection, which
// intentionally removes list bounds.
function draft7(value) {
  if (Array.isArray(value)) return value.map(draft7);
  if (!value || typeof value !== "object") return value;
  const result = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, draft7(item)]));
  delete result.$schema;
  if (result.prefixItems) {
    if (result.items !== undefined) throw new Error("Unexpected trailing tuple items");
    result.items = result.prefixItems;
    delete result.prefixItems;
  }
  return result;
}

const schema = draft7(JSON.parse(fs.readFileSync("contracts/generated/oversight.schema.json", "utf8")));
const ajv = new Ajv({ allErrors: true });
const validate = ajv.compile({ ...schema, $ref: "#/$defs/ServerEvent" });
const records = JSON.parse(fs.readFileSync(path.resolve(process.argv[2]), "utf8"));
for (const { source, event } of records) {
  if (!validate(event)) throw new Error(`${source}: ${ajv.errorsText(validate.errors)}`);
}
// A negative control makes missing required keys and permissive unknown-key handling visible.
const first = records[0].event;
for (const invalid of [{ ...first, seq: 0 }, { ...first, extra: true }, { ...first, payload: {} }]) {
  if (validate(invalid)) throw new Error("Schema validator admitted invalid event");
}
process.stdout.write(`Validated ${records.length} recorded events against generated schema\n`);
