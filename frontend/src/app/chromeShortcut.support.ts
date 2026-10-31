import type { ChildProcess } from "node:child_process";

export async function stopXvfb(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise<void>((resolve) => {
    const onExit = () => {
      clearTimeout(timer);
      child.off("exit", onExit);
      resolve();
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      onExit();
    }, 5_000);
    child.once("exit", onExit);
    child.kill("SIGTERM");
  });
}
