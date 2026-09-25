import { useMemo, useState } from 'react';
import {
  ArrowRight,
  CheckCircle2,
  MessageSquare,
  RefreshCw,
  Sparkles,
  Target,
  Trophy,
  X,
  XCircle,
} from 'lucide-react';
import { QuizResponse } from '../../api/quiz';
import { useProfileStore } from '../../store/profileStore';
import { needsPractice, useProgressStore } from '../../store/progressStore';
import { useSendMessage } from '../../hooks/useSendMessage';

interface QuizPanelProps {
  quiz: QuizResponse;
  onClose: () => void;
  /** Called when the student wants another quiz on the same topic. Optional —
   *  the parent decides whether re-quizzing is available (rate limits, etc.). */
  onRetake?: () => void;
}

/**
 * The quiz overlay. One card, one question at a time. Points only land in the
 * profile once the whole set is finished, so a student cannot farm the same
 * quiz for points by opening and closing it.
 */
export const QuizPanel = ({ quiz, onClose, onRetake }: QuizPanelProps) => {
  const addPoints = useProfileStore(state => state.addPoints);
  const points = useProfileStore(state => state.profile.points);
  const recordAttempt = useProgressStore(state => state.recordAttempt);
  const attempts = useProgressStore(state => state.attempts);
  const send = useSendMessage();

  const [index, setIndex] = useState(0);
  const [picked, setPicked] = useState<Record<number, number>>({});
  const [revealed, setRevealed] = useState<Record<number, boolean>>({});
  const [finished, setFinished] = useState(false);
  const [awarded, setAwarded] = useState(false);

  const q = quiz.questions[index];
  const total = quiz.questions.length;

  const correctCount = useMemo(
    () =>
      quiz.questions.reduce(
        (n, item, i) => n + (picked[i] === item.answer_index ? 1 : 0),
        0
      ),
    [picked, quiz.questions]
  );

  const wrongItems = useMemo(
    () =>
      quiz.questions
        .map((item, i) => ({ item, i, chosen: picked[i] }))
        .filter(({ item, chosen }) => chosen !== undefined && chosen !== item.answer_index),
    [picked, quiz.questions]
  );

  const skippedCount = useMemo(
    () => quiz.questions.filter((_, i) => picked[i] === undefined).length,
    [picked, quiz.questions]
  );

  // The persistent "you keep missing these" list — includes prior chats, so it
  // stays useful even if the current quiz was 5/5.
  const chronicWeak = useMemo(
    () => needsPractice(attempts, 3).slice(0, 3),
    [attempts]
  );

  const pick = (optIdx: number) => {
    if (revealed[index]) return;
    setPicked(prev => ({ ...prev, [index]: optIdx }));
    setRevealed(prev => ({ ...prev, [index]: true }));
    // Per-question topic tag beats the combined display topic — a mixed quiz
    // records each answer against the concept it really tested.
    recordAttempt({
      topic: q.topic || quiz.topic,
      subjectArea: quiz.subject_area,
      correct: optIdx === q.answer_index,
      isMini: false,
      source: q.source,
    });
  };

  const next = () => {
    if (index < total - 1) {
      setIndex(i => i + 1);
      return;
    }
    if (!awarded) {
      addPoints(correctCount * quiz.points_per_correct);
      setAwarded(true);
    }
    setFinished(true);
  };

  const percent = total > 0 ? Math.round((correctCount / total) * 100) : 0;

  const verdict = (pct: number): { label: string; hint: string } => {
    if (pct >= 90) return { label: 'Excellent', hint: 'You have this topic locked in.' };
    if (pct >= 70) return { label: 'Solid', hint: 'A quick review of the misses and you\'re there.' };
    if (pct >= 50) return { label: 'Getting there', hint: 'The core is right — details need a second pass.' };
    if (pct >= 30) return { label: 'Needs work', hint: 'Re-read the chapter before the next quiz.' };
    return { label: 'Start over', hint: 'Best to re-learn this from scratch — take it slow.' };
  };

  /**
   * Ask the tutor to explain what the student got wrong. Includes the exact
   * question and the correct answer so the reply teaches the concept, not the
   * answer key.
   */
  const askTutor = (questionText: string, correctAnswer: string) => {
    void send(
      `In the last quiz on ${quiz.topic}, I got this wrong:\n\n"${questionText}"\n\n` +
      `The correct answer was: ${correctAnswer}.\n\n` +
      `Explain the concept behind this in simple language so I understand why, ` +
      `and give me one memory trick to hold onto it.`
    );
    onClose();
  };

  const retake = () => {
    if (!onRetake) return;
    onRetake();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-canvas/85 backdrop-blur-sm animate-fadeIn px-4 py-6 overflow-y-auto">
      <div className="relative w-full max-w-xl rounded-2xl border border-line-strong bg-canvas shadow-pop overflow-hidden my-auto">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-line">
          <div className="flex items-center gap-2 min-w-0">
            <Sparkles className="w-4 h-4 text-ink" strokeWidth={1.9} />
            <div className="min-w-0">
              <p className="text-[0.8125rem] font-medium text-ink truncate">
                Quiz · {quiz.topic}
              </p>
              <p className="text-[0.6875rem] text-ink-4 truncate">
                {quiz.subject_area || 'NCERT topic'} · 10 pts per correct answer
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="icon-btn shrink-0"
            title="Close quiz"
            aria-label="Close quiz"
          >
            <X className="w-4 h-4" strokeWidth={1.8} />
          </button>
        </div>

        {!finished ? (
          <div className="p-5">
            <div className="flex items-center justify-between mb-4">
              <p className="text-[0.75rem] text-ink-3">
                Question {index + 1} of {total}
              </p>
              <p className="text-[0.75rem] text-ink-4 tabular-nums">
                {points} pts · session best
              </p>
            </div>

            <div className="flex gap-1 mb-5">
              {quiz.questions.map((_, i) => (
                <span
                  key={i}
                  className={`h-1 flex-1 rounded-full ${
                    i <= index ? 'bg-ink' : 'bg-surface-3'
                  }`}
                />
              ))}
            </div>

            {q.topic && quiz.topics && quiz.topics.length > 1 && (
              <p className="mb-1.5 text-[0.6875rem] uppercase tracking-wider text-ink-4 font-medium">
                on {q.topic}
              </p>
            )}
            <h3 className="text-[0.9375rem] font-medium text-ink leading-relaxed mb-4">
              {q.question}
              {q.source === 'pyq' && (
                <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[0.6875rem] font-normal text-ink-3 align-middle">
                  PYQ
                </span>
              )}
            </h3>

            <div className="space-y-2">
              {q.options.map((opt, i) => {
                const isPicked = picked[index] === i;
                const isCorrect = i === q.answer_index;
                const show = revealed[index];
                const state =
                  show && isCorrect
                    ? 'correct'
                    : show && isPicked && !isCorrect
                    ? 'wrong'
                    : isPicked
                    ? 'picked'
                    : 'idle';

                return (
                  <button
                    key={i}
                    onClick={() => pick(i)}
                    disabled={show}
                    className={`w-full flex items-start gap-3 text-left px-4 py-3 rounded-xl border transition-colors text-[0.875rem] ${
                      state === 'correct'
                        ? 'border-ink bg-surface-2 text-ink'
                        : state === 'wrong'
                        ? 'border-line-strong bg-surface-2 text-ink'
                        : 'border-line hover:border-line-strong hover:bg-surface-2 text-ink-2'
                    } ${show ? 'cursor-default' : 'cursor-pointer'}`}
                  >
                    <span className="grid place-items-center w-6 h-6 rounded-full border border-line-strong text-[0.75rem] font-medium shrink-0">
                      {String.fromCharCode(65 + i)}
                    </span>
                    <span className="flex-1 leading-relaxed">{opt}</span>
                    {show && isCorrect && (
                      <CheckCircle2 className="w-4 h-4 text-ink shrink-0" strokeWidth={1.9} />
                    )}
                    {show && isPicked && !isCorrect && (
                      <XCircle className="w-4 h-4 text-ink shrink-0" strokeWidth={1.9} />
                    )}
                  </button>
                );
              })}
            </div>

            {revealed[index] && (
              <div className="mt-4 rounded-xl border border-line bg-surface-2 px-4 py-3 text-[0.8125rem] text-ink-2 leading-relaxed">
                {picked[index] === q.answer_index ? (
                  <>
                    <span className="font-medium text-ink">Correct.</span> {q.explanation}
                  </>
                ) : (
                  <>
                    <span className="font-medium text-ink">Not quite.</span>{' '}
                    Correct answer:{' '}
                    <span className="font-medium">
                      {String.fromCharCode(65 + q.answer_index)}. {q.options[q.answer_index]}
                    </span>
                    . {q.explanation}
                  </>
                )}
              </div>
            )}

            <div className="mt-5 flex items-center justify-between">
              <p className="text-[0.75rem] text-ink-4">
                {correctCount} correct so far
              </p>
              <button
                onClick={next}
                disabled={!revealed[index]}
                className="btn btn-primary"
              >
                {index < total - 1 ? 'Next question' : 'See results'}
              </button>
            </div>
          </div>
        ) : (
          <ResultsView
            quiz={quiz}
            correctCount={correctCount}
            total={total}
            percent={percent}
            skippedCount={skippedCount}
            wrongItems={wrongItems}
            chronicWeak={chronicWeak}
            verdictInfo={verdict(percent)}
            pointsBalance={points}
            onClose={onClose}
            onRetake={onRetake ? retake : undefined}
            onAskTutor={askTutor}
          />
        )}
      </div>
    </div>
  );
};

// ── The full results / analysis view ──────────────────────────────────────

interface ResultsProps {
  quiz: QuizResponse;
  correctCount: number;
  total: number;
  percent: number;
  skippedCount: number;
  wrongItems: {
    item: QuizResponse['questions'][number];
    i: number;
    chosen: number | undefined;
  }[];
  chronicWeak: ReturnType<typeof needsPractice>;
  verdictInfo: { label: string; hint: string };
  pointsBalance: number;
  onClose: () => void;
  onRetake?: () => void;
  onAskTutor: (questionText: string, correctAnswer: string) => void;
}

const ResultsView = ({
  quiz,
  correctCount,
  total,
  percent,
  skippedCount,
  wrongItems,
  chronicWeak,
  verdictInfo,
  pointsBalance,
  onClose,
  onRetake,
  onAskTutor,
}: ResultsProps) => {
  const earned = correctCount * quiz.points_per_correct;

  // Focus recommendations — first the concrete misses from THIS quiz, then any
  // chronically weak topics that keep showing up across sessions.
  const focusFromQuiz = wrongItems
    .map(({ item }) => shortConcept(item.question))
    .filter(Boolean);
  const focusFromHistory = chronicWeak
    .map(t => `${t.topic} — ${t.mastery}% mastery over ${t.attempts} tries`)
    .filter(t => t.toLowerCase() !== `${quiz.topic.toLowerCase()} — ${percent}% mastery`);

  return (
    <div className="p-6">
      {/* Score hero */}
      <div className="text-center mb-6">
        <div className="mx-auto mb-4">
          <ScoreRing percent={percent} />
        </div>
        <p className="text-[0.75rem] uppercase tracking-wider text-ink-4 font-medium">
          {verdictInfo.label}
        </p>
        <h3 className="text-[1.375rem] font-semibold text-ink mt-1">
          {correctCount} / {total} correct
        </h3>
        <p className="text-[0.8125rem] text-ink-3 mt-1 max-w-sm mx-auto">
          {verdictInfo.hint}
        </p>
      </div>

      {/* Points + skip summary strip */}
      <div className="grid grid-cols-3 gap-2 mb-6">
        <MetricPill icon={Trophy} label="Earned" value={`+${earned} pts`} />
        <MetricPill
          icon={CheckCircle2}
          label="Accuracy"
          value={`${percent}%`}
        />
        <MetricPill
          icon={Target}
          label={skippedCount > 0 ? 'Skipped' : 'Balance'}
          value={skippedCount > 0 ? `${skippedCount}` : `${pointsBalance} pts`}
        />
      </div>

      {/* Per-question review — every wrong answer laid out in full */}
      {wrongItems.length > 0 && (
        <section className="mb-6">
          <h4 className="text-[0.875rem] font-semibold text-ink mb-1">
            Review your misses
          </h4>
          <p className="text-[0.75rem] text-ink-4 mb-3">
            {wrongItems.length} question{wrongItems.length === 1 ? '' : 's'} to
            re-look. Tap "Ask tutor" and the concept opens in a fresh chat turn.
          </p>

          <div className="space-y-3">
            {wrongItems.map(({ item, i, chosen }) => (
              <article
                key={i}
                className="rounded-xl border border-line bg-surface-2 p-3.5"
              >
                <p className="text-[0.75rem] text-ink-4 mb-1">Question {i + 1}</p>
                <p className="text-[0.8125rem] font-medium text-ink leading-relaxed mb-3">
                  {item.question}
                </p>

                <div className="space-y-1.5 mb-3">
                  <AnswerLine
                    label="Your answer"
                    letter={chosen !== undefined ? String.fromCharCode(65 + chosen) : '—'}
                    text={chosen !== undefined ? item.options[chosen] : 'Not answered'}
                    tone="wrong"
                  />
                  <AnswerLine
                    label="Correct answer"
                    letter={String.fromCharCode(65 + item.answer_index)}
                    text={item.options[item.answer_index]}
                    tone="correct"
                  />
                </div>

                {item.explanation && (
                  <p className="text-[0.75rem] text-ink-2 leading-relaxed border-t border-line pt-2.5">
                    <span className="font-medium text-ink">Why:</span> {item.explanation}
                  </p>
                )}

                <div className="mt-3 flex justify-end">
                  <button
                    onClick={() =>
                      onAskTutor(item.question, item.options[item.answer_index])
                    }
                    className="btn btn-quiet h-7 px-2 text-[0.75rem]"
                  >
                    <MessageSquare className="w-3.5 h-3.5" strokeWidth={1.8} />
                    Ask tutor to explain
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {/* Focus recommendations */}
      {(focusFromQuiz.length > 0 || focusFromHistory.length > 0) && (
        <section className="mb-6">
          <h4 className="text-[0.875rem] font-semibold text-ink mb-1">
            Focus next on
          </h4>
          <p className="text-[0.75rem] text-ink-4 mb-3">
            Concrete concepts to revisit — the first list is from this quiz, the
            second is what your Progress tab has been flagging.
          </p>

          {focusFromQuiz.length > 0 && (
            <ul className="space-y-1.5 mb-3">
              {focusFromQuiz.map((c, idx) => (
                <li
                  key={`q-${idx}`}
                  className="flex items-start gap-2 text-[0.8125rem] text-ink-2 leading-relaxed"
                >
                  <span className="mt-1.5 w-1 h-1 rounded-full bg-ink shrink-0" />
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          )}

          {focusFromHistory.length > 0 && (
            <div className="rounded-lg border border-line bg-surface-2 px-3 py-2.5">
              <p className="text-[0.6875rem] uppercase tracking-wider text-ink-4 font-medium mb-1.5">
                Chronic weak spots
              </p>
              <ul className="space-y-1">
                {focusFromHistory.map((c, idx) => (
                  <li key={`h-${idx}`} className="text-[0.75rem] text-ink-2">
                    · {c}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {/* Nothing wrong — praise + a nudge */}
      {wrongItems.length === 0 && (
        <div className="rounded-xl border border-line bg-surface-2 px-4 py-4 text-center mb-6">
          <p className="text-[0.875rem] font-medium text-ink mb-1">Clean sweep.</p>
          <p className="text-[0.75rem] text-ink-3">
            You had every question on {quiz.topic}. Try a fresh topic next, or
            retake for a harder set.
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {onRetake && (
          <button onClick={onRetake} className="btn btn-secondary">
            <RefreshCw className="w-3.5 h-3.5" strokeWidth={1.8} />
            New quiz on this topic
          </button>
        )}
        <button onClick={onClose} className="btn btn-primary">
          Back to chat
          <ArrowRight className="w-3.5 h-3.5" strokeWidth={1.8} />
        </button>
      </div>
    </div>
  );
};

// ── Small presentational helpers ──────────────────────────────────────────

const ScoreRing = ({ percent }: { percent: number }) => {
  const r = 34;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - Math.max(0, Math.min(100, percent)) / 100);
  return (
    <svg width="88" height="88" viewBox="0 0 88 88" className="mx-auto">
      <circle
        cx="44"
        cy="44"
        r={r}
        stroke="#EFEFEF"
        strokeWidth="6"
        fill="none"
      />
      <circle
        cx="44"
        cy="44"
        r={r}
        stroke="#0D0D0D"
        strokeWidth="6"
        strokeLinecap="round"
        fill="none"
        strokeDasharray={c}
        strokeDashoffset={offset}
        transform="rotate(-90 44 44)"
      />
      <text
        x="44"
        y="49"
        textAnchor="middle"
        fontSize="20"
        fontWeight="600"
        fill="#0D0D0D"
      >
        {percent}%
      </text>
    </svg>
  );
};

const MetricPill = ({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
}) => (
  <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
    <div className="flex items-center gap-1.5 text-ink-3 text-[0.6875rem]">
      <Icon className="w-3 h-3" strokeWidth={1.9} />
      {label}
    </div>
    <p className="mt-0.5 text-[0.9375rem] font-semibold text-ink tabular-nums">
      {value}
    </p>
  </div>
);

const AnswerLine = ({
  label,
  letter,
  text,
  tone,
}: {
  label: string;
  letter: string;
  text: string;
  tone: 'correct' | 'wrong';
}) => (
  <div className="flex items-start gap-2 text-[0.75rem]">
    <span className="text-ink-4 w-24 shrink-0">{label}</span>
    <span className="flex items-start gap-1.5 min-w-0 flex-1">
      {tone === 'correct' ? (
        <CheckCircle2 className="w-3.5 h-3.5 text-ink mt-0.5 shrink-0" strokeWidth={1.9} />
      ) : (
        <XCircle className="w-3.5 h-3.5 text-ink mt-0.5 shrink-0" strokeWidth={1.9} />
      )}
      <span className="text-ink-2 leading-snug">
        <span className="font-medium text-ink">{letter}.</span> {text}
      </span>
    </span>
  </div>
);

/**
 * Turn a full MCQ stem into a short focus concept. Strips the "what is / which
 * of the following" scaffolding so the list reads like study bullets rather
 * than a wall of restated questions.
 */
const shortConcept = (questionText: string): string => {
  let t = (questionText || '').trim();
  t = t.replace(/[?.]+$/g, '');
  t = t.replace(
    /^(which of the following (best )?(describes|is|are|explains|represents|shows|means|refers to)|which one (best )?(describes|is|explains)|what (is|are|does|do)|why (does|do|is|are)|how (does|do|is|are|many)|when (does|do|is|are)|where (does|do|is|are)|choose the (correct|best)|identify the|name the|state the|define|explain)\s+/i,
    ''
  );
  t = t.replace(/^(the )/i, '');
  const trimmed = t.length > 100 ? t.slice(0, 97) + '…' : t;
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
};
