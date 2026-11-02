import type { MapFrame } from "./MapLayer";
import { mapSegments } from "./mapLinesModel";

function MapLine({ line }: { line: ReturnType<typeof mapSegments>[number] }) {
  const { start: a, end: b } = line;
  const angle = Math.atan2(b.y - a.y, b.x - a.x),
    ux = Math.cos(angle),
    uy = Math.sin(angle);
  return (
    <g
      data-map-line={line.cone ? "cone" : line.possible ? "possible" : "exact"}
      style={{
        color: `var(--${line.cone ? "lit-edge" : line.possible ? "edge-possible" : "edge"})`,
      }}
    >
      <line
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        stroke="currentColor"
        style={{
          strokeWidth: `var(--${line.cone ? "lit-edge-width" : line.possible ? "possible-edge-width" : "exact-edge-width"})`,
        }}
      />
      {!line.possible && (
        <polygon
          fill="currentColor"
          points={`${String(b.x)},${String(b.y)} ${String(b.x - 7 * ux + 3 * uy)},${String(b.y - 7 * uy - 3 * ux)} ${String(b.x - 7 * ux - 3 * uy)},${String(b.y - 7 * uy + 3 * ux)}`}
        />
      )}
      {line.chip && (
        <g data-map-call-chip>
          <rect
            x={line.chip.x - (line.chip.box.x2 - line.chip.box.x1 - 4) / 2}
            y={line.chip.y - 8}
            width={line.chip.box.x2 - line.chip.box.x1 - 4}
            height={16}
            rx={8}
            fill="var(--ground)"
            stroke="var(--border)"
          />
          <text
            x={line.chip.x}
            y={line.chip.y}
            textAnchor="middle"
            dominantBaseline="central"
            fill="var(--text)"
            fontSize={10}
            fontWeight={600}
          >
            ×{line.count}
          </text>
        </g>
      )}
    </g>
  );
}

/** One pointer-free SVG, omitted entirely when there are no evidence lines. */
export function MapLines({ frame }: { frame: MapFrame }) {
  const segments = mapSegments(frame);
  return segments.length ? (
    <svg className="absolute inset-0 size-full" style={{ zIndex: 1 }}>
      {segments.map((line) => (
        <MapLine key={`${line.source}>${line.target}`} line={line} />
      ))}
    </svg>
  ) : null;
}
