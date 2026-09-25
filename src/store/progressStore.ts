import { create } from 'zustand';

/**
 * One recorded quiz attempt — either from the full modal or a mini popup.
 * Kept flat (no nesting) so the persisted blob is trivially inspectable in
 * DevTools and easy to migrate later.
 */
export interface QuizAttempt {
  id: string;
  topic: string;
  subjectArea: string;
  correct: boolean;
  isMini: boolean;
  source: 'generated' | 'pyq';
  /** ISO timestamp — used to bucket into days for trend/streak math. */
  ts: string;
}

export interface TopicStat {
  topic: string;
  subjectArea: string;
  attempts: number;
  correct: number;
  /** 0-100 accuracy over this topic's attempts. */
  mastery: number;
  lastAttemptAt: string;
}

export interface DayBucket {
  /** YYYY-MM-DD, in the viewer's local timezone. */
  day: string;
  /** Short label like "Mon" for the axis tick. */
  label: string;
  attempts: number;
  correct: number;
  /** 0-100 accuracy for the day; null when the day had no attempts. */
  mastery: number | null;
}

const STORAGE_KEY = 'ragnous_quiz_attempts';
/**
 * Attempts pile up over the year but the Progress dashboard only reads the
 * last few weeks. Cap so localStorage never grows unbounded.
 */
const MAX_ATTEMPTS = 2000;

const load = (): QuizAttempt[] => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const save = (attempts: QuizAttempt[]) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(attempts.slice(-MAX_ATTEMPTS)));
  } catch { /* quota exceeded — drop the write silently */ }
};

interface ProgressState {
  attempts: QuizAttempt[];
  recordAttempt: (a: Omit<QuizAttempt, 'id' | 'ts'>) => void;
  clear: () => void;
}

export const useProgressStore = create<ProgressState>((set) => ({
  attempts: load(),

  recordAttempt: (a) =>
    set(state => {
      const entry: QuizAttempt = {
        ...a,
        id: crypto.randomUUID?.() ?? Math.random().toString(36).slice(2),
        ts: new Date().toISOString(),
      };
      const next = [...state.attempts, entry].slice(-MAX_ATTEMPTS);
      save(next);
      return { attempts: next };
    }),

  clear: () => {
    save([]);
    set({ attempts: [] });
  },
}));


// ── Derived helpers — pure functions on the attempts list ──────────────────

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

const dayKey = (d: Date): string => {
  // Local YYYY-MM-DD, not UTC, so "today" agrees with what the student sees.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

/** Per-topic aggregate. Topics with only a single attempt still show up —
 *  a student who has answered nothing on a topic yet gets an honest "no
 *  data" rather than a fake number. */
export const topicStats = (attempts: QuizAttempt[]): TopicStat[] => {
  const map = new Map<string, TopicStat>();
  for (const a of attempts) {
    const key = a.topic.trim().toLowerCase();
    if (!key) continue;
    const prev =
      map.get(key) ??
      ({
        topic: a.topic.trim(),
        subjectArea: a.subjectArea,
        attempts: 0,
        correct: 0,
        mastery: 0,
        lastAttemptAt: a.ts,
      } as TopicStat);
    prev.attempts += 1;
    prev.correct += a.correct ? 1 : 0;
    prev.lastAttemptAt =
      new Date(a.ts) > new Date(prev.lastAttemptAt) ? a.ts : prev.lastAttemptAt;
    if (a.subjectArea) prev.subjectArea = a.subjectArea;
    prev.mastery = Math.round((prev.correct / prev.attempts) * 100);
    map.set(key, prev);
  }
  return [...map.values()].sort((a, b) => b.attempts - a.attempts);
};

/** Seven daily buckets ending today, oldest first. Days with no attempts
 *  come back with `mastery: null` so the chart can skip them or render a
 *  gap instead of pretending the student scored zero. */
export const last7Days = (attempts: QuizAttempt[]): DayBucket[] => {
  const buckets: DayBucket[] = [];
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  for (let i = 6; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    buckets.push({
      day: dayKey(d),
      label: DAY_LABELS[d.getDay()],
      attempts: 0,
      correct: 0,
      mastery: null,
    });
  }

  const index = new Map(buckets.map(b => [b.day, b]));
  for (const a of attempts) {
    const key = dayKey(new Date(a.ts));
    const bucket = index.get(key);
    if (!bucket) continue;
    bucket.attempts += 1;
    if (a.correct) bucket.correct += 1;
  }
  for (const b of buckets) {
    b.mastery = b.attempts > 0 ? Math.round((b.correct / b.attempts) * 100) : null;
  }
  return buckets;
};

/** Percentage delta between this rolling week and the one before it. `null`
 *  when the previous week had nothing to compare against. */
export const weeklyGrowth = (attempts: QuizAttempt[]): number | null => {
  const now = Date.now();
  const wk = 7 * 24 * 60 * 60 * 1000;

  const inWindow = (start: number, end: number) =>
    attempts.filter(a => {
      const t = new Date(a.ts).getTime();
      return t >= start && t < end;
    });

  const thisWeek = inWindow(now - wk, now + 1);
  const lastWeek = inWindow(now - 2 * wk, now - wk);

  if (thisWeek.length === 0 || lastWeek.length === 0) return null;

  const rate = (arr: QuizAttempt[]) =>
    arr.filter(a => a.correct).length / arr.length;

  const prev = rate(lastWeek);
  if (prev === 0) return null;
  return Math.round(((rate(thisWeek) - prev) / prev) * 100);
};

/** Consecutive days ending today on which the student answered at least
 *  one question. Streak ends the moment a day is skipped. */
export const studyStreak = (attempts: QuizAttempt[]): number => {
  if (attempts.length === 0) return 0;
  const days = new Set(attempts.map(a => dayKey(new Date(a.ts))));

  let streak = 0;
  const cursor = new Date();
  cursor.setHours(0, 0, 0, 0);

  while (days.has(dayKey(cursor))) {
    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }
  return streak;
};

/** Weakest topic among those with at least `minAttempts` — a topic with a
 *  single wrong answer should not outrank one with a real track record. */
export const weakestTopic = (
  attempts: QuizAttempt[],
  minAttempts = 2
): TopicStat | null => {
  const eligible = topicStats(attempts).filter(t => t.attempts >= minAttempts);
  if (eligible.length === 0) return null;
  return eligible.slice().sort((a, b) => a.mastery - b.mastery)[0];
};

/** Topics ordered by weakest first, capped so the list stays scannable. */
export const needsPractice = (attempts: QuizAttempt[], limit = 5): TopicStat[] =>
  topicStats(attempts)
    .filter(t => t.attempts >= 2 && t.mastery < 80)
    .sort((a, b) => a.mastery - b.mastery)
    .slice(0, limit);

export const overallAccuracy = (attempts: QuizAttempt[]): number | null => {
  if (attempts.length === 0) return null;
  return Math.round(
    (attempts.filter(a => a.correct).length / attempts.length) * 100
  );
};
