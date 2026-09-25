import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';

export interface SelectOption {
  id: string;
  label: string;
  /** Optional second line inside the menu — never shown on the trigger. */
  description?: string;
}

interface SelectProps {
  value: string;
  options: SelectOption[];
  onChange: (id: string) => void;
  align?: 'left' | 'right';
  /** Opens upward when the trigger sits near the bottom of the viewport. */
  placement?: 'bottom' | 'top';
  menuWidth?: string;
  label?: string;
  className?: string;
}

/** A quiet dropdown: text + chevron at rest, a bordered sheet when open. */
export const Select = ({
  value,
  options,
  onChange,
  align = 'left',
  placement = 'bottom',
  menuWidth = 'min-w-[13rem]',
  label,
  className = '',
}: SelectProps) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = options.find(o => o.id === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className={`relative ${className}`} ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        className={`inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-[0.8125rem] font-medium transition-colors ${
          open ? 'bg-surface-3 text-ink' : 'text-ink-2 hover:bg-surface-2 hover:text-ink'
        }`}
      >
        <span className="truncate max-w-[9rem]">{current?.label}</span>
        <ChevronDown
          className={`w-3.5 h-3.5 shrink-0 text-ink-3 transition-transform duration-200 ${
            open ? 'rotate-180' : ''
          }`}
          strokeWidth={1.9}
        />
      </button>

      {open && (
        <div
          role="listbox"
          className={`absolute z-50 p-1 popover animate-popIn ${menuWidth} ${
            align === 'right' ? 'right-0' : 'left-0'
          } ${placement === 'top' ? 'bottom-full mb-2' : 'top-full mt-1.5'}`}
        >
          {options.map(option => {
            const selected = option.id === value;
            return (
              <button
                key={option.id}
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => {
                  onChange(option.id);
                  setOpen(false);
                }}
                className="w-full flex items-start gap-2.5 px-2.5 py-2 rounded-[10px] text-left hover:bg-surface-2 transition-colors"
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-[0.8125rem] font-medium text-ink truncate">
                    {option.label}
                  </span>
                  {option.description && (
                    <span className="block mt-0.5 text-[0.75rem] text-ink-3 leading-snug">
                      {option.description}
                    </span>
                  )}
                </span>
                {selected && <Check className="w-4 h-4 text-ink shrink-0 mt-0.5" strokeWidth={2} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
