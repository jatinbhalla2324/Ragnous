import { ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Menu, PanelLeftOpen, SquarePen } from 'lucide-react';
import { Sidebar } from './Sidebar';
import { LogoMark } from './Logo';
import { useUiStore } from '../../store/uiStore';
import { useChatStore } from '../../store/chatStore';

interface AppShellProps {
  children: ReactNode;
  /** Shown in the top bar when no `headerCenter` is supplied. */
  title?: string;
  /** Custom control cluster in place of the title (the tutor's model pickers). */
  headerLead?: ReactNode;
  headerActions?: ReactNode;
  /**
   * Pages scroll inside the shell by default. The chat thread manages its own
   * scroll container so it can pin the composer, and opts out.
   */
  scroll?: boolean;
  /** Width cap + padding applied to scrolling pages. */
  contentClassName?: string;
}

/**
 * The frame every screen sits in: a persistent rail on the left, a slim bar on
 * top, and one white content column. Nothing else competes for attention.
 */
export const AppShell = ({
  children,
  title,
  headerLead,
  headerActions,
  scroll = true,
  contentClassName = 'mx-auto w-full max-w-4xl px-5 py-8 sm:px-8',
}: AppShellProps) => {
  const navigate = useNavigate();
  const location = useLocation();
  const sidebarOpen = useUiStore(s => s.sidebarOpen);
  const toggleSidebar = useUiStore(s => s.toggleSidebar);
  const setMobileNavOpen = useUiStore(s => s.setMobileNavOpen);
  const createSession = useChatStore(s => s.createSession);

  const startNewChat = () => {
    createSession();
    if (location.pathname !== '/tutor' && location.pathname !== '/') navigate('/tutor');
  };

  return (
    <div className="flex h-[100dvh] w-full overflow-hidden bg-canvas text-ink">
      <Sidebar />

      <div className="flex flex-col flex-1 min-w-0">
        <header className="flex items-center gap-1.5 h-14 px-2.5 sm:px-4 shrink-0 bg-canvas">
          {/* Mobile: open the drawer */}
          <button
            onClick={() => setMobileNavOpen(true)}
            className="icon-btn md:hidden"
            aria-label="Open menu"
          >
            <Menu className="w-[19px] h-[19px]" strokeWidth={1.7} />
          </button>

          {/* Desktop: the rail's re-open control lives here while it is closed */}
          {!sidebarOpen && (
            <>
              <button
                onClick={toggleSidebar}
                className="icon-btn hidden md:inline-grid"
                title="Open sidebar"
                aria-label="Open sidebar"
              >
                <PanelLeftOpen className="w-[18px] h-[18px]" strokeWidth={1.7} />
              </button>
              <button
                onClick={startNewChat}
                className="icon-btn hidden md:inline-grid"
                title="New chat"
                aria-label="New chat"
              >
                <SquarePen className="w-[18px] h-[18px]" strokeWidth={1.7} />
              </button>
            </>
          )}

          {/* Mobile brand — the sidebar wordmark is hidden behind the drawer */}
          <span className="md:hidden flex items-center gap-2 pl-0.5">
            <LogoMark size={22} />
          </span>

          <div className="flex items-center gap-2 min-w-0 flex-1 pl-1">
            {headerLead ?? (
              title && (
                <h1 className="text-[0.9375rem] font-semibold text-ink truncate">{title}</h1>
              )
            )}
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            {headerActions}
            <button
              onClick={startNewChat}
              className="icon-btn md:hidden"
              aria-label="New chat"
            >
              <SquarePen className="w-[18px] h-[18px]" strokeWidth={1.7} />
            </button>
          </div>
        </header>

        <main className={`flex-1 min-h-0 min-w-0 ${scroll ? 'overflow-y-auto' : 'flex flex-col overflow-hidden'}`}>
          {scroll ? <div className={contentClassName}>{children}</div> : children}
        </main>
      </div>
    </div>
  );
};
