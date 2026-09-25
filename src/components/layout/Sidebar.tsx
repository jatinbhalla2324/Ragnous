import { useEffect, useRef, useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import {
  MessageSquare,
  BarChart3,
  Plus,
  Pencil,
  Trash2,
  PanelLeftClose,
  Search,
  X,
} from 'lucide-react';
import { useChatStore, ChatSession } from '../../store/chatStore';
import { useUiStore } from '../../store/uiStore';
import { Logo } from './Logo';

const PRIMARY_NAV = [
  { to: '/tutor', icon: MessageSquare, label: 'Tutor' },
  { to: '/progress', icon: BarChart3, label: 'Progress' },
];

/* ── History grouping ────────────────────────────────────────────
   Chats read far better under a date heading than as one long list,
   which is what the old sidebar showed.
   ─────────────────────────────────────────────────────────────── */
const DAY = 86_400_000;

const bucketOf = (iso: string): string => {
  const age = Date.now() - new Date(iso).getTime();
  if (age < DAY) return 'Today';
  if (age < 2 * DAY) return 'Yesterday';
  if (age < 7 * DAY) return 'Previous 7 days';
  if (age < 30 * DAY) return 'Previous 30 days';
  return 'Older';
};

const BUCKET_ORDER = ['Today', 'Yesterday', 'Previous 7 days', 'Previous 30 days', 'Older'];

const groupSessions = (sessions: ChatSession[]) => {
  const buckets = new Map<string, ChatSession[]>();
  for (const session of sessions) {
    const key = bucketOf(session.createdAt);
    const list = buckets.get(key);
    if (list) list.push(session);
    else buckets.set(key, [session]);
  }
  return BUCKET_ORDER.filter(k => buckets.has(k)).map(k => ({
    label: k,
    sessions: buckets.get(k)!,
  }));
};

/* ── One chat row ─────────────────────────────────────────────── */
const SessionRow = ({
  session,
  isActive,
  onOpen,
}: {
  session: ChatSession;
  isActive: boolean;
  onOpen: () => void;
}) => {
  const { renameSession, deleteSession } = useChatStore();
  const [mode, setMode] = useState<'idle' | 'renaming' | 'confirming'>('idle');
  const [draft, setDraft] = useState(session.title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (mode === 'renaming') inputRef.current?.select();
  }, [mode]);

  const commitRename = () => {
    const next = draft.trim();
    if (next && next !== session.title) renameSession(session.id, next);
    setMode('idle');
  };

  if (mode === 'renaming') {
    return (
      <div className="flex items-center gap-1.5 h-9 px-1.5 rounded-lg bg-canvas border border-ink/20">
        <input
          ref={inputRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={e => {
            if (e.key === 'Enter') commitRename();
            if (e.key === 'Escape') {
              setDraft(session.title);
              setMode('idle');
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-sm text-ink px-1.5 focus:outline-none"
          aria-label="Chat name"
        />
      </div>
    );
  }

  if (mode === 'confirming') {
    return (
      <div className="flex items-center gap-2 h-9 px-2.5 rounded-lg bg-surface-3">
        <span className="min-w-0 flex-1 truncate text-[0.8125rem] text-ink">Delete chat?</span>
        <button
          onClick={() => deleteSession(session.id)}
          className="text-[0.75rem] font-semibold text-ink underline underline-offset-2"
        >
          Delete
        </button>
        <button
          onClick={() => setMode('idle')}
          className="text-[0.75rem] text-ink-3 hover:text-ink"
        >
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div
      onClick={onOpen}
      className={`group relative flex items-center h-9 px-2.5 rounded-lg cursor-pointer transition-colors ${
        isActive ? 'bg-surface-3 text-ink' : 'text-ink-2 hover:bg-surface-2 hover:text-ink'
      }`}
    >
      <span className="min-w-0 flex-1 truncate text-[0.8125rem] pr-1">{session.title}</span>

      {/* Actions ride on a fade so the list stays quiet at rest. */}
      <span
        className={`absolute right-1.5 flex items-center gap-0.5 pl-6 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity ${
          isActive ? 'bg-gradient-to-l from-surface-3 via-surface-3' : 'bg-gradient-to-l from-surface-2 via-surface-2'
        } to-transparent`}
      >
        <button
          onClick={e => {
            e.stopPropagation();
            setDraft(session.title);
            setMode('renaming');
          }}
          className="grid place-items-center w-6 h-6 rounded-md text-ink-3 hover:text-ink hover:bg-black/[0.06]"
          title="Rename"
          aria-label={`Rename ${session.title}`}
        >
          <Pencil className="w-3.5 h-3.5" strokeWidth={1.7} />
        </button>
        <button
          onClick={e => {
            e.stopPropagation();
            setMode('confirming');
          }}
          className="grid place-items-center w-6 h-6 rounded-md text-ink-3 hover:text-ink hover:bg-black/[0.06]"
          title="Delete"
          aria-label={`Delete ${session.title}`}
        >
          <Trash2 className="w-3.5 h-3.5" strokeWidth={1.7} />
        </button>
      </span>
    </div>
  );
};

/* ── Sidebar body ─────────────────────────────────────────────── */
const SidebarBody = ({ onNavigate }: { onNavigate?: () => void }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { sessions, activeSessionId, createSession, switchSession } = useChatStore();
  const toggleSidebar = useUiStore(s => s.toggleSidebar);
  const setMobileNavOpen = useUiStore(s => s.setMobileNavOpen);
  const [query, setQuery] = useState('');

  const visible = query.trim()
    ? sessions.filter(s => s.title.toLowerCase().includes(query.trim().toLowerCase()))
    : sessions;
  const groups = groupSessions(visible);

  const startNewChat = () => {
    createSession();
    onNavigate?.();
    if (location.pathname !== '/tutor' && location.pathname !== '/') navigate('/tutor');
  };

  const openSession = (id: string) => {
    switchSession(id);
    onNavigate?.();
    if (location.pathname !== '/tutor' && location.pathname !== '/') navigate('/tutor');
  };

  return (
    <div className="flex flex-col h-full">
      {/* Brand + collapse */}
      <div className="flex items-center justify-between h-14 px-3 shrink-0">
        <NavLink to="/tutor" onClick={onNavigate} className="flex items-center rounded-lg px-1 py-1">
          <Logo />
        </NavLink>

        <button
          onClick={toggleSidebar}
          className="icon-btn hidden md:inline-grid"
          title="Collapse sidebar"
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose className="w-[18px] h-[18px]" strokeWidth={1.7} />
        </button>

        <button
          onClick={() => setMobileNavOpen(false)}
          className="icon-btn md:hidden"
          aria-label="Close menu"
        >
          <X className="w-[18px] h-[18px]" strokeWidth={1.7} />
        </button>
      </div>

      {/* New chat */}
      <div className="px-3 pb-2 shrink-0">
        <button
          onClick={startNewChat}
          className="flex items-center gap-2.5 w-full h-9 px-2.5 rounded-lg border border-line-strong bg-canvas text-sm font-medium text-ink hover:bg-surface-2 transition-colors"
        >
          <Plus className="w-4 h-4 shrink-0" strokeWidth={2} />
          New chat
        </button>
      </div>

      {/* Nav */}
      <nav className="px-3 pb-2 space-y-0.5 shrink-0">
        {PRIMARY_NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            onClick={onNavigate}
            className={({ isActive }) => `nav-row ${isActive ? 'nav-row-active' : ''}`}
          >
            <Icon className="w-[17px] h-[17px] shrink-0" strokeWidth={1.7} />
            <span className="truncate">{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Search — only worth showing once there is enough history to sift. */}
      {sessions.length > 4 && (
        <div className="px-3 pb-2 shrink-0">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-4" strokeWidth={1.8} />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search chats"
              className="w-full h-8 pl-8 pr-2.5 rounded-lg bg-surface-2 text-[0.8125rem] text-ink placeholder:text-ink-4 border border-transparent focus:outline-none focus:bg-canvas focus:border-line-strong transition-colors"
            />
          </div>
        </div>
      )}

      {/* History */}
      <div className="flex-1 overflow-y-auto px-3 pb-2 min-h-0">
        {groups.length === 0 ? (
          <p className="px-2.5 py-3 text-[0.8125rem] text-ink-4">
            {query ? 'No chats match that.' : 'Your chats will appear here.'}
          </p>
        ) : (
          groups.map(group => (
            <div key={group.label} className="pt-3 first:pt-1">
              <p className="section-label px-2.5 pb-1.5">{group.label}</p>
              <div className="space-y-0.5">
                {group.sessions.map(session => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    isActive={session.id === activeSessionId}
                    onOpen={() => openSession(session.id)}
                  />
                ))}
              </div>
            </div>
          ))
        )}
      </div>

    </div>
  );
};

/* ── Desktop rail + mobile drawer ─────────────────────────────── */
export const Sidebar = () => {
  const sidebarOpen = useUiStore(s => s.sidebarOpen);
  const mobileNavOpen = useUiStore(s => s.mobileNavOpen);
  const setMobileNavOpen = useUiStore(s => s.setMobileNavOpen);

  // Escape closes the drawer — a drawer you can only dismiss by aiming at a
  // small X is the kind of thing that makes a phone feel broken.
  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMobileNavOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [mobileNavOpen, setMobileNavOpen]);

  return (
    <>
      <aside
        className={`hidden md:flex flex-col shrink-0 h-full bg-surface border-r border-line overflow-hidden transition-[width] duration-200 ease-out ${
          sidebarOpen ? 'w-[260px]' : 'w-0 border-r-0'
        }`}
      >
        <div className="w-[260px] h-full">
          <SidebarBody />
        </div>
      </aside>

      {/* Mobile drawer */}
      <div
        className={`md:hidden fixed inset-0 z-50 ${mobileNavOpen ? '' : 'pointer-events-none'}`}
        aria-hidden={!mobileNavOpen}
      >
        <div
          onClick={() => setMobileNavOpen(false)}
          className={`absolute inset-0 bg-ink/25 transition-opacity duration-200 ${
            mobileNavOpen ? 'opacity-100' : 'opacity-0'
          }`}
        />
        <div
          className={`absolute inset-y-0 left-0 w-[276px] max-w-[85vw] bg-surface border-r border-line shadow-pop transition-transform duration-200 ease-out ${
            mobileNavOpen ? 'translate-x-0' : '-translate-x-full'
          }`}
        >
          <SidebarBody onNavigate={() => setMobileNavOpen(false)} />
        </div>
      </div>
    </>
  );
};
