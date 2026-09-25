import { useState } from 'react';
import { CheckCircle2, Sparkles, X, XCircle } from 'lucide-react';
import { QuizQuestion } from '../../api/quiz';
import { useProfileStore } from '../../store/profileStore';
import { useProgressStore } from '../../store/progressStore';

interface MiniQuizPopupProps {
  question: QuizQuestion;
  topic: string;
  subjectArea?: string;
  pointsPerCorrect: number;
  onDismiss: () => void;
}

/**
 * A small, non-modal check-in that slides up from the bottom-right while the
 * student is chatting. One MCQ, four options, awards points on a correct
 * answer. Kept compact on purpose — this is a surprise, not a takeover.
 */
export const MiniQuizPopup = ({
  question,
  topic,
  subjectArea = '',
  pointsPerCorrect,
  onDismiss,
}: MiniQuizPopupProps) => {
  const addPoints = useProfileStore(state => state.addPoints);
  const recordAttempt = useProgressStore(state => state.recordAttempt);

  const [picked, setPicked] = useState<number | null>(null);
  const [awarded, setAwarded] = useState(false);

  const revealed = picked !== null;
  const isCorrect = picked === question.answer_index;

  const pick = (idx: number) => {
    if (revealed) return;
    setPicked(idx);
    const correct = idx === question.answer_index;
    if (correct && !awarded) {
      addPoints(pointsPerCorrect);
      setAwarded(true);
    }
    // Prefer the question's own topic tag when the backend classified it
    // (mixed-topic chats need per-question attribution), else fall back to
    // the combined display topic.
    recordAttempt({
      topic: question.topic || topic,
      subjectArea,
      correct,
      isMini: true,
      source: question.source,
    });
  };

  return (
    <div className="fixed bottom-4 right-4 z-40 w-[22rem] max-w-[calc(100vw-2rem)] animate-fadeUp">
      <div className="rounded-2xl border border-line-strong bg-canvas shadow-pop overflow-hidden">
        <div className="flex items-start justify-between gap-2 px-4 pt-3 pb-2 border-b border-line">
          <div className="flex items-center gap-2 min-w-0">
            <span className="grid place-items-center w-6 h-6 rounded-full bg-surface-2 border border-line shrink-0">
              <Sparkles className="w-3.5 h-3.5 text-ink" strokeWidth={1.9} />
            </span>
            <div className="min-w-0">
              <p className="text-[0.75rem] font-medium text-ink leading-tight">
                Quick check
              </p>
              <p className="text-[0.6875rem] text-ink-4 truncate">
                on {question.topic || topic} · +{pointsPerCorrect} pts if correct
              </p>
            </div>
          </div>
          <button
            onClick={onDismiss}
            className="icon-btn -mr-1 -mt-1"
            title="Skip this check"
            aria-label="Skip this check"
          >
            <X className="w-3.5 h-3.5" strokeWidth={1.8} />
          </button>
        </div>

        <div className="p-4">
          <p className="text-[0.8125rem] font-medium text-ink leading-relaxed mb-3">
            {question.question}
            {question.source === 'pyq' && (
              <span className="ml-1.5 inline-flex items-center rounded-full border border-line bg-surface-2 px-1.5 py-0.5 text-[0.625rem] font-normal text-ink-3 align-middle">
                PYQ
              </span>
            )}
          </p>

          <div className="space-y-1.5">
            {question.options.map((opt, i) => {
              const isPicked = picked === i;
              const correctHere = i === question.answer_index;
              const state =
                revealed && correctHere
                  ? 'correct'
                  : revealed && isPicked && !correctHere
                  ? 'wrong'
                  : 'idle';
              return (
                <button
                  key={i}
                  onClick={() => pick(i)}
                  disabled={revealed}
                  className={`w-full flex items-start gap-2 text-left px-3 py-2 rounded-lg border text-[0.8125rem] transition-colors ${
                    state === 'correct'
                      ? 'border-ink bg-surface-2 text-ink'
                      : state === 'wrong'
                      ? 'border-line-strong bg-surface-2 text-ink'
                      : 'border-line hover:border-line-strong hover:bg-surface-2 text-ink-2'
                  } ${revealed ? 'cursor-default' : 'cursor-pointer'}`}
                >
                  <span className="grid place-items-center w-5 h-5 rounded-full border border-line-strong text-[0.6875rem] font-medium shrink-0">
                    {String.fromCharCode(65 + i)}
                  </span>
                  <span className="flex-1 leading-snug">{opt}</span>
                  {state === 'correct' && (
                    <CheckCircle2 className="w-3.5 h-3.5 text-ink shrink-0" strokeWidth={1.9} />
                  )}
                  {state === 'wrong' && (
                    <XCircle className="w-3.5 h-3.5 text-ink shrink-0" strokeWidth={1.9} />
                  )}
                </button>
              );
            })}
          </div>

          {revealed && (
            <div className="mt-3 rounded-lg border border-line bg-surface-2 px-3 py-2 text-[0.75rem] text-ink-2 leading-relaxed">
              {isCorrect ? (
                <>
                  <span className="font-medium text-ink">Correct — +{pointsPerCorrect} pts.</span>{' '}
                  {question.explanation}
                </>
              ) : (
                <>
                  <span className="font-medium text-ink">Answer:</span>{' '}
                  {String.fromCharCode(65 + question.answer_index)}.{' '}
                  {question.options[question.answer_index]}. {question.explanation}
                </>
              )}
              <div className="mt-2 flex justify-end">
                <button onClick={onDismiss} className="btn btn-quiet h-7 px-2 text-[0.75rem]">
                  Back to chat
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
