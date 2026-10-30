import { afterEach, describe, expect, it, vi } from "vitest";

import type { BoxSource } from "./boxSource";
import { createFieldController } from "./controller";
import type { FieldHost } from "./controller";
import { createLampRenderer } from "./renderer";
import type { LampLook } from "./renderer";

/** WebGL entry points for initialization and drawing; handles are tracked by identity. */
function context(failure: string) {
  const shaders: object[] = [];
  const programs: object[] = [];
  const textures: object[] = [];
  const deleted = { shaders: [] as object[], programs: [] as object[], textures: [] as object[] };
  const gl = {
    VERTEX_SHADER: 1,
    FRAGMENT_SHADER: 2,
    COMPILE_STATUS: 3,
    LINK_STATUS: 4,
    NO_ERROR: 0,
    TEXTURE_2D: 3553,
    TEXTURE0: 33984,
    TEXTURE1: 33985,
    RG: 33319,
    RG16F: 33324,
    FLOAT: 5126,
    createShader: vi.fn(() => {
      if (failure === `shader-null-${String(shaders.length)}`) return null;
      const handle = {};
      shaders.push(handle);
      return handle;
    }),
    shaderSource: vi.fn(),
    compileShader: vi.fn(),
    getShaderParameter: vi.fn(
      (shader: object) => shader !== shaders[Number(failure.split("compile-")[1])],
    ),
    deleteShader: vi.fn((handle: object) => deleted.shaders.push(handle)),
    createProgram: vi.fn(() => {
      if (failure === "program-null") return null;
      const handle = {};
      programs.push(handle);
      return handle;
    }),
    attachShader: vi.fn(),
    linkProgram: vi.fn(() => {
      if (failure === "link-throws") throw new Error("driver");
    }),
    getProgramParameter: vi.fn(() => failure !== "link"),
    deleteProgram: vi.fn((handle: object) => deleted.programs.push(handle)),
    createTexture: vi.fn(() => {
      if (failure === "texture-null") return null;
      const handle = {};
      textures.push(handle);
      return handle;
    }),
    deleteTexture: vi.fn((handle: object) => deleted.textures.push(handle)),
    getUniformLocation: vi.fn(() => (failure === "uniform-null" ? null : {})),
    bindTexture: vi.fn(),
    texParameteri: vi.fn(),
    pixelStorei: vi.fn(),
    disable: vi.fn(),
    getError: vi.fn((): number => (failure === "gl-error" ? 1285 : 0)),
    isContextLost: vi.fn(() => failure === "context-lost"),
    activeTexture: vi.fn(),
    texImage2D: vi.fn(),
    texSubImage2D: vi.fn(),
    viewport: vi.fn(),
    useProgram: vi.fn(),
    uniform1i: vi.fn(),
    uniform2f: vi.fn(),
    uniform1f: vi.fn(),
    uniform4f: vi.fn(),
    clearColor: vi.fn(),
    clear: vi.fn(),
    drawArrays: vi.fn(),
  };
  const canvas = { getContext: () => gl } as unknown as HTMLCanvasElement;
  return { canvas, gl, shaders, programs, textures, deleted };
}

describe("failed field initialization", () => {
  it.each([
    "compile-0",
    "compile-1",
    "link",
    "link-throws",
    "shader-null-0",
    "shader-null-1",
    "program-null",
  ])("releases every allocated resource after %s", (failure) => {
    const test = context(failure);
    expect(createLampRenderer(test.canvas)).toBeNull();
    expect(new Set(test.deleted.shaders)).toEqual(new Set(test.shaders));
    expect(new Set(test.deleted.programs)).toEqual(new Set(test.programs));
    expect(new Set(test.deleted.textures)).toEqual(new Set(test.textures));
    expect(test.deleted.shaders).toHaveLength(test.shaders.length);
  });

  it.each(["texture-null", "uniform-null", "gl-error", "context-lost"])(
    "falls back and releases resources after %s",
    (failure) => {
      const test = context(failure);
      expect(createLampRenderer(test.canvas)).toBeNull();
      expect(test.deleted.shaders).toHaveLength(test.shaders.length);
      expect(test.deleted.programs).toEqual(test.programs);
      expect(test.deleted.textures).toEqual(test.textures);
    },
  );

  it("also tolerates a context getter that throws", () => {
    const canvas = {
      getContext() {
        throw new Error("driver");
      },
    } as unknown as HTMLCanvasElement;
    expect(createLampRenderer(canvas)).toBeNull();
  });

  it("keeps successful resources until disposal and deletes each exactly once", () => {
    const test = context("");
    const renderer = createLampRenderer(test.canvas);
    expect(renderer).not.toBeNull();
    expect(test.deleted.shaders).toHaveLength(2);
    expect(test.deleted.programs).toEqual([]);
    expect(test.deleted.textures).toEqual([]);
    renderer?.dispose();
    renderer?.dispose();
    expect(test.deleted.programs).toEqual(test.programs);
    expect(test.deleted.textures).toEqual(test.textures);
  });
});

function mountCanvas(gl: unknown): FieldHost {
  vi.stubGlobal("window", Object.assign(new EventTarget(), { devicePixelRatio: 1 }));
  vi.stubGlobal(
    "document",
    Object.assign(new EventTarget(), {
      hidden: false,
      documentElement: { dataset: { theme: "dark" } },
    }),
  );
  vi.stubGlobal(
    "MutationObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => null);
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn(() => 1),
  );
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  return Object.assign(new EventTarget(), {
    getContext: () => gl,
    clientWidth: 70,
    clientHeight: 70,
    hidden: false,
  }) as unknown as FieldHost;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it.each(["draw-throws", "texture-allocation"])(
  "falls back through the controller and frees GL resources after a runtime %s",
  (failure) => {
    const test = context("");
    const canvas = mountCanvas(test.gl);
    const source = {
      boxes: () => new Map([["a", { x1: 0, y1: 0, x2: 10, y2: 10 }]]),
      subscribe: () => () => undefined,
    } as unknown as BoxSource;
    const controller = createFieldController(canvas, source);
    expect(canvas.oversightField?.state()).toBe("webgl");
    expect(test.gl.texImage2D).toHaveBeenCalledOnce();
    expect(test.gl.drawArrays).toHaveBeenCalledOnce();
    expect(test.deleted.programs).toEqual([]);
    expect(test.deleted.textures).toEqual([]);
    controller.update({
      mode: "watch",
      lights: [{ id: "a", source: "agent-a", weight: 1, pulses: true }],
    });
    expect(canvas.oversightField?.animating()).toBe(true);
    const draws = canvas.oversightField?.draws();
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fail = () => {
      test.gl.getError.mockReturnValue(1285);
    };
    if (failure === "texture-allocation") {
      Object.assign(canvas, { clientWidth: 140 });
      test.gl.texImage2D.mockImplementationOnce(fail);
    } else
      test.gl.drawArrays.mockImplementationOnce(() => {
        throw new Error("driver");
      });
    controller.redraw();
    expect(error).toHaveBeenCalledOnce();
    expect(canvas.hidden).toBe(true);
    expect(canvas.oversightField?.state()).toBe("plain");
    expect(canvas.oversightField?.animating()).toBe(false);
    expect(cancelAnimationFrame).toHaveBeenCalledWith(1);
    expect(canvas.oversightField?.draws()).toBe(draws);
    expect(canvas.oversightField?.lamp(0, 0)).toBeNull();
    expect(canvas.oversightField?.pixel(0, 0)).toBeNull();
    controller.redraw();
    controller.dispose();
    expect(test.deleted.programs).toEqual(test.programs);
    expect(test.deleted.textures).toEqual(test.textures);
    expect(test.deleted.shaders).toEqual([...test.shaders].reverse());
  },
);

const look: LampLook = {
  cell: 7,
  coverage: 0.72,
  guideSize: 1,
  guideEvery: 4,
  guide: { color: [237, 237, 234], alpha: 0.1 },
};

describe("field diagnostics", () => {
  it("checks initialization and allocations, without polling errors on repeated draws", () => {
    const { canvas, gl } = context("");
    const renderer = createLampRenderer(canvas);
    expect(renderer).not.toBeNull();
    expect(gl.getError).toHaveBeenCalledOnce();
    renderer?.resize(70, 70, 1);
    expect([canvas.width, canvas.height]).toEqual([70, 70]);
    expect(gl.getError).toHaveBeenCalledTimes(2);
    const grid = { cols: 10, rows: 10, data: new Uint8ClampedArray(400) };
    renderer?.draw(grid, look);
    expect(gl.getError).toHaveBeenCalledTimes(3);
    gl.getError.mockClear();
    for (let frame = 0; frame < 3; frame++) {
      renderer?.resize(70, 70, 1);
      renderer?.draw(grid, look);
    }
    expect(gl.getError).not.toHaveBeenCalled();
    expect(gl.texSubImage2D).toHaveBeenCalledTimes(3);
    renderer?.draw({ cols: 20, rows: 10, data: new Uint8ClampedArray(800) }, look);
    expect(gl.getError).toHaveBeenCalledOnce();
    renderer?.dispose();
  });

  it("checks errors on loss and restore while retaining the plain fallback", () => {
    const test = context("");
    const canvas = mountCanvas(test.gl);
    const source = {
      boxes: () => new Map(),
      subscribe: () => () => undefined,
    } as unknown as BoxSource;
    const controller = createFieldController(canvas, source);
    test.gl.getError.mockClear();
    test.gl.isContextLost.mockReturnValue(true);
    test.gl.getError.mockReturnValue(0x9242);
    canvas.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    expect(test.gl.getError).toHaveBeenCalledOnce();
    expect(canvas.hidden).toBe(true);
    expect(canvas.oversightField?.state()).toBe("plain");
    test.gl.isContextLost.mockReturnValue(false);
    test.gl.getError.mockReturnValue(0);
    canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(test.gl.getError).toHaveBeenCalledTimes(2);
    expect(canvas.oversightField?.state()).toBe("plain");
    controller.dispose();
    canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(test.gl.getError).toHaveBeenCalledTimes(2);
  });
});

it("uploads masked cells in the same draw as the LEDs and guides, without additional clears", () => {
  const { canvas, gl } = context("");
  const renderer = createLampRenderer(canvas);
  renderer?.resize(100, 100, 1.5);
  const grid = { cols: 15, rows: 15, data: new Uint8ClampedArray(900) };
  renderer?.draw(grid, look, [{ x1: 10.2, y1: 20.3, x2: 30.4, y2: 40.5 }]);
  expect(grid.data[(2 * 15 + 1) * 4 + 3]).toBe(1);
  expect(grid.data[(5 * 15 + 4) * 4 + 3]).toBe(1);
  expect(grid.data[(6 * 15 + 4) * 4 + 3]).toBe(0);
  expect(gl.texImage2D).toHaveBeenCalledOnce();
  expect(gl.drawArrays).toHaveBeenCalledOnce();
  expect(gl.clear).toHaveBeenCalledOnce();
  renderer?.dispose();
});

describe("contours", () => {
  const heights = { cols: 4, rows: 3, cell: 16, data: new Float32Array(4 * 3 * 2) };
  const contours = {
    heights,
    look: {
      color: [237, 237, 234] as [number, number, number],
      alpha: 0.09,
      step: 0.15,
      levels: 8,
      fade: 0.45,
      top: 0.72,
      width: 1,
    },
  };
  const grid = () => ({ cols: 10, rows: 10, data: new Uint8ClampedArray(400) });

  it("keeps one texture and turns contours off for a field drawn without them", () => {
    const { canvas, gl, textures } = context("");
    const renderer = createLampRenderer(canvas);
    renderer?.draw(grid(), look);
    expect(textures).toHaveLength(1);
    expect(gl.uniform1f).toHaveBeenCalledWith(expect.anything(), 0);
    expect(gl.uniform4f).not.toHaveBeenCalledWith(expect.anything(), 0.15, 8, 0.45, 0.72);
    renderer?.dispose();
  });

  it("makes the ground texture with the first contours, then only updates it", () => {
    const { canvas, gl, textures, deleted } = context("");
    const renderer = createLampRenderer(canvas);
    renderer?.draw(grid(), look, [], contours);
    expect(textures).toHaveLength(2);
    // The lamp texture, then the heights: two floats per node.
    expect(gl.texImage2D).toHaveBeenCalledTimes(2);
    expect(gl.texImage2D).toHaveBeenLastCalledWith(
      gl.TEXTURE_2D,
      0,
      gl.RG16F,
      4,
      3,
      0,
      gl.RG,
      gl.FLOAT,
      heights.data,
    );
    expect(gl.uniform4f).toHaveBeenCalledWith(expect.anything(), 0.15, 8, 0.45, 0.72);
    gl.texImage2D.mockClear();
    renderer?.draw(grid(), look, [], contours);
    expect(gl.texImage2D).not.toHaveBeenCalled();
    expect(gl.texSubImage2D).toHaveBeenLastCalledWith(
      gl.TEXTURE_2D,
      0,
      0,
      0,
      4,
      3,
      gl.RG,
      gl.FLOAT,
      heights.data,
    );
    renderer?.dispose();
    expect(new Set(deleted.textures)).toEqual(new Set(textures));
    expect(deleted.textures).toHaveLength(2);
  });

  it("allocates the heights again when the grid changes size, and checks the allocation", () => {
    const { canvas, gl } = context("");
    const renderer = createLampRenderer(canvas);
    renderer?.draw(grid(), look, [], contours);
    gl.getError.mockClear();
    renderer?.draw(grid(), look, [], contours);
    expect(gl.getError).not.toHaveBeenCalled();
    const bigger = { ...contours, heights: { ...heights, cols: 5, data: new Float32Array(30) } };
    renderer?.draw(grid(), look, [], bigger);
    expect(gl.getError).toHaveBeenCalledOnce();
    renderer?.dispose();
  });
});
