/** Placeholder rows while content loads. Shape first, colour never. */
export const LoadingSkeleton = ({ count = 3 }: { count?: number }) => (
  <div className="space-y-5" aria-hidden="true">
    {Array.from({ length: count }).map((_, i) => (
      <div key={i} className="space-y-2.5">
        <div className="skeleton h-3.5 w-1/3 rounded-full" />
        <div className="skeleton h-3.5 w-full rounded-full" />
        <div className="skeleton h-3.5 w-5/6 rounded-full" />
      </div>
    ))}
  </div>
);
