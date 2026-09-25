import { create } from 'zustand';
import { ChatMessage } from '../types/rag';

export interface ChatSession {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: string;
  language: string;
  studentClass: string;
  selectedModel: string;
}

interface ChatState {
  sessions: ChatSession[];
  activeSessionId: string;
  isHomeworkMode: boolean;
  isStreaming: boolean;
  isVoiceActive: boolean;

  // Computed helpers
  activeSession: () => ChatSession | undefined;
  messages: () => ChatMessage[];

  // Session actions
  createSession: () => string;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;
  renameSession: (id: string, title: string) => void;

  // Message actions
  addMessage: (msg: ChatMessage, sessionId?: string) => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
  setHomeworkMode: (mode: boolean) => void;
  setStreaming: (streaming: boolean) => void;
  setVoiceActive: (active: boolean) => void;
  setLanguage: (lang: string) => void;
  setStudentClass: (cls: string) => void;
  setModel: (model: string) => void;
}

const STORAGE_KEY = 'ragnous_sessions';

const loadSessions = (): ChatSession[] => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
};

/**
 * Attachments carry the whole file with them — a base64 image or a PDF's
 * extracted text — which is exactly what localStorage's ~5 MB quota cannot
 * hold. One photo would blow it, and because the write below is wrapped in a
 * silent catch the symptom would be the entire chat history quietly failing to
 * persist. So the persisted copy keeps only what the chip needs to render;
 * the live session in memory keeps the full payload for the turn it is sent on.
 */
const lighten = (sessions: ChatSession[]): ChatSession[] =>
  sessions.map(s => ({
    ...s,
    messages: s.messages.map(m =>
      m.attachments?.length
        ? {
            ...m,
            attachments: m.attachments.map(({ name, mime, kind, size, page_count }) => ({
              name, mime, kind, size, page_count,
            })),
          }
        : m
    ),
  }));

const saveSessions = (sessions: ChatSession[]) => {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(lighten(sessions))); } catch {}
};

const makeSession = (language = 'English', studentClass = '8th', selectedModel = 'ragnous_x1'): ChatSession => ({
  id: crypto.randomUUID(),
  title: 'New Chat',
  messages: [],
  createdAt: new Date().toISOString(),
  language,
  studentClass,
  selectedModel,
});

// One session object, used for BOTH the initial list and activeSessionId.
// These used to be two separate makeSession() calls, so on a fresh browser the
// active id pointed at a session that was not in `sessions` — addMessage found
// nothing to append to and every message of the first chat vanished silently.
const stored = loadSessions();
const initialSessions = stored.length > 0 ? stored : [makeSession()];
if (stored.length === 0) saveSessions(initialSessions);
const firstSessionId = initialSessions[0].id;

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: initialSessions,
  activeSessionId: firstSessionId,
  isHomeworkMode: false,
  isStreaming: false,
  isVoiceActive: false,

  activeSession: () => get().sessions.find(s => s.id === get().activeSessionId),

  messages: () => get().activeSession()?.messages ?? [],

  createSession: () => {
    const current = get().activeSession();
    const s = makeSession(current?.language ?? 'English', current?.studentClass ?? '8th', current?.selectedModel ?? 'ragnous_x1');
    set(state => {
      const updated = [s, ...state.sessions];
      saveSessions(updated);
      return { sessions: updated, activeSessionId: s.id };
    });
    return s.id;
  },

  switchSession: (id) => set({ activeSessionId: id }),

  deleteSession: (id) => set(state => {
    const updated = state.sessions.filter(s => s.id !== id);
    if (updated.length === 0) {
      const fresh = makeSession();
      saveSessions([fresh]);
      return { sessions: [fresh], activeSessionId: fresh.id };
    }
    const newActive = state.activeSessionId === id ? updated[0].id : state.activeSessionId;
    saveSessions(updated);
    return { sessions: updated, activeSessionId: newActive };
  }),

  renameSession: (id, title) => set(state => {
    const updated = state.sessions.map(s => s.id === id ? { ...s, title } : s);
    saveSessions(updated);
    return { sessions: updated };
  }),

  addMessage: (msg, sessionId) => set(state => {
    // `sessionId` pins the write to the chat the turn started in — so a reply
    // that arrives after the user has switched sessions still lands on the
    // conversation that asked the question, not the one they're looking at.
    const targetId = sessionId ?? state.activeSessionId;
    const updated = state.sessions.map(s =>
      s.id === targetId
        ? { ...s, messages: [...s.messages, msg] }
        : s
    );
    saveSessions(updated);
    return { sessions: updated };
  }),

  updateMessage: (msgId, updates) => set(state => {
    const updated = state.sessions.map(s =>
      s.id === state.activeSessionId
        ? {
            ...s,
            messages: s.messages.map(m => m.id === msgId ? { ...m, ...updates } : m)
          }
        : s
    );
    saveSessions(updated);
    return { sessions: updated };
  }),

  setHomeworkMode: (mode) => set({ isHomeworkMode: mode }),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  setVoiceActive: (active) => set({ isVoiceActive: active }),

  setLanguage: (lang) => set(state => {
    const updated = state.sessions.map(s =>
      s.id === state.activeSessionId ? { ...s, language: lang } : s
    );
    saveSessions(updated);
    return { sessions: updated };
  }),

  setStudentClass: (cls) => set(state => {
    const updated = state.sessions.map(s =>
      s.id === state.activeSessionId ? { ...s, studentClass: cls } : s
    );
    saveSessions(updated);
    return { sessions: updated };
  }),

  setModel: (model) => set(state => {
    const updated = state.sessions.map(s =>
      s.id === state.activeSessionId ? { ...s, selectedModel: model } : s
    );
    saveSessions(updated);
    return { sessions: updated };
  }),
}));
