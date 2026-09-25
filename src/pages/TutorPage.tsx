import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Download, Sparkles, Trophy } from 'lucide-react';
import { AppShell } from '../components/layout/AppShell';
import { ChatThread } from '../components/chat/ChatWindow';
import { ChatInput } from '../components/chat/ChatInput';
import { EmptyState } from '../components/chat/EmptyState';
import { Select } from '../components/ui/Select';
import { QuizPanel } from '../components/chat/QuizPanel';
import { MiniQuizPopup } from '../components/chat/MiniQuizPopup';
import { useChatStore } from '../store/chatStore';
import { useProfileStore } from '../store/profileStore';
import { shouldOfferMiniQuiz, useQuizStore } from '../store/quizStore';
import { useNotesStore } from '../store/notesStore';

const MODELS = [
  { id: 'ragnous_x1', label: 'Ragnous X1', description: 'Quick answers for everyday doubts' },
  { id: 'ragnous_pro_x1', label: 'Ragnous Pro X1', description: 'Deeper reasoning for hard problems' },
];

/** A quiz needs some substance to work from — nothing meaningful comes back
 *  after "hi" and one answer, so the button only shows once the chat has real
 *  content behind it. */
const MIN_TURNS_FOR_QUIZ = 4;

const languageIdToLabel = (id: string): string => {
  if (id === 'hinglish') return 'Hinglish';
  if (id === 'hindi') return 'Hindi';
  return 'English';
};

export default function TutorPage() {
  const navigate = useNavigate();
  const {
    messages: getMessages,
    activeSession,
    activeSessionId,
    isStreaming,
    setModel,
    setStudentClass,
    setLanguage,
  } = useChatStore();
  const profile = useProfileStore(state => state.profile);

  const quiz = useQuizStore(state => state.quiz);
  const quizLoading = useQuizStore(state => state.loading);
  const quizError = useQuizStore(state => state.error);
  const miniQuiz = useQuizStore(state => state.miniQuiz);
  const startQuiz = useQuizStore(state => state.start);
  const startMini = useQuizStore(state => state.startMini);
  const closeQuiz = useQuizStore(state => state.close);
  const closeMini = useQuizStore(state => state.closeMini);
  const noteAssistantTurn = useQuizStore(state => state.noteAssistantTurn);

  const messages = getMessages();
  const session = activeSession();
  const exportNotes = useNotesStore(state => state.exportNotes);
  const exporting = useNotesStore(state => state.exporting);
  const notesError = useNotesStore(state => state.error);

  // First-time visitors go through onboarding once — after that the app opens
  // straight into a chat, and the class/subject selectors are gone from the
  // header because the profile already answered those questions.
  useEffect(() => {
    if (!profile.onboarded) {
      navigate('/onboarding', { replace: true });
    }
  }, [profile.onboarded, navigate]);

  // Keep every session pinned to what the student picked in onboarding.
  useEffect(() => {
    if (!session) return;
    if (session.studentClass !== profile.grade) setStudentClass(profile.grade);
    const langLabel = languageIdToLabel(profile.languagePreference);
    if (session.language !== langLabel) setLanguage(langLabel);
  }, [session, profile.grade, profile.languagePreference, setStudentClass, setLanguage]);

  // ── Random mid-chat mini-quiz check-ins ──────────────────────────────
  // Every time a NEW assistant message finishes streaming, roll a probability
  // to offer a small MCQ popup. Cooldowns and content-length checks in
  // shouldOfferMiniQuiz keep this from becoming spam.
  const lastAssistantId = useRef<string | null>(null);
  useEffect(() => {
    if (isStreaming) return;
    const lastMsg = messages[messages.length - 1];
    if (!lastMsg || lastMsg.role !== 'assistant') return;
    if (lastMsg.id === lastAssistantId.current) return;
    lastAssistantId.current = lastMsg.id;

    // Skip the store's own "Opening a quiz…" confirmation line.
    if (lastMsg.content.startsWith('Opening a quick MCQ quiz')) return;

    noteAssistantTurn();

    const assistantTurns = messages.filter(m => m.role === 'assistant').length;
    const state = useQuizStore.getState();

    if (
      !state.quiz &&
      !state.miniQuiz &&
      !state.loading &&
      !state.miniLoading &&
      shouldOfferMiniQuiz({
        turnsSinceQuiz: state.turnsSinceQuiz,
        assistantTurns,
        lastAssistantContent: lastMsg.content,
      })
    ) {
      // Take the whole session (capped) so a chat that jumped from motion to
      // chemistry hands the backend BOTH topics — not just the most recent one.
      const history = messages.slice(-32).map(m => ({ role: m.role, content: m.content }));
      void startMini({
        history,
        studentClass: profile.grade,
        language: languageIdToLabel(profile.languagePreference),
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, isStreaming]);

  // A brand-new chat starts with a clean "no quiz recently" state so the
  // popup can fire again in the next conversation.
  useEffect(() => {
    lastAssistantId.current = null;
  }, [activeSessionId]);

  const handleQuiz = async (topicHint?: string) => {
    if (quizLoading || messages.length < MIN_TURNS_FOR_QUIZ) return;
    const history = messages.slice(-12).map(m => ({ role: m.role, content: m.content }));
    await startQuiz({
      history,
      studentClass: profile.grade,
      language: languageIdToLabel(profile.languagePreference),
      topicHint,
    });
  };

  const canQuiz = messages.length >= MIN_TURNS_FOR_QUIZ;

  /* Header stripped to what actually changes per chat: the model. Class and
     language are answered once in onboarding and shown as read-only chips. */
  const headerLead = (
    <div className="flex items-center gap-2 min-w-0">
      <Select
        value={session?.selectedModel ?? 'ragnous_x1'}
        options={MODELS}
        onChange={setModel}
        label="Model"
        menuWidth="w-[16rem]"
      />
      <span className="hidden sm:inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 px-2.5 py-1 text-[0.75rem] text-ink-3">
        Class {profile.grade}
      </span>
      {profile.subjects[0] && (
        <span className="hidden md:inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 px-2.5 py-1 text-[0.75rem] text-ink-3">
          {profile.subjects[0]}
        </span>
      )}
    </div>
  );

  const headerActions = (
    <div className="flex items-center gap-2">
      <span
        className="hidden sm:inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 px-2.5 h-8 text-[0.75rem] text-ink-2 tabular-nums"
        title="Points earned from quizzes"
      >
        <Trophy className="w-3.5 h-3.5" strokeWidth={1.8} />
        {profile.points} pts
      </span>

      {canQuiz && (
        <button
          onClick={() => { void handleQuiz(); }}
          disabled={quizLoading}
          className="btn btn-secondary h-8 px-3 text-[0.8125rem]"
          title="Quiz me on what we've been discussing"
        >
          <Sparkles className="w-3.5 h-3.5" strokeWidth={1.8} />
          {quizLoading ? 'Building quiz…' : 'Quiz me'}
        </button>
      )}

      {messages.length > 0 && (
        <button
          onClick={() => { void exportNotes(); }}
          disabled={exporting}
          className="btn btn-secondary h-8 px-3 text-[0.8125rem] hidden sm:inline-flex"
          title="Build detailed NCERT study notes from this chat"
        >
          <Download className="w-3.5 h-3.5" strokeWidth={1.8} />
          {exporting ? 'Writing notes…' : 'Save as notes'}
        </button>
      )}
    </div>
  );

  const composer = (
    <div className="mx-auto w-full max-w-3xl">
      <ChatInput autoFocus />
    </div>
  );

  return (
    <AppShell scroll={false} headerLead={headerLead} headerActions={headerActions}>
      {messages.length === 0 ? (
        <EmptyState composer={composer} />
      ) : (
        <>
          <ChatThread />
          <div className="shrink-0 px-4 sm:px-6 pb-3 pt-1 bg-canvas">
            {(quizError || notesError) && (
              <div className="mx-auto max-w-3xl mb-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-[0.8125rem] text-ink-2">
                {quizError || notesError}
              </div>
            )}
            {composer}
            {exporting && (
              <p className="mt-2 text-center text-[0.6875rem] text-ink-4">
                Reading your NCERT chapters and writing the notes — this takes a
                few moments.
              </p>
            )}
            <p className="mt-2 text-center text-[0.6875rem] text-ink-4">
              NCERT-aligned<span className="hidden sm:inline"> · Shift + Enter for a new line</span> ·
              Check important facts against your textbook
            </p>
          </div>
        </>
      )}

      {quiz && (
        <QuizPanel
          quiz={quiz}
          onClose={closeQuiz}
          onRetake={() => {
            // Snap the same topic set for the fresh quiz so "New quiz on this
            // topic" doesn't drift onto whatever came up in the last message.
            closeQuiz();
            const hint = quiz.topics && quiz.topics.length > 0
              ? quiz.topics.join(', ')
              : quiz.topic;
            void handleQuiz(hint);
          }}
        />
      )}
      {!quiz && miniQuiz && (
        <MiniQuizPopup
          question={miniQuiz.question}
          topic={miniQuiz.topic}
          subjectArea={miniQuiz.subjectArea}
          pointsPerCorrect={miniQuiz.pointsPerCorrect}
          onDismiss={closeMini}
        />
      )}
    </AppShell>
  );
}
