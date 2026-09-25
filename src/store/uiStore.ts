import { create } from 'zustand';

const SIDEBAR_KEY = 'ragnous_sidebar_open';

const loadSidebar = (): boolean => {
  try {
    const raw = localStorage.getItem(SIDEBAR_KEY);
    return raw === null ? true : raw === '1';
  } catch {
    return true;
  }
};

interface UiState {
  /** Desktop rail. Persisted so it survives navigation and reloads. */
  sidebarOpen: boolean;
  /** Mobile drawer. Always starts closed. */
  mobileNavOpen: boolean;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setMobileNavOpen: (open: boolean) => void;
}

export const useUiStore = create<UiState>((set) => ({
  sidebarOpen: loadSidebar(),
  mobileNavOpen: false,

  toggleSidebar: () =>
    set(state => {
      const next = !state.sidebarOpen;
      try { localStorage.setItem(SIDEBAR_KEY, next ? '1' : '0'); } catch {}
      return { sidebarOpen: next };
    }),

  setSidebarOpen: (open) =>
    set(() => {
      try { localStorage.setItem(SIDEBAR_KEY, open ? '1' : '0'); } catch {}
      return { sidebarOpen: open };
    }),

  setMobileNavOpen: (open) => set({ mobileNavOpen: open }),
}));
