import type { Page } from "@playwright/test";
import { stopXvfb } from "./chromeShortcut.support";
import { launchBrowser } from "../browserLaunch.support";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { promisify } from "node:util";
import { expect, it } from "vitest";

// CDP's keyboard.press dispatches to the page and bypasses Chrome accelerators. Use XTest
// against a headed Chrome in its own X server to measure the actual browser shortcut.
const nativeKeys = String.raw`
import ctypes as c
import sys
x = c.CDLL("libX11.so.6")
t = c.CDLL("libXtst.so.6")
x.XOpenDisplay.restype = c.c_void_p
x.XOpenDisplay.argtypes = [c.c_char_p]
d = x.XOpenDisplay(sys.argv[1].encode())
assert d
x.XDefaultRootWindow.argtypes = [c.c_void_p]
x.XDefaultRootWindow.restype = c.c_ulong
x.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_ulong), c.POINTER(c.POINTER(c.c_ulong)), c.POINTER(c.c_uint)]
x.XFetchName.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_char_p)]
x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
x.XInternAtom.restype = c.c_ulong
x.XGetWindowProperty.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_long, c.c_long, c.c_int, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_int), c.POINTER(c.c_ulong), c.POINTER(c.c_ulong), c.POINTER(c.c_char_p)]
atom = x.XInternAtom(d, b"_NET_WM_NAME", 0)
x.XMapRaised.argtypes = [c.c_void_p, c.c_ulong]
x.XSetInputFocus.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_ulong]
x.XGetInputFocus.argtypes = [c.c_void_p, c.POINTER(c.c_ulong), c.POINTER(c.c_int)]
x.XSync.argtypes = [c.c_void_p, c.c_int]
x.XFree.argtypes = [c.c_void_p]
root = x.XDefaultRootWindow(d)
rr, parent, count = c.c_ulong(), c.c_ulong(), c.c_uint()
children = c.POINTER(c.c_ulong)()
x.XQueryTree(d, root, c.byref(rr), c.byref(parent), c.byref(children), c.byref(count))
found = False
names = []
for i in range(count.value):
    name = c.c_char_p()
    kind, fmt, length, rest = c.c_ulong(), c.c_int(), c.c_ulong(), c.c_ulong()
    x.XGetWindowProperty(d, children[i], atom, 0, 1024, 0, 0, c.byref(kind), c.byref(fmt), c.byref(length), c.byref(rest), c.byref(name))
    if not name.value:
        x.XFetchName(d, children[i], c.byref(name))
    names.append(name.value)
    if name.value and b"Oversight shortcut probe" in name.value:
        x.XMapRaised(d, children[i])
        x.XSetInputFocus(d, children[i], 2, 0)
        found = children[i]
    if name:
        x.XFree(name)
x.XFree(children)
assert found, f"Chrome window not found: {names}"
x.XSync(d, 0)
focus, revert = c.c_ulong(), c.c_int()
x.XGetInputFocus(d, c.byref(focus), c.byref(revert))
assert focus.value == found, f"Chrome window not focused: {focus.value} != {found}"
x.XStringToKeysym.argtypes = [c.c_char_p]
x.XStringToKeysym.restype = c.c_ulong
x.XKeysymToKeycode.argtypes = [c.c_void_p, c.c_ulong]
x.XKeysymToKeycode.restype = c.c_uint
t.XTestFakeKeyEvent.argtypes = [c.c_void_p, c.c_uint, c.c_int, c.c_ulong]
keys = [x.XKeysymToKeycode(d, x.XStringToKeysym(key.encode())) for key in sys.argv[2:]]
for key in keys:
    t.XTestFakeKeyEvent(d, key, 1, 0)
for key in reversed(keys):
    t.XTestFakeKeyEvent(d, key, 0, 0)
x.XSync(d, 0)
`;

type Key = { key: string; ctrl: boolean; alt: boolean };

async function proveFocus(page: Page, keys: Key[], display: string) {
  // XSetInputFocus returning does not mean Chrome has handled the focus event yet.
  // Retry the harmless Alt+W until the page acknowledges it, not just until XTest sends it.
  await expect
    .poll(
      async () => {
        if (keys.some((key) => key.key === "w" && key.alt)) return true;
        try {
          await promisify(execFile)("python3", ["-c", nativeKeys, display], { timeout: 1_000 });
          if (!(await page.evaluate(() => document.hasFocus()))) return false;
          await promisify(execFile)("python3", ["-c", nativeKeys, display, "Alt_L", "w"], {
            timeout: 1_000,
          });
          return keys.some((key) => key.key === "w" && key.alt);
        } catch (error) {
          return String(error);
        }
      },
      { timeout: 10_000 },
    )
    .toBe(true);
  await expect.poll(() => page.evaluate(() => document.hasFocus()), { timeout: 5_000 }).toBe(true);
}

it("Chrome reserves native Ctrl+W; Alt+W reaches the page", async () => {
  const xvfb = spawn(
    "Xvfb",
    [
      "-displayfd",
      "1",
      "-nolisten",
      "unix",
      "-listen",
      "tcp",
      "-ac",
      "-screen",
      "0",
      "1920x1080x24",
    ],
    {
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let browser;
  let failure: { error: unknown } | undefined;
  try {
    const [data] = (await once(xvfb.stdout, "data")) as [Buffer];
    const display = `127.0.0.1:${data.toString().trim()}`;
    browser = await launchBrowser({
      headless: false,
      args: ["--ozone-platform=x11"],
      env: { ...process.env, DISPLAY: display },
    });
    const context = await browser.newContext();
    const survivor = await context.newPage(); // Closing the probe must leave this page alive.
    const page = await context.newPage();
    const keys: Key[] = [];
    await page.exposeFunction("reportKey", (value: (typeof keys)[number]) => {
      keys.push(value);
    });
    await page.setContent("<title>Oversight shortcut probe</title><p>Native keyboard probe</p>");
    await page.evaluate(() => {
      document.addEventListener("keydown", (event) => {
        const report = Reflect.get(window, "reportKey") as (value: {
          key: string;
          ctrl: boolean;
          alt: boolean;
        }) => Promise<void>;
        void report({ key: event.key, ctrl: event.ctrlKey, alt: event.altKey });
      });
    });
    await page.bringToFront();
    await proveFocus(page, keys, display);
    // Allow slow CI startup: unlike Alt+W, the destructive Ctrl+W must never be retried.
    await promisify(execFile)("python3", ["-c", nativeKeys, display, "Control_L", "w"], {
      timeout: 5_000,
    });
    await expect.poll(() => page.isClosed(), { timeout: 5_000 }).toBe(true);
    expect(browser.isConnected()).toBe(true);
    expect(survivor.isClosed()).toBe(false);
    expect(await survivor.evaluate(() => document.readyState)).toBe("complete");
    expect(keys.some((key) => key.key === "w" && key.ctrl)).toBe(false);
  } catch (error) {
    failure = { error };
  } finally {
    try {
      await browser?.close();
    } catch (error) {
      failure ??= { error };
    } finally {
      await stopXvfb(xvfb);
    }
  }
  if (failure) throw failure.error;
}, 30_000);
