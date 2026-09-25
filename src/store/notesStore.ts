import { create } from 'zustand';
import { exportChatToPdf } from '../api/chat';
import { useChatStore } from './chatStore';
import { useProfileStore } from './profileStore';

/**
 * "Save as notes" is offered in two places — the header button and the notes
 * artifact's own button — and both used to build the request themselves. They
 * drifted: the artifact's copy sent nothing but the transcript, so the backend
 * had no class to scope the NCERT search to. One action, one request shape.
 */

const languageIdToLabel = (id: string): string => {
  if (id === 'hinglish') return 'Hinglish';
  if (id === 'hindi') return 'Hindi';
  return 'English';
};

interface NotesState {
  exporting: boolean;
  /** Shown to the student verbatim — the backend explains refusals in words. */
  error: string | null;
  exportNotes: () => Promise<void>;
  clearError: () => void;
}

export const useNotesStore = create<NotesState>((set, get) => ({
  exporting: false,
  error: null,

  clearError: () => set({ error: null }),

  exportNotes: async () => {
    if (get().exporting) return;
    const messages = useChatStore.getState().messages();
    if (!messages.length) return;

    const { profile } = useProfileStore.getState();
    set({ exporting: true, error: null });
    try {
      await exportChatToPdf(
        messages.map(m => ({ role: m.role, content: m.content })),
        {
          studentClass: profile.grade,
          language: languageIdToLabel(profile.languagePreference),
          subjects: profile.subjects,
        }
      );
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Could not build your notes.';
      console.error('Failed to export notes:', e);
      set({ error: message });
    } finally {
      set({ exporting: false });
    }
  },
}));
