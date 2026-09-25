/**
 * The waiting state. A shimmering word and three breathing dots — enough to
 * say "working", quiet enough not to pull the eye off the thread.
 */
export const ThinkingIndicator = ({ label = 'Thinking' }: { label?: string }) => (
  <div className="flex items-center gap-2.5 py-1" role="status" aria-live="polite">
    <span className="shimmer-text text-[0.9375rem] font-medium">{label}</span>

    <span className="flex items-center gap-1" aria-hidden="true">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="dot-pulse w-1.5 h-1.5 rounded-full bg-ink-3"
          style={{ animationDelay: `${i * 0.16}s` }}
        />
      ))}
    </span>

    <span className="sr-only">RAGNOUS is preparing an answer</span>
  </div>
);
