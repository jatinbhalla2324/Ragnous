import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDown, Bookmark, Check, Copy, Loader2, Volume2, VolumeX } from 'lucide-react';
import { useChatStore } from '../../store/chatStore';
import { useNotesStore } from '../../store/notesStore';
import { ConfidenceMode } from '../../types/rag';
import { ArtifactRenderer } from '../artifacts/ArtifactRenderer';
import { ThinkingIndicator } from './ThinkingIndicator';
import { PyqCard } from './PyqCard';
import { MessageAttachments } from './Attachments';
import { Prose } from './Markdown';
import { API_BASE_URL } from '../../api/client';

/* ── Where the answer came from ─────────────────────────────────
   One muted line. The dot carries the colour; the text stays grey
   so it never competes with the answer above it.
   ─────────────────────────────────────────────────────────────── */
const CONFIDENCE_META: Record<ConfidenceMode, { label: string; color: string }> = {
  ncert_verified: { label: 'NCERT verified', color: '#0D0D0D' },
  extended_reference: { label: 'Extended reference', color: '#8E8E8E' },
  ai_knowledge: { label: 'Ragnous knowledge', color: '#C9C9C9' },
};

const ConfidenceTag = ({ mode, score }: { mode: ConfidenceMode; score?: number }) => {
  const meta = CONFIDENCE_META[mode] ?? CONFIDENCE_META.ai_knowledge;
  const pct = typeof score === 'number' ? Math.round(Math.min(99, Math.max(0, score))) : null;

  return (
    <span
      className="inline-flex items-center gap-1.5 text-[0.75rem] text-ink-3 select-none"
      title={pct !== null ? `Retrieval confidence ${pct}%` : meta.label}
    >
      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: meta.color }} />
      {meta.label}
      {pct !== null && <span className="tabular-nums text-ink-4">· {pct}%</span>}
    </span>
  );
};

/* ── Speech ─────────────────────────────────────────────────────
   Deepgram speaks plain prose, so the markdown has to come off first.
   ─────────────────────────────────────────────────────────────── */
const stripMarkdown = (md: string): string =>
  md
    .replace(/!\[.*?\]\(.*?\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]+`/g, '')
    .replace(/#{1,6}\s*/g, '')
    .replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1')
    .replace(/_{1,2}([^_]+)_{1,2}/g, '$1')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ' ')
    .trim();

export const ChatThread = () => {
  const { messages: getMessages, isStreaming, isVoiceActive, activeSessionId } = useChatStore();
  const messages = getMessages();

  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [loadingTtsId, setLoadingTtsId] = useState<string | null>(null);
  const [atBottom, setAtBottom] = useState(true);

  /* Which messages were already on screen when this chat opened. Only a turn
     that arrives while you are watching should type itself out — reloading the
     page and seeing yesterday's answer re-typed is disorienting. */
  const known = useRef<{ sessionId: string; ids: Set<string> } | null>(null);
  if (!known.current || known.current.sessionId !== activeSessionId) {
    known.current = { sessionId: activeSessionId, ids: new Set(messages.map(m => m.id)) };
  }
  const isFresh = (id: string) => !known.current!.ids.has(id);

  /* Only auto-scroll while the reader is already at the bottom — yanking the
     view down mid-scroll is the fastest way to make a chat feel hostile. */
  useEffect(() => {
    if (atBottom) bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, isStreaming]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'auto' });
    setAtBottom(true);
  }, [activeSessionId]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 120);
  };

  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    setSpeakingId(null);
  }, []);

  useEffect(() => stopSpeaking, [stopSpeaking]);

  const handleSpeak = useCallback(
    async (id: string, text: string) => {
      if (speakingId === id) {
        stopSpeaking();
        return;
      }
      stopSpeaking();

      setLoadingTtsId(id);
      try {
        const clean = stripMarkdown(text).slice(0, 2000);
        // Our backend calls Deepgram and hands back the mp3; the key stays there.
        const res = await fetch(`${API_BASE_URL}/voice/speak`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: clean, model: 'aura-asteria-en' }),
        });
        if (!res.ok) {
          console.error('[TTS] speak failed:', res.status, await res.text());
          return;
        }

        const url = URL.createObjectURL(await res.blob());
        const audio = new Audio(url);
        audioRef.current = audio;
        setSpeakingId(id);

        const cleanup = () => {
          URL.revokeObjectURL(url);
          setSpeakingId(null);
          audioRef.current = null;
        };
        audio.onended = cleanup;
        audio.onerror = cleanup;

        await audio.play();
      } catch (e) {
        console.error('[TTS] Failed to play audio:', e);
        setSpeakingId(null);
      } finally {
        setLoadingTtsId(null);
      }
    },
    [speakingId, stopSpeaking]
  );

  const handleCopy = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1800);
  };

  /* The notes artifact's button asks for the same notes the header button
     builds, so it goes through the same store action — class, language and
     subjects included. */
  const handleArtifactAction = async (action: string) => {
    if (action !== 'download_pdf') return;
    await useNotesStore.getState().exportNotes();
  };

  const handleSave = (id: string) => {
    setSavedId(id);
    setTimeout(() => setSavedId(null), 1800);
  };

  const pendingTurn = isStreaming && messages[messages.length - 1]?.role === 'user';

  return (
    <div className="relative flex-1 min-h-0">
      <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 sm:px-6 pt-6 pb-10 space-y-8">
          {messages.map((msg, index) => {
            const isAssistant = msg.role === 'assistant';
            const isLatestAssistant = isAssistant && index === messages.length - 1;
            const isGenerating = isLatestAssistant && isStreaming;

            if (!isAssistant) {
              return (
                <div key={msg.id} className="rise-in flex flex-col items-end">
                  {msg.attachments && <MessageAttachments attachments={msg.attachments} />}
                  {/* A file on its own is a complete turn — there is no bubble
                      to draw when the student attached a page and said nothing. */}
                  {msg.content && (
                    <div className="bubble-user max-w-[85%] sm:max-w-[76%] px-4 py-2.5 text-[0.9375rem] leading-relaxed whitespace-pre-wrap break-words">
                      {msg.content}
                      {msg.isVoice && isVoiceActive && (
                        <span className="ml-2 text-[0.6875rem] uppercase tracking-wider text-ink-4">
                          voice
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            }

            return (
              <div key={msg.id} className="rise-in group">
                {isGenerating ? (
                  <ThinkingIndicator />
                ) : (
                  <Prose content={msg.content} animate={isLatestAssistant && isFresh(msg.id)} />
                )}

                {msg.artifact && !isGenerating && (
                  <div className="mt-4 rounded-2xl border border-line overflow-hidden">
                    <ArtifactRenderer payload={msg.artifact as any} onAction={handleArtifactAction} />
                  </div>
                )}

                {msg.pyq && !isGenerating && <PyqCard pyq={msg.pyq} />}

                {!isGenerating && (
                  <div className="mt-3 flex items-center gap-3 flex-wrap min-h-[2rem]">
                    <div className="flex items-center gap-0.5 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 transition-opacity duration-200">
                      <button
                        onClick={() => handleCopy(msg.id, msg.content)}
                        className="icon-btn"
                        title="Copy answer"
                        aria-label="Copy answer"
                      >
                        {copiedId === msg.id ? (
                          <Check className="w-4 h-4 text-ink" strokeWidth={2} />
                        ) : (
                          <Copy className="w-4 h-4" strokeWidth={1.7} />
                        )}
                      </button>

                      <button
                        onClick={() => handleSave(msg.id)}
                        className="icon-btn"
                        title="Save to revision"
                        aria-label="Save to revision"
                      >
                        <Bookmark
                          className={`w-4 h-4 ${savedId === msg.id ? 'text-ink fill-ink' : ''}`}
                          strokeWidth={1.7}
                        />
                      </button>

                      <button
                        onClick={() => handleSpeak(msg.id, msg.content)}
                        disabled={loadingTtsId !== null && loadingTtsId !== msg.id}
                        className={`icon-btn ${speakingId === msg.id ? 'text-ink bg-surface-2' : ''}`}
                        title={speakingId === msg.id ? 'Stop reading' : 'Read aloud'}
                        aria-label={speakingId === msg.id ? 'Stop reading' : 'Read aloud'}
                      >
                        {loadingTtsId === msg.id ? (
                          <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.9} />
                        ) : speakingId === msg.id ? (
                          <VolumeX className="w-4 h-4" strokeWidth={1.7} />
                        ) : (
                          <Volume2 className="w-4 h-4" strokeWidth={1.7} />
                        )}
                      </button>
                    </div>

                    {msg.confidenceMode && (
                      <ConfidenceTag mode={msg.confidenceMode} score={msg.confidenceScore} />
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {pendingTurn && (
            <div className="rise-in">
              <ThinkingIndicator />
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Jump back down — appears only once the reader has scrolled away. */}
      {!atBottom && (
        <button
          onClick={() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' })}
          className="absolute bottom-4 left-1/2 -translate-x-1/2 grid place-items-center w-9 h-9 rounded-full bg-canvas border border-line-strong shadow-pop text-ink-2 hover:text-ink animate-fadeIn"
          aria-label="Scroll to latest message"
        >
          <ArrowDown className="w-4 h-4" strokeWidth={1.9} />
        </button>
      )}
    </div>
  );
};
