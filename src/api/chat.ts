import { Attachment, ChatMessage } from '../types/rag';
import { API_BASE } from './config';

export const mockChatResponse = async (
  query: string,
  language: string = 'English',
  studentClass: string = '8th',
  model: string = 'ragnous_x1',
  history: { role: string; content: string }[] = [],
  // Topics this session already showed an exam-history card for. The backend
  // holds no session state, so without this the card would come back on every
  // turn about the same chapter.
  pyqTopicsSeen: string[] = [],
  // Files attached to THIS turn, already read by /attachments. The backend
  // keeps nothing between requests, so the parsed text and image data have to
  // travel with the question.
  attachments: Attachment[] = []
): Promise<ChatMessage> => {
  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        language,
        student_class: studentClass,
        model,
        history,
        pyq_topics_seen: pyqTopicsSeen,
        attachments,
      })
    });
    
    if (!res.ok) {
      throw new Error(`API error: ${res.status}`);
    }
    
    const data = await res.json();
    return data as ChatMessage;
  } catch (error) {
    console.error("Failed to fetch from backend:", error);
    return {
      id: Math.random().toString(),
      role: 'assistant',
      content: "Sorry, I couldn't connect to the backend server. Is it running?",
      createdAt: new Date().toISOString(),
      confidenceMode: 'ai_knowledge'
    };
  }
};

export interface NotesOptions {
  studentClass: string;
  language: string;
  subjects: string[];
  /** Force a topic instead of letting the backend read one out of the chat. */
  topicHint?: string;
}

/**
 * Ask the backend to build study notes from this chat and download the PDF.
 *
 * The class travels with the request because the backend searches only the
 * NCERT books belonging to that class — notes for a Class 8 chat must never be
 * written out of the Class 10 chapter.
 */
export const exportChatToPdf = async (
  history: { role: string; content: string }[],
  options: NotesOptions
) => {
  const res = await fetch(`${API_BASE}/export/pdf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      history,
      student_class: options.studentClass,
      language: options.language,
      subjects: options.subjects,
      topic_hint: options.topicHint ?? null,
    })
  });

  if (!res.ok) {
    // The backend explains a refusal in a sentence meant for the student
    // ("this chat has no study topic yet"); pass it through rather than
    // replacing it with a status code.
    let detail = '';
    try {
      const body = await res.json();
      detail = typeof body?.detail === 'string' ? body.detail : '';
    } catch {
      /* not JSON — fall back to the status */
    }
    throw new Error(detail || `Could not build your notes (error ${res.status}).`);
  }

  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filenameFromResponse(res) || 'Study_Notes.pdf';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
};

/** The backend names the file after the topic, e.g. Reflex_Action.pdf. */
const filenameFromResponse = (res: Response): string | null => {
  const header = res.headers.get('Content-Disposition');
  const match = header?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  return match ? decodeURIComponent(match[1]) : null;
};
