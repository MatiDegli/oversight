// The dot field's lamps on a WebGL2 canvas. The model has already said which lamp shows what: one
// RGBA texel per cell, the lamp's color and, as alpha, its level. One full-screen triangle draws
// them: each fragment finds its cell, keeps the lamp's square (72% of the cell) and, on a lamp that
// is off, the guide dot every few cells. The gaps between lamps stay transparent, so the ground
// behind the canvas shows. With contours, the same pass draws the rest ground's lines from a height
// grid, in the places no lamp is drawn: there an off lamp is not drawn at all. Where WebGL2 is
// missing this returns null and the graph shows plain ground.
import { maskLamps } from "./mask";
import type { Box, LampGrid, Rgb } from "./model";
import type { Contours } from "./terrain";

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
  /** Diagnoses lifecycle boundaries, never the steady drawing path. */
  check: () => void;
  /** Sizes the drawing buffer to a CSS size at a device pixel ratio. */
  resize(width: number, height: number, ratio: number): void;
  /** With `contours`, the rest ground's lines are drawn too; without them the field is as it was. */
  draw(grid: LampGrid, look: LampLook, masks?: readonly Box[], contours?: Contours): void;
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
uniform sampler2D uTerrain;
uniform vec2 uTerrainSize;
uniform float uTerrainCell;
uniform float uContourOn;
uniform vec4 uContour;
uniform vec4 uContourLook;
uniform float uContourWidth;
out vec4 outColor;

// A CSS length in whole device pixels, so lamps keep sharp edges at any pixel ratio.
vec2 snap(vec2 css) { return floor(css * uScale + 0.5); }

bool inside(vec2 p, vec2 low, vec2 size) {
  return all(greaterThanEqual(p, low)) && all(lessThan(p, low + size));
}

// The line of the rest ground at a point: its alpha, premultiplied. The heights are the red
// channel, the cover of light the green one. A line is the distance to the nearest whole level,
// measured in pixels by the slope, so it is one pixel wide and soft at any angle.
vec4 contourAt(float f, float slope, float cover) {
  float level = floor(f + 0.5);
  if (level < 1.0 || level > uContourLook.y) return vec4(0.0);
  // A soft 1px line: whole within a tenth of a pixel of its center, gone 1.1 px past its edge.
  float reach = 0.5 * uContourWidth * uScale + 1.1;
  float line = clamp(reach - abs(f - level) / max(slope, 0.0005), 0.0, 1.0);
  float share = clamp(level * uContourLook.x / uContourLook.w, 0.0, 1.0);
  float a = line * mix(uContourLook.z, 1.0, share) * uContour.a
      * (1.0 - smoothstep(0.02, 0.35, cover));
  return vec4(uContour.rgb * a, a);
}

void main() {
  vec2 p = vec2(gl_FragCoord.x, uView.y - gl_FragCoord.y);
  vec2 cell = floor(p / (uCell * uScale));
  // Derivatives need every fragment, so the terrain is read before any early return.
  float f = 0.0;
  float cover = 0.0;
  if (uContourOn > 0.5) {
    vec2 ground = texture(uTerrain, (p / uScale / uTerrainCell + 0.5) / uTerrainSize).rg;
    f = ground.r / uContourLook.x;
    cover = ground.g;
  }
  float slope = fwidth(f);
  outColor = vec4(0.0);
  if (cell.x >= uGrid.x || cell.y >= uGrid.y) return;
  vec4 lamp = texelFetch(uCells, ivec2(cell), 0);
  if (lamp.a > 0.0 && lamp.a < 0.01) return;
  vec2 origin = cell * uCell;
  bool onLamp = inside(p, snap(origin + 0.5 * (uCell - uLamp)), snap(vec2(uLamp)));
  ivec2 index = ivec2(cell);
  bool guide = lamp.a < 0.001 && index.x % uEvery == 0 && index.y % uEvery == 0
      && inside(p, snap(origin + 0.5 * (uCell - uGuide)), snap(vec2(uGuide)));
  if (uContourOn > 0.5) {
    // An off lamp is not drawn: only a lit one, a guide dot, or the ground's line.
    if (onLamp && lamp.a >= 0.01) {
      outColor = vec4(lamp.rgb, 1.0);
      return;
    }
    vec4 line = contourAt(f, slope, cover);
    float g = guide ? uGuideColor.a : 0.0;
    outColor = vec4(uGuideColor.rgb * g, g) + line * (1.0 - g);
    return;
  }
  if (!onLamp) return;
  vec3 color = lamp.rgb;
  if (guide) color = mix(color, uGuideColor.rgb, uGuideColor.a);
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
  const error = gl.getError();
  if (gl.isContextLost() || error !== gl.NO_ERROR) throw new Error("WebGL context failed");
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
    terrain: uniform("uTerrain"),
    terrainSize: uniform("uTerrainSize"),
    terrainCell: uniform("uTerrainCell"),
    contourOn: uniform("uContourOn"),
    contour: uniform("uContour"),
    contourLook: uniform("uContourLook"),
    contourWidth: uniform("uContourWidth"),
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
  // Made when the first contours come, so a field without them holds one texture only.
  let ground: WebGLTexture | null = null;
  let groundSize = { cols: 0, rows: 0 };
  return {
    check: () => {
      check(gl);
    },
    resize(width, height, nextRatio) {
      ratio = nextRatio;
      const w = Math.max(1, Math.round(width * ratio));
      const h = Math.max(1, Math.round(height * ratio));
      if (canvas.width !== w || canvas.height !== h) {
        if (canvas.width !== w) canvas.width = w;
        if (canvas.height !== h) canvas.height = h;
        check(gl);
      }
    },
    draw(grid, look, masks = [], contours) {
      gl.activeTexture(gl.TEXTURE0);
      maskLamps(grid, look.cell, masks);
      upload(gl, grid, texture, uploaded);
      uploaded = { cols: grid.cols, rows: grid.rows };
      gl.activeTexture(gl.TEXTURE1);
      if (contours) {
        ground ??= groundTexture(resources);
        groundSize = uploadGround(gl, contours, ground, groundSize);
      } else gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.activeTexture(gl.TEXTURE0);
      paint(gl, canvas, initialized, grid, look, ratio, contours);
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
    check(gl);
  }
}

/** The height texture: two floats per node, read with linear filtering for smooth lines. */
function groundTexture(resources: Resources): WebGLTexture {
  const { gl } = resources;
  const texture = resources.own(gl.createTexture(), (handle) => {
    gl.deleteTexture(handle);
  });
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return texture;
}

function uploadGround(
  gl: WebGL2RenderingContext,
  { heights }: Contours,
  texture: WebGLTexture,
  known: { cols: number; rows: number },
): { cols: number; rows: number } {
  const { cols, rows, data } = heights;
  gl.bindTexture(gl.TEXTURE_2D, texture);
  if (known.cols === cols && known.rows === rows) {
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, cols, rows, gl.RG, gl.FLOAT, data);
  } else {
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RG16F, cols, rows, 0, gl.RG, gl.FLOAT, data);
    check(gl);
  }
  return { cols, rows };
}

function paint(
  gl: WebGL2RenderingContext,
  canvas: HTMLCanvasElement,
  { program, at }: Initialized,
  grid: LampGrid,
  look: LampLook,
  ratio: number,
  contours: Contours | undefined,
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
  gl.uniform1i(at.terrain, 1);
  gl.uniform1f(at.contourOn, contours ? 1 : 0);
  if (contours) {
    const { heights, look: line } = contours;
    const [cr, cg, cb] = line.color;
    gl.uniform2f(at.terrainSize, heights.cols, heights.rows);
    gl.uniform1f(at.terrainCell, heights.cell);
    gl.uniform4f(at.contour, cr / 255, cg / 255, cb / 255, line.alpha);
    gl.uniform4f(at.contourLook, line.step, line.levels, line.fade, line.top);
    gl.uniform1f(at.contourWidth, line.width);
  }
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
}
