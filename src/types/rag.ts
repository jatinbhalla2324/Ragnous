export type ConfidenceMode = "ncert_verified" | "extended_reference" | "ai_knowledge";

export interface Citation {
  chapter: string;
  page: number;
  source: "NCERT" | string;
}

export type PyqQuestionType = "objective" | "short" | "long";

export interface PyqQuestion {
  text: string;
  type: PyqQuestionType;
  /** Marks stated on the paper. null when the source did not say. */
  marks: number | null;
  /** Exam year stated for this question. null when the source did not say. */
  year: number | null;
}

/**
 * "board" for Class 10/12 — real CBSE board archives. "sample" for the
 * classes that never sit a board (8, 9, 11) — sample and practice papers,
 * NOT previous board years. The UI wording differs so the card cannot
 * imply a board year where none exists.
 */
export type PyqPaperType = "board" | "sample";

/** Previous exam / sample-paper appearances of a topic, from public sources. */
export interface PyqInsight {
  topic: string;
  paperType: PyqPaperType;
  questionCount: number;
  /** Distinct exam years found, newest first. Empty for sample-paper cards. */
  years: number[];
  yearCount: number;
  byType: Record<PyqQuestionType, number>;
  questions: PyqQuestion[];
  sources: { title: string; url: string }[];
}

/** A file the student attached to a turn, already read by the backend. */
export interface Attachment {
  name: string;
  mime: string;
  kind: "image" | "document";
  size: number;
  /** Text pulled out of the file. Empty for images and for scans. */
  text?: string;
  /**
   * Images always carry one; documents only when their text could not be read
   * and the file itself has to go to a vision model. Stripped before the
   * session is written to localStorage — see chatStore.
   */
  data_url?: string;
  page_count?: number | null;
  truncated?: boolean;
  note?: string;
}

/** A file the backend refused, with the reason to show the student. */
export interface RejectedAttachment {
  name: string;
  error: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  confidenceMode?: ConfidenceMode;
  confidenceScore?: number;
  citations?: Citation[];
  artifact?: import('./artifact').ArtifactPayload;
  pyq?: PyqInsight;
  attachments?: Attachment[];
  audioBriefingUrl?: string;
  isVoice?: boolean;
  createdAt: string;
}
