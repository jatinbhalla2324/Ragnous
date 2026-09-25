import { API_BASE } from './config';

/** One multiple-choice question the backend built for a chat topic. */
export interface QuizQuestion {
  question: string;
  options: string[];
  answer_index: number;
  explanation: string;
  source: 'generated' | 'pyq';
  /** Which of the chat's topics this question belongs to. Empty string when
   *  the backend could not classify — treat as the combined `topic`. */
  topic: string;
}

export interface QuizResponse {
  /** Combined display label — "Motion · Acids and bases" for multi-topic. */
  topic: string;
  /** The individual topics detected in the chat. Always ≥ 1 on success. */
  topics: string[];
  subject_area: string;
  questions: QuizQuestion[];
  points_per_correct: number;
  pyq_sources: { title?: string; url?: string }[];
}

interface QuizTurn { role: string; content: string }

/**
 * Ask the backend to read the chat, extract the topic, and generate a short
 * MCQ quiz on THAT topic — nothing else. The backend is stateless, so the
 * whole recent history has to travel with the call.
 */
export const generateQuiz = async (
  history: QuizTurn[],
  studentClass: string,
  language: string,
  topicHint?: string,
): Promise<QuizResponse> => {
  const res = await fetch(`${API_BASE}/quiz/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      history,
      student_class: studentClass,
      language,
      topic_hint: topicHint ?? null,
    }),
  });

  if (!res.ok) {
    let detail = `Quiz service error (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch { /* keep the generic error */ }
    throw new Error(detail);
  }

  return (await res.json()) as QuizResponse;
};
