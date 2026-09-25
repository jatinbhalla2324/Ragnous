import { create } from 'zustand';
import { generateQuiz, QuizQuestion, QuizResponse } from '../api/quiz';

/**
 * Quiz state, kept in its own store so it can be triggered from anywhere —
 * the header button, or a "give me a quiz" typed straight into the composer —
 * and rendered from the TutorPage without lifting state through props.
 *
 * Two flavours share the store:
 *   * `quiz`     — the full 5-question MCQ modal.
 *   * `miniQuiz` — a single "surprise check" popup that appears in the middle
 *                  of a chat to keep the student honest between long answers.
 */

interface MiniQuiz {
  question: QuizQuestion;
  topic: string;
  subjectArea: string;
  pointsPerCorrect: number;
}

interface QuizState {
  quiz: QuizResponse | null;
  loading: boolean;
  error: string | null;

  miniQuiz: MiniQuiz | null;
  miniLoading: boolean;
  /** How many assistant turns have gone by since any quiz was offered. Used to
   *  keep the popup rare — students hate being interrupted every message. */
  turnsSinceQuiz: number;

  start: (params: {
    history: { role: string; content: string }[];
    studentClass: string;
    language: string;
    topicHint?: string;
  }) => Promise<void>;

  startMini: (params: {
    history: { role: string; content: string }[];
    studentClass: string;
    language: string;
  }) => Promise<void>;

  close: () => void;
  closeMini: () => void;
  clearError: () => void;
  noteAssistantTurn: () => void;
}

export const useQuizStore = create<QuizState>((set, get) => ({
  quiz: null,
  loading: false,
  error: null,
  miniQuiz: null,
  miniLoading: false,
  turnsSinceQuiz: 999,

  start: async ({ history, studentClass, language, topicHint }) => {
    if (get().loading) return;
    set({ loading: true, error: null });
    try {
      const data = await generateQuiz(history, studentClass, language, topicHint);
      set({ quiz: data, loading: false, turnsSinceQuiz: 0 });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : 'Could not build a quiz right now.',
      });
      setTimeout(() => {
        if (get().error) set({ error: null });
      }, 5000);
    }
  },

  /**
   * Fetch a single MCQ using the same endpoint (which returns 5) and keep only
   * the first item. The backend cost is the same either way, but the UI shows
   * one question in a small non-modal card so it does not steal the whole
   * screen mid-conversation.
   */
  startMini: async ({ history, studentClass, language }) => {
    if (get().miniLoading || get().miniQuiz || get().quiz || get().loading) return;
    set({ miniLoading: true });
    try {
      const data = await generateQuiz(history, studentClass, language);
      const first = data.questions[0];
      if (first) {
        set({
          miniQuiz: {
            question: first,
            topic: data.topic,
            subjectArea: data.subject_area,
            pointsPerCorrect: data.points_per_correct,
          },
          miniLoading: false,
          turnsSinceQuiz: 0,
        });
      } else {
        set({ miniLoading: false });
      }
    } catch (e) {
      // A silent miss is fine — the popup is opportunistic, not requested.
      console.warn('[QUIZ] mini quiz skipped:', e);
      set({ miniLoading: false });
    }
  },

  close: () => set({ quiz: null }),
  closeMini: () => set({ miniQuiz: null }),
  clearError: () => set({ error: null }),
  noteAssistantTurn: () =>
    set(state => ({ turnsSinceQuiz: state.turnsSinceQuiz + 1 })),
}));

/**
 * Does this message read like "quiz me on this"? Kept intentionally broad —
 * students phrase it a dozen ways ("give quiz", "test me", "mcq please",
 * "quiz karo", "mujhe test do") — but pinned to intent words so a message
 * that merely mentions the word "quiz" in passing does not trigger it.
 */
const QUIZ_INTENT_RE =
  /\b(?:(?:give|start|take|do|run|make|generate|show|open|begin|launch|create|want|need|conduct|hold|host|throw|drop|share)\s+(?:me\s+)?(?:a\s+|the\s+|some\s+|an\s+)?(?:quick\s+|short\s+|small\s+|mini\s+)?(?:mcq(?:s)?|multiple[-\s]?choice|quiz|test|practice|questions?|mock\s*test|assessment)|(?:quiz|test|mcq(?:s)?)\s+me|test\s+my\s+knowledge|quiz\s+time|(?:quiz|test|mcq)\s*(?:karo|kar\s*do|de\s*do|chahiye|dedo|do)\b|mujhe\s+(?:quiz|test|mcq))/i;

export const isQuizRequest = (text: string): boolean => {
  const t = (text || '').trim();
  if (!t) return false;
  return QUIZ_INTENT_RE.test(t);
};

/**
 * The rules for surprising a student with a check-in question. Deliberately
 * conservative — one mid-chat popup that lands wrong is much worse than one
 * we skipped, so we wait for real substance and enforce a cooldown.
 *
 * The trigger climbs sharply the longer it has been since the last check-in,
 * so a chat that has covered several topics never goes 20 turns without a
 * single quiz slipping in.
 */
export const shouldOfferMiniQuiz = (opts: {
  turnsSinceQuiz: number;
  assistantTurns: number;
  lastAssistantContent: string;
}): boolean => {
  // At least 2 real assistant turns in the chat, and at least 3 turns since
  // the last quiz — a student who just finished one deserves breathing room.
  if (opts.assistantTurns < 2) return false;
  if (opts.turnsSinceQuiz < 3) return false;

  const content = (opts.lastAssistantContent || '').trim();
  // Small chit-chat replies (a two-sentence clarification) aren't quizzable.
  if (content.length < 160) return false;

  // Rising probability: 35% at the minimum cooldown, near-certain by turn 8.
  // Keeps early checks feeling like surprises while guaranteeing at least
  // one check per stretch of substantive chat.
  const bump = Math.min(0.6, (opts.turnsSinceQuiz - 3) * 0.1);
  const probability = 0.35 + bump;
  return Math.random() < probability;
};
