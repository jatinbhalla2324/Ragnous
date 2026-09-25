import { useState } from 'react';
import { ArrowUpRight, Atom, Dna, FlaskConical, Sigma } from 'lucide-react';
import { useSendMessage } from '../../hooks/useSendMessage';

type SubjectKey = 'physics' | 'chemistry' | 'maths' | 'biology';

const SUBJECTS: {
  key: SubjectKey;
  label: string;
  icon: React.ElementType;
  starters: string[];
}[] = [
  {
    key: 'physics',
    label: 'Physics',
    icon: Atom,
    starters: [
      "Explain Newton's three laws with everyday examples",
      'Solve a Class 10 light-reflection numerical step by step',
      'Quiz me on Class 12 electric charges and fields',
    ],
  },
  {
    key: 'chemistry',
    label: 'Chemistry',
    icon: FlaskConical,
    starters: [
      "How does Le Chatelier's principle shift an equilibrium?",
      'Balance and explain a redox reaction from NCERT',
      'Summarise periodic trends in five points',
    ],
  },
  {
    key: 'maths',
    label: 'Maths',
    icon: Sigma,
    starters: [
      'Derive the quadratic formula step by step',
      'Walk me through integration by parts with an example',
      'Give me three practice questions on trigonometric identities',
    ],
  },
  {
    key: 'biology',
    label: 'Biology',
    icon: Dna,
    starters: [
      'Explain the structure of a eukaryotic cell',
      'Compare mitosis and meiosis in a table',
      'Summarise NCERT human respiration in five steps',
    ],
  },
];

/**
 * The opening screen. A greeting, the composer at eye level, and a short set
 * of ways in — picking a subject swaps the suggestions rather than piling
 * every prompt on screen at once.
 */
export const EmptyState = ({ composer }: { composer: React.ReactNode }) => {
  const [subject, setSubject] = useState<SubjectKey>('physics');
  const send = useSendMessage();
  const active = SUBJECTS.find(s => s.key === subject)!;

  return (
    <div className="h-full overflow-y-auto">
      <div className="min-h-full flex flex-col items-center justify-center px-4 sm:px-6 py-10">
        <div className="w-full max-w-3xl rise-in">
          <div className="text-center mb-7">
            <h1 className="text-[1.75rem] sm:text-[2.125rem] font-semibold tracking-[-0.02em] text-ink">
              What are we learning today?
            </h1>
            <p className="mt-2 text-[0.9375rem] text-ink-3">
              NCERT-aligned tutoring across Physics, Chemistry, Maths and Biology.
            </p>
          </div>

          {composer}

          <div className="mt-7 flex flex-wrap justify-center gap-2">
            {SUBJECTS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setSubject(key)}
                aria-pressed={subject === key}
                className={`chip ${subject === key ? 'chip-active' : ''}`}
              >
                <Icon className="w-4 h-4" strokeWidth={1.8} />
                {label}
              </button>
            ))}
          </div>

          <div className="mt-5 mx-auto w-full max-w-xl card overflow-hidden">
            {active.starters.map((starter, i) => (
              <button
                key={starter}
                onClick={() => void send(starter)}
                className={`group w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-surface-2 transition-colors ${
                  i > 0 ? 'border-t border-line' : ''
                }`}
              >
                <span className="text-[0.8125rem] text-ink-2 group-hover:text-ink transition-colors">
                  {starter}
                </span>
                <ArrowUpRight
                  className="w-4 h-4 text-ink-4 group-hover:text-ink shrink-0 transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
                  strokeWidth={1.8}
                />
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
