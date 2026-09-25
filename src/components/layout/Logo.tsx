interface LogoProps {
  /** Square edge in px. */
  size?: number;
  className?: string;
}

/** The mark: a black tile with a thin orange orbit — the only place brand colour leads. */
export const LogoMark = ({ size = 26, className = '' }: LogoProps) => (
  <span
    className={`inline-grid place-items-center rounded-[8px] bg-ink shrink-0 ${className}`}
    style={{ width: size, height: size }}
    aria-hidden="true"
  >
    <svg width={size * 0.62} height={size * 0.62} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="8" stroke="#FFFFFF" strokeWidth="2.2" />
      <circle cx="12" cy="12" r="2.8" fill="#FFFFFF" />
    </svg>
  </span>
);

export const Wordmark = ({ className = '' }: { className?: string }) => (
  <span className={`font-semibold tracking-[-0.01em] text-ink ${className}`}>RAGNOUS</span>
);

export const Logo = ({ size = 26 }: LogoProps) => (
  <span className="flex items-center gap-2.5">
    <LogoMark size={size} />
    <Wordmark className="text-[0.9375rem]" />
  </span>
);
