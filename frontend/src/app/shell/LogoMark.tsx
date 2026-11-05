/**
 * The Oversight mark: a lens drawn as two graph edges between two nodes, around one approved node.
 * It paints in currentColor, so it follows the text token in dark and day. Decorative only: the
 * wordmark beside it carries the name.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" className={className}>
      <path
        d="M2.5 12A10.52 10.52 0 0 1 21.5 12A10.52 10.52 0 0 1 2.5 12Z"
        stroke="currentColor"
        strokeWidth="2"
      />
      <g fill="currentColor">
        <circle cx="2.5" cy="12" r="2.25" />
        <circle cx="21.5" cy="12" r="2.25" />
        <circle cx="12" cy="12" r="3.25" />
      </g>
    </svg>
  );
}
