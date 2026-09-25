import { useCallback } from 'react';
import { useChatStore } from '../store/chatStore';
import { useProfileStore } from '../store/profileStore';
import { isQuizRequest, useQuizStore } from '../store/quizStore';
import { mockChatResponse } from '../api/chat';
import { Attachment } from '../types/rag';

/**
 * Trim conversational scaffolding off the front so a title is what the chat
 * is about, not how it was requested. "Give me a 3D model of plant
 * respiration" -> "3D model of plant respiration"; "Explain photosynthesis"
 * -> "Photosynthesis". Kept alongside a hard cap on length so long questions
 * do not blow out the sidebar row.
 */
/**
 * Openers that carry no subject matter. Peeled off the front so the title is
 * what the chat is ABOUT, not how the student asked. Order matters: longer
 * multi-word phrases before single verbs, and single verbs are only stripped
 * when they open a bare command ("give me X"), not when they lead a question.
 */
const TITLE_LEAD_PATTERNS: RegExp[] = [
  /^(?:please|kindly|pls|plz|pleez|hey|hi|hello|yo)\s+/i,
  /^(?:can|could|would|will)\s+(?:you|u)\s+/i,
  /^(?:ok(?:ay)?|now|next|then|so|also|actually|alright)\s+/i,
  /^(?:umm+|hmm+|uh+)\s+/i,
  /^(?:give|show|tell|teach|explain|help|send|share)\s+(?:me\s+)?/i,
  /^(?:please\s+)?(?:bta(?:o|do)?|batao|samjhao|dedo|dijiye|karo|kar\s+do|de\s+do)\s+/i,
];

const cleanForTitle = (raw: string): string => {
  let t = (raw || '').replace(/\s+/g, ' ').trim();

  // Peel repeatedly — "please could you give me…" chains several openers.
  let peeled = true;
  let guard = 6;
  while (peeled && guard-- > 0) {
    peeled = false;
    for (const re of TITLE_LEAD_PATTERNS) {
      const stripped = t.replace(re, '');
      if (stripped !== t) {
        t = stripped;
        peeled = true;
      }
    }
  }

  // Stop after the first sentence — "What is photosynthesis? Also, tell me…"
  // should not become the whole run-on.
  t = t.split(/[?.!\n]/)[0].trim();

  const MAX_CHARS = 42;
  if (t.length > MAX_CHARS) t = t.slice(0, MAX_CHARS).replace(/\s+\S*$/, '') + '…';

  // Sentence case — "cell structure" reads sloppier than "Cell structure"
  // in the sidebar; existing uppercase is preserved.
  if (t && t[0] === t[0].toLowerCase()) {
    t = t[0].toUpperCase() + t.slice(1);
  }
  return t;
};

const deriveTitle = (question: string, attachments: Attachment[]): string => {
  const cleaned = cleanForTitle(question);
  if (cleaned) return cleaned;
  const firstFile = attachments[0]?.name;
  if (firstFile) {
    // "photosynthesis-notes.pdf" -> "Photosynthesis notes"
    const base = firstFile.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim();
    return cleanForTitle(base) || firstFile;
  }
  return 'New Chat';
};

/** A chat should get its real name from the first thing the student says —
 *  whether that first turn is a normal question, an attach-only doc, or even
 *  "give me a quiz" that never reaches the answering LLM. */
const isPlaceholderTitle = (title: string | undefined): boolean =>
  !title || title.trim() === '' || title.trim().toLowerCase() === 'new chat';

/**
 * The single path a question takes from the UI to the tutor.
 *
 * The composer and the starter prompts both need identical behaviour —
 * append the turn, carry the last few messages as history, remember which
 * topics already showed exam history, and name the chat after the first
 * question — so it lives in one place rather than being copied per caller.
 *
 * A message that reads like "give me a quiz / test me / mcq please" is short-
 * circuited here: it opens the MCQ panel instead of being sent to the chat
 * LLM, which would otherwise answer with plain-text open questions and never
 * offer the four-option interface the student actually wanted.
 */
export const useSendMessage = () => {
  const store = useChatStore;

  return useCallback(async (text: string, attachments: Attachment[] = []) => {
    const question = text.trim();
    if (!question && attachments.length === 0) return;

    const state = store.getState();
    if (state.isStreaming) return;

    const session = state.activeSession();
    const previous = session?.messages ?? [];
    // Pin the turn to the session it started in. If the user switches chats or
    // opens a new one while the reply is in flight, both the user turn and the
    // assistant reply still land on the conversation that asked the question.
    const targetSessionId = state.activeSessionId;

    const userMessage = {
      id: Math.random().toString(),
      role: 'user' as const,
      content: question,
      ...(attachments.length > 0 ? { attachments } : {}),
      createdAt: new Date().toISOString(),
    };
    state.addMessage(userMessage, targetSessionId);

    // ── Name the chat as EARLY as possible ─────────────────────────────
    // Runs before the quiz short-circuit so a session whose first turn is
    // "give me a quiz" still gets a real title from THAT turn's content,
    // rather than staying "New Chat" for the whole conversation. Also
    // catches sessions that somehow slipped through renamed as "New Chat"
    // in an earlier turn (e.g. a first turn that was a bare "hi").
    if (isPlaceholderTitle(session?.title) && !isQuizRequest(question)) {
      const title = deriveTitle(question, attachments);
      if (title && title !== 'New Chat') {
        store.getState().renameSession(targetSessionId, title);
      }
    }

    // ── Quiz short-circuit ─────────────────────────────────────────────
    // "give me quiz", "quiz me", "mcq please", "test karo" — all of these
    // should open the MCQ panel, not go to the answering LLM. Needs enough
    // chat context to have a topic; otherwise fall through so the assistant
    // can explain that it needs to learn what to quiz on first.
    if (question && isQuizRequest(question) && previous.length >= 2 && attachments.length === 0) {
      // The whole session (capped) so a mixed-topic chat quizzes on every
      // topic covered, not just the last exchange.
      const history = [...previous, userMessage].slice(-32).map(m => ({
        role: m.role,
        content: m.content,
      }));

      const profile = useProfileStore.getState().profile;
      const languageLabel =
        profile.languagePreference === 'hinglish'
          ? 'Hinglish'
          : profile.languagePreference === 'hindi'
          ? 'Hindi'
          : 'English';

      // A visible assistant note so the transcript records what happened —
      // otherwise the modal appearing over an empty spot is disorienting.
      state.addMessage(
        {
          id: Math.random().toString(),
          role: 'assistant',
          content:
            'Opening a quick MCQ quiz on what we\'ve been discussing — pick an option for each question. Correct answers earn 10 points.',
          createdAt: new Date().toISOString(),
          confidenceMode: 'ai_knowledge',
        },
        targetSessionId
      );

      void useQuizStore.getState().start({
        history,
        studentClass: session?.studentClass ?? profile.grade,
        language: session?.language ?? languageLabel,
      });
      return;
    }

    // Only this turn's files travel in full. Earlier turns keep a one-line
    // marker instead, so a follow-up ("now do question 3") still knows a file
    // was in play without re-uploading every image on every turn.
    const history = [...previous, userMessage]
      .slice(-6)
      .map(m => ({
        role: m.role,
        content:
          m.attachments?.length && m.id !== userMessage.id
            ? `${m.content}\n[Attached: ${m.attachments.map(a => a.name).join(', ')}]`.trim()
            : m.content,
      }));

    const pyqTopicsSeen = previous
      .map(m => m.pyq?.topic)
      .filter((t): t is string => Boolean(t));

    state.setStreaming(true);
    try {
      const response = await mockChatResponse(
        question,
        session?.language ?? 'English',
        session?.studentClass ?? '8th',
        session?.selectedModel ?? 'ragnous_x1',
        history,
        pyqTopicsSeen,
        attachments
      );
      store.getState().addMessage(response, targetSessionId);
    } finally {
      store.getState().setStreaming(false);
    }

    // Safety net — if the chat still has the placeholder title after this
    // turn (first message was a quiz request that skipped naming, or the
    // early rename produced only "New Chat"), name it from the first real
    // user message we have on record now.
    const stillPlaceholder = isPlaceholderTitle(
      store.getState().sessions.find(s => s.id === targetSessionId)?.title
    );
    if (stillPlaceholder) {
      const allMessages = store.getState().sessions.find(s => s.id === targetSessionId)?.messages ?? [];
      const firstUser = allMessages.find(m => m.role === 'user' && (m.content || m.attachments?.length));
      if (firstUser) {
        const title = deriveTitle(firstUser.content, firstUser.attachments ?? []);
        if (title && title !== 'New Chat') {
          store.getState().renameSession(targetSessionId, title);
        }
      }
    }
  }, [store]);
};
