// run_detect (detect.sh) against fake detectors, with no servers and no browser: the scan is
// optional, so whatever the detector does, the function returns 0 and the run goes on.
import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { existsSync, watch } from "node:fs";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const run = promisify(execFile);
const root = resolve(import.meta.dirname, "../..");

interface Outcome {
  code: number;
  stdout: string;
  stderr: string;
  out: string;
}

// Runs run_detect as run.sh does (set -e), with `script` as the detector. The marker line after it
// proves the run went on.
async function detect(script: string, seconds: number, body: (out: string) => Promise<void>) {
  const dir = await mkdtemp(join(tmpdir(), "oversight-detect-"));
  try {
    const detector = join(dir, "impeccable");
    await writeFile(detector, `#!/bin/sh\n${script}\n`);
    await chmod(detector, 0o755);
    const command = `set -eu -o pipefail; source frontend/shots/detect.sh; run_detect "$1" http://x/ "$2" ${String(seconds)}; echo continued`;
    let outcome: Omit<Outcome, "out">;
    try {
      const { stdout, stderr } = await run("bash", ["-c", command, "bash", detector, dir], {
        cwd: root,
        env: { ...process.env, IMPECCABLE_BROWSER: "unused" },
        timeout: 20_000,
      });
      outcome = { code: 0, stdout, stderr };
    } catch (error) {
      const failed = error as { code?: number; stdout?: string; stderr?: string };
      outcome = {
        code: failed.code ?? -1,
        stdout: failed.stdout ?? "",
        stderr: failed.stderr ?? "",
      };
    }
    assert.equal(outcome.code, 0, `run_detect must not fail the run: ${outcome.stderr}`);
    assert.match(outcome.stdout, /continued/);
    await body(dir);
    return outcome;
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

void test("a detector that hangs is stopped at the deadline, leaves no detect.json, and the run goes on", async () => {
  const started = Date.now();
  const outcome = await detect('echo $$ > "$(dirname "$0")/pid"; exec sleep 30', 1, async (dir) => {
    assert.equal(existsSync(join(dir, "detect.json")), false);
    const pid = Number((await readFile(join(dir, "pid"), "utf8")).trim());
    assert.throws(() => process.kill(pid, 0), { code: "ESRCH" }, "the hung detector is dead");
  });
  assert.match(outcome.stderr, /did not finish within 1 s/);
  assert.ok(Date.now() - started < 15_000, "returned long before the detector would have");
});

// A fake detector like a Chrome that outlives it: it starts a child that ignores SIGTERM, records
// both PIDs (written to a temp name and renamed, so a watcher sees them whole), exits on SIGTERM, and
// otherwise waits. The child never ends by itself.
const stubborn = `
record() { echo "$2" > "$1.tmp"; mv "$1.tmp" "$1"; }
dir=$(dirname "$0")
( trap '' TERM; exec sleep 30 ) &
record "$dir/child" $!
trap 'exit 0' TERM
record "$dir/pid" $$
wait
`;

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function pidIn(dir: string, name: string): Promise<number> {
  return Number((await readFile(join(dir, name), "utf8")).trim());
}

// Resolves when every named file exists in dir. Start it before the process that writes them.
function appear(dir: string, names: string[]): Promise<void> {
  return new Promise((done) => {
    const watcher = watch(dir, () => {
      if (names.every((name) => existsSync(join(dir, name)))) {
        watcher.close();
        done();
      }
    });
  });
}

void test("a child that ignores SIGTERM dies with the detector at the deadline", async () => {
  let child = 0;
  try {
    const outcome = await detect(stubborn, 1, async (dir) => {
      child = await pidIn(dir, "child");
      assert.equal(alive(await pidIn(dir, "pid")), false, "the detector is dead");
      assert.equal(alive(child), false, "its child, which ignores SIGTERM, is dead too");
    });
    assert.match(outcome.stderr, /did not finish within 1 s/);
  } finally {
    if (child > 0 && alive(child)) process.kill(child, "SIGKILL");
  }
});

void test("a signal to the shell during the scan stops the whole group before the shell goes", async () => {
  const dir = await mkdtemp(join(tmpdir(), "oversight-detect-"));
  let child = 0;
  try {
    const detector = join(dir, "impeccable");
    await writeFile(detector, `#!/bin/sh\n${stubborn}\n`);
    await chmod(detector, 0o755);
    const recorded = appear(dir, ["child", "pid"]);
    // As run.sh does: a TERM trap that exits.
    const command =
      "set -eu -o pipefail; source frontend/shots/detect.sh; trap 'exit 143' TERM; " +
      'run_detect "$1" http://x/ "$2" 30; echo continued';
    const shell = spawn("bash", ["-c", command, "bash", detector, dir], {
      cwd: root,
      env: { ...process.env, IMPECCABLE_BROWSER: "unused" },
      stdio: "ignore",
    });
    const closed = new Promise<number | null>((done) => shell.on("close", done));
    await recorded;
    child = await pidIn(dir, "child");
    const detectorPid = await pidIn(dir, "pid");
    shell.kill("SIGTERM");
    assert.equal(await closed, 143);
    assert.equal(alive(detectorPid), false, "the detector is dead");
    assert.equal(alive(child), false, "its child is dead");
  } finally {
    if (child > 0 && alive(child)) process.kill(child, "SIGKILL");
    await rm(dir, { recursive: true, force: true });
  }
});

void test("exit 2 is a completed scan with findings: detect.json is kept", async () => {
  const outcome = await detect('echo \'[{"rule":"x"}]\'; exit 2', 5, async (dir) => {
    assert.equal(await readFile(join(dir, "detect.json"), "utf8"), '[{"rule":"x"}]\n');
  });
  assert.match(outcome.stdout, /detect scanned http:\/\/x\/ \(exit 2\)/);
});

void test("exit 1 is a failed scan: no detect.json, which would read as no findings", async () => {
  const outcome = await detect("echo '[]'; echo no browser >&2; exit 1", 5, async (dir) => {
    assert.equal(existsSync(join(dir, "detect.json")), false);
    assert.match(await readFile(join(dir, "detect.err"), "utf8"), /no browser/);
  });
  assert.match(outcome.stderr, /could not scan http:\/\/x\/ \(exit 1\)/);
});

void test("a missing detector is one line and the run goes on", async () => {
  const missing = await run(
    "bash",
    [
      "-c",
      "set -eu; source frontend/shots/detect.sh; run_detect /nonexistent/impeccable http://x/ /tmp 5; echo continued",
    ],
    { cwd: root },
  );
  assert.match(missing.stdout, /is missing or not executable, detect skipped\ncontinued/);
});
