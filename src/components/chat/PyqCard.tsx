import { useState } from 'react';
import { ChevronDown, ExternalLink, History } from 'lucide-react';
import { PyqInsight, PyqQuestionType } from '../../types/rag';

/* ═══════════════════════════════════════════════════════════════
   Previous-year questions

   Shown once, on the turn a topic is first asked about: how often it
   has appeared in board papers, and the questions themselves grouped
   by the form they were asked in. Collapsed by default so it never
   buries the answer.
   ═══════════════════════════════════════════════════════════════ */

const TYPE_META: Record<PyqQuestionType, { label: string; tint: string }> = {
  objective: { label: 'Objective', tint: '#0D0D0D' },
  short: { label: 'Short answer', tint: '#5D5D5D' },
  long: { label: 'Long answer', tint: '#9A9A9A' },
};

const ORDER: PyqQuestionType[] = ['objective', 'short', 'long'];

/**
 * The headline sentence.
 *
 * Deliberately hedged where the data is thin. Years are only known when a
 * source printed them next to the question, so a card built from undated
 * question banks says how many questions were found and stops there rather
 * than implying a frequency it cannot support. Sample-paper cards (Class 8/9/
 * 11 — no board exam exists) never quote a year: they explicitly say what
 * they are, so a Class 8 student can never misread a scraped Class 10 tag as
 * "my exam".
 */
const summarise = (pyq: PyqInsight): string => {
  const { yearCount, years, questionCount, paperType } = pyq;
  const asked = `${questionCount} question${questionCount === 1 ? '' : 's'} found`;
  if (paperType === 'sample') {
    return `${asked} in CBSE sample & practice papers`;
  }
  if (yearCount >= 2) return `Asked across ${yearCount} board years (${years[years.length - 1]}–${years[0]})`;
  if (yearCount === 1) return `Asked in the ${years[0]} board paper`;
  return `${asked} in board question banks`;
};

const HEADLINE_LABEL: Record<PyqInsight['paperType'], string> = {
  board: 'In the board exams',
  sample: 'In sample papers',
};

const SOURCES_LABEL: Record<PyqInsight['paperType'], string> = {
  board: 'Collected from',
  sample: 'Collected from (sample / practice papers — not actual board archives)',
};

export const PyqCard = ({ pyq }: { pyq: PyqInsight }) => {
  const [open, setOpen] = useState(false);

  if (!pyq?.questions?.length) return null;

  const grouped = ORDER.map(type => ({
    type,
    items: pyq.questions.filter(q => q.type === type),
  })).filter(group => group.items.length > 0);

  return (
    <div className="mt-4 rounded-2xl border border-line overflow-hidden bg-canvas">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-surface-2 transition-colors"
        aria-expanded={open}
      >
        <span className="grid place-items-center w-7 h-7 rounded-lg bg-surface-2 shrink-0">
          <History className="w-3.5 h-3.5 text-ink-2" strokeWidth={1.9} />
        </span>

        <span className="min-w-0 flex-1">
          <span className="block text-[0.8125rem] font-semibold text-ink truncate">
            {HEADLINE_LABEL[pyq.paperType]} · {summarise(pyq)}
          </span>
          <span className="mt-0.5 flex items-center gap-3 flex-wrap text-[0.75rem] text-ink-3">
            {ORDER.filter(t => (pyq.byType?.[t] ?? 0) > 0).map(t => (
              <span key={t} className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: TYPE_META[t].tint }} />
                {pyq.byType[t]} {TYPE_META[t].label.toLowerCase()}
              </span>
            ))}
          </span>
        </span>

        <ChevronDown
          className={`w-4 h-4 text-ink-3 shrink-0 transition-transform duration-200 ${
            open ? 'rotate-180' : ''
          }`}
          strokeWidth={1.8}
        />
      </button>

      {open && (
        <div className="px-4 pb-4 pt-3 space-y-5 border-t border-line">
          {grouped.map(({ type, items }) => (
            <div key={type} className="space-y-2">
              <p
                className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em]"
                style={{ color: TYPE_META[type].tint }}
              >
                {TYPE_META[type].label}
              </p>

              {items.map((q, i) => (
                <div key={i} className="pl-3.5 relative">
                  <span
                    className="absolute left-0 top-[0.6em] w-1 h-1 rounded-full"
                    style={{ background: TYPE_META[type].tint, opacity: 0.6 }}
                  />
                  <p className="text-[0.8125rem] leading-relaxed text-ink-2">{q.text}</p>
                  {(q.year || q.marks) && (
                    <p className="mt-0.5 text-[0.6875rem] text-ink-4 tabular-nums">
                      {q.year ?? 'year not stated'}
                      {q.marks ? ` · ${q.marks} mark${q.marks === 1 ? '' : 's'}` : ''}
                    </p>
                  )}
                </div>
              ))}
            </div>
          ))}

          {/* Where the numbers came from — these are public question banks, not
              an official board dataset, and the student should be able to check
              them. */}
          {pyq.sources?.length > 0 && (
            <div className="pt-1 space-y-1.5">
              <p className="section-label">{SOURCES_LABEL[pyq.paperType]}</p>
              {pyq.sources.map(source => (
                <a
                  key={source.url}
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-[0.75rem] text-ink-3 hover:text-ink transition-colors"
                >
                  <ExternalLink className="w-3 h-3 shrink-0" strokeWidth={1.8} />
                  <span className="truncate">{source.title}</span>
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
