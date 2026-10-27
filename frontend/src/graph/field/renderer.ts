// The dot field's lamps on a WebGL2 canvas. The model has already said which lamp shows what: one
// RGBA texel per cell, the lamp's color and, as alpha, its level. One full-screen triangle draws
// them: each fragment finds its cell, keeps the lamp's square (72% of the cell) and, on a lamp that
// is off, the guide dot every few cells. The gaps between lamps stay transparent, so the ground
// behind the canvas shows. Where WebGL2 is missing this returns null and the graph shows plain ground.
import type { LampGrid, Rgb } from "./model";

/** What the shader draws besides the grid: sizes in CSS px and the guide dot's color. */
export interface LampLook {
  cell: number;
  /** The lamp's side as a share of the cell. */
  coverage: number;
  guideSize: number;
  /** A guide dot every this many cells. */
  guideEvery: number;
  guide: { color: Rgb; alpha: number };
}

export interface LampRenderer {
  /** Sizes the drawing buffer to a CSS size at a device pixel ratio. */
  resize(width: number, height: number, ratio: number): void;
  draw(grid: LampGrid, look: LampLook): void;
  /** The pixel last drawn at a point in CSS px, as red, green, blue and alpha, or null off the buffer. */
  pixel(x: number, y: number): [number, number, number, number] | null;
  /** True once the browser took the context away. */
  lost(): boolean;
  dispose(): void;
}

const VERTEX = `#version 300 es
void main() {
  vec2 corner = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(corner * 2.0 - 1.0, 0.0, 1.0);
}`;

const FRAGMENT = `#version 300 es
precision highp float;
precision highp int;
uniform sampler2D uCells;
uniform vec2 uView;
uniform vec2 uGrid;
uniform float uScale;
uniform float uCell;
uniform float uLamp;
uniform float uGuide;
uniform int uEvery;
uniform vec4 uGuideColor;
out vec4 outColor;

// A CSS length in whole device pixels, so lamps keep sharp edges at any pixel ratio.
vec2 snap(vec2 css) { return floor(css * uScale + 0.5); }

bool inside(vec2 p, vec2 low, vec2 size) {
  return all(greaterThanEqual(p, low)) && all(lessThan(p, low + size));
}

void main() {
  vec2 p = vec2(gl_FragCoord.x, uView.y - gl_FragCoord.y);
  vec2 cell = floor(p / (uCell * uScale));
  outColor = vec4(0.0);
  if (cell.x >= uGrid.x || cell.y >= uGrid.y) return;
  vec2 origin = cell * uCell;
  if (!inside(p, snap(origin + 0.5 * (uCell - uLamp)), snap(vec2(uLamp)))) return;
  vec4 lamp = texelFetch(uCells, ivec2(cell), 0);
  vec3 color = lamp.rgb;
  ivec2 index = ivec2(cell);
  if (lamp.a < 0.001 && index.x % uEvery == 0 && index.y % uEvery == 0
      && inside(p, snap(origin + 0.5 * (uCell - uGuide)), snap(vec2(uGuide)))) {
    color = mix(color, uGuideColor.rgb, uGuideColor.a);
  }
  outColor = vec4(color, 1.0);
}`;

/** Own handles immediately, including partially initialized resources. */
class Resources {
  private readonly cleanup: (() => void)[] = [];
  constructor(readonly gl: WebGL2RenderingContext) {}
  own<T>(handle: T | null, release: (handle: T) => void): T {
    if (handle === null) throw new Error("WebGL allocation failed");
    this.cleanup.push(() => {
      release(handle);
    });
    return handle;
  }
  dispose = () => {
    for (const release of this.cleanup.splice(0).reverse()) release();
  };
}

function compile(resources: Resources, type: number, source: string): WebGLShader {
  const { gl } = resources;
  const shader = resources.own(gl.createShader(type), (handle) => {
    gl.deleteShader(handle);
  });
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS) !== true)
    throw new Error("WebGL shader compile failed");
  return shader;
}

function link(resources: Resources): WebGLProgram {
  const { gl } = resources;
  const shaders = new Resources(gl);
  try {
    const vertex = compile(shaders, gl.VERTEX_SHADER, VERTEX);
    const fragment = compile(shaders, gl.FRAGMENT_SHADER, FRAGMENT);
    const program = resources.own(gl.createProgram(), (handle) => {
      gl.deleteProgram(handle);
    });
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (gl.getProgramParameter(program, gl.LINK_STATUS) !== true)
      throw new Error("WebGL program link failed");
    return program;
  } finally {
    shaders.dispose();
  }
}

function check(gl: WebGL2RenderingContext) {
  if (gl.isContextLost() || gl.getError() !== gl.NO_ERROR) throw new Error("WebGL context failed");
}

function initialize(resources: Resources) {
  const { gl } = resources;
  const program = link(resources);
  const texture = resources.own(gl.createTexture(), (handle) => {
    gl.deleteTexture(handle);
  });
  const uniform = (name: string) => {
    const location = gl.getUniformLocation(program, name);
    if (location === null) throw new Error(`Missing WebGL uniform: ${name}`);
    return location;
  };
  const at = {
    cells: uniform("uCells"),
    view: uniform("uView"),
    grid: uniform("uGrid"),
    scale: uniform("uScale"),
    cell: uniform("uCell"),
    lamp: uniform("uLamp"),
    guide: uniform("uGuide"),
    every: uniform("uEvery"),
    guideColor: uniform("uGuideColor"),
  };
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.disable(gl.BLEND);
  check(gl);
  return { program, texture, at };
}

type Initialized = ReturnType<typeof initialize>;

/** Null on any failed initialization; no resources survive a failed attempt. */
export function createLampRenderer(canvas: HTMLCanvasElement): LampRenderer | null {
  let resources: Resources | undefined;
  try {
    const gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      premultipliedAlpha: true,
      preserveDrawingBuffer: false,
    });
    if (!gl) return null;
    resources = new Resources(gl);
    return renderer(canvas, resources, initialize(resources));
  } catch {
    resources?.dispose();
    return null;
  }
}

function renderer(
  canvas: HTMLCanvasElement,
  resources: Resources,
  initialized: Initialized,
): LampRenderer {
  const { gl } = resources;
  const { texture } = initialized;
  let ratio = 1;
  let uploaded = { cols: 0, rows: 0 };
  return {
    resize(width, height, nextRatio) {
      ratio = nextRatio;
      const w = Math.max(1, Math.round(width * ratio));
      const h = Math.max(1, Math.round(height * ratio));
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
    },
    draw(grid, look) {
      gl.activeTexture(gl.TEXTURE0);
      upload(gl, grid, texture, uploaded);
      uploaded = { cols: grid.cols, rows: grid.rows };
      paint(gl, canvas, initialized, grid, look, ratio);
      check(gl);
    },
    pixel(x, y) {
      const px = Math.floor(x * ratio);
      const py = canvas.height - 1 - Math.floor(y * ratio);
      if (px < 0 || py < 0 || px >= canvas.width || py >= canvas.height) return null;
      const out = new Uint8Array(4);
      gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, out);
      return [out[0] ?? 0, out[1] ?? 0, out[2] ?? 0, out[3] ?? 0];
    },
    lost: () => gl.isContextLost(),
    dispose: resources.dispose,
  };
}

function upload(
  gl: WebGL2RenderingContext,
  grid: LampGrid,
  texture: WebGLTexture,
  uploaded: { cols: number; rows: number },
) {
  gl.bindTexture(gl.TEXTURE_2D, texture);
  if (uploaded.cols === grid.cols && uploaded.rows === grid.rows) {
    gl.texSubImage2D(
      gl.TEXTURE_2D,
      0,
      0,
      0,
      grid.cols,
      grid.rows,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      grid.data,
    );
  } else {
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA8,
      grid.cols,
      grid.rows,
      0,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      grid.data,
    );
  }
}

function paint(
  gl: WebGL2RenderingContext,
  canvas: HTMLCanvasElement,
  { program, at }: Initialized,
  grid: LampGrid,
  look: LampLook,
  ratio: number,
) {
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.useProgram(program);
  gl.uniform1i(at.cells, 0);
  gl.uniform2f(at.view, canvas.width, canvas.height);
  gl.uniform2f(at.grid, grid.cols, grid.rows);
  gl.uniform1f(at.scale, ratio);
  gl.uniform1f(at.cell, look.cell);
  gl.uniform1f(at.lamp, look.cell * look.coverage);
  gl.uniform1f(at.guide, look.guideSize);
  gl.uniform1i(at.every, look.guideEvery);
  const [r, g, b] = look.guide.color;
  gl.uniform4f(at.guideColor, r / 255, g / 255, b / 255, look.guide.alpha);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
}
