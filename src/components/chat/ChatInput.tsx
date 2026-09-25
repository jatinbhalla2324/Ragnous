import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, Lightbulb, Mic, Paperclip, Square, Upload, Zap } from 'lucide-react';
import { useChatStore } from '../../store/chatStore';
import { useSendMessage } from '../../hooks/useSendMessage';
import { Attachment, RejectedAttachment } from '../../types/rag';
import {
  ACCEPTED_TYPES,
  MAX_FILES,
  MAX_FILE_BYTES,
  formatBytes,
  uploadAttachments,
} from '../../api/attachments';
import { AttachmentTray } from './Attachments';
import { LiveTutorButton } from './LiveTutorButton';
import { WS_BASE_URL } from '../../api/client';

interface ChatInputProps {
  autoFocus?: boolean;
}

/**
 * The composer. One white slab with a hairline edge: the field, a mode toggle,
 * and the three ways to send — type, dictate, or talk live.
 *
 * Files can arrive three ways, and all three land in the same place: the
 * paperclip, a paste, or a drop anywhere on the page. They are read by the
 * backend the moment they arrive, so the tray below shows a real page count
 * (or a real error) before the question is even finished.
 */
export const ChatInput = ({ autoFocus = false }: ChatInputProps) => {
  const [input, setInput] = useState('');
  const [isRecording, setIsRecording] = useState(false);

  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [attachmentErrors, setAttachmentErrors] = useState<RejectedAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);

  const { isHomeworkMode, setHomeworkMode, isStreaming } = useChatStore();
  const send = useSendMessage();

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const silenceTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Drag events fire for every child element the pointer crosses, so a plain
  // boolean flickers the overlay off the moment the cursor enters a nested
  // node. Counting enter/leave pairs is the standard fix.
  const dragDepth = useRef(0);

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    return () => {
      if (silenceTimeoutRef.current) clearTimeout(silenceTimeoutRef.current);
      if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop());
      if (socketRef.current) socketRef.current.close();
    };
  }, []);

  // Grow with the content, up to about seven lines.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = '0px';
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }, [input]);

  /* ── Attachments ─────────────────────────────────────────────── */

  const addFiles = useCallback(
    async (incoming: FileList | File[] | null | undefined) => {
      const files = Array.from(incoming ?? []);
      if (files.length === 0) return;

      setAttachmentErrors([]);

      const rejected: RejectedAttachment[] = [];
      const room = MAX_FILES - attachments.length;
      if (room <= 0) {
        setAttachmentErrors([
          { name: '', error: `You can attach up to ${MAX_FILES} files per message.` },
        ]);
        return;
      }

      // Catch the obvious failures here so a 40 MB video never starts uploading.
      const sized = files.filter(f => {
        if (f.size > MAX_FILE_BYTES) {
          rejected.push({
            name: f.name,
            error: `${formatBytes(f.size)} — the limit is ${formatBytes(MAX_FILE_BYTES)} per file.`,
          });
          return false;
        }
        return true;
      });

      const accepted = sized.slice(0, room);
      sized.slice(room).forEach(f =>
        rejected.push({ name: f.name, error: `Only ${MAX_FILES} files fit in one message.` })
      );

      if (accepted.length === 0) {
        setAttachmentErrors(rejected);
        return;
      }

      setUploadingCount(c => c + accepted.length);
      try {
        const result = await uploadAttachments(accepted);
        // Re-check the ceiling against the live value: a second drop can land
        // while the first upload is still in flight.
        setAttachments(current => [...current, ...result.attachments].slice(0, MAX_FILES));
        setAttachmentErrors([...rejected, ...result.rejected]);
      } catch (e) {
        console.error('Attachment upload failed:', e);
        setAttachmentErrors([
          ...rejected,
          { name: '', error: e instanceof Error ? e.message : 'Could not read those files.' },
        ]);
      } finally {
        setUploadingCount(c => Math.max(0, c - accepted.length));
      }
    },
    [attachments.length]
  );

  const removeAttachment = (index: number) => {
    setAttachments(current => current.filter((_, i) => i !== index));
    setAttachmentErrors([]);
  };

  /* Drop anywhere on the page, the way ChatGPT and Claude do — hunting for the
     composer with a file already held down is needless precision work. */
  useEffect(() => {
    const hasFiles = (e: DragEvent) =>
      Array.from(e.dataTransfer?.types ?? []).includes('Files');

    const onDragEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current += 1;
      setIsDragging(true);
    };
    const onDragOver = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    };
    const onDragLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setIsDragging(false);
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      // Without this the browser navigates away to display the dropped file.
      e.preventDefault();
      dragDepth.current = 0;
      setIsDragging(false);
      void addFiles(e.dataTransfer?.files);
    };

    window.addEventListener('dragenter', onDragEnter);
    window.addEventListener('dragover', onDragOver);
    window.addEventListener('dragleave', onDragLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragenter', onDragEnter);
      window.removeEventListener('dragover', onDragOver);
      window.removeEventListener('dragleave', onDragLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [addFiles]);

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files ?? []);
    if (files.length === 0) return;
    // Copied text arrives with an image/* item too on some platforms; only
    // swallow the paste when there is genuinely a file behind it.
    e.preventDefault();
    void addFiles(files);
  };

  /* ── Sending ─────────────────────────────────────────────────── */

  const handleSend = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (isStreaming || uploadingCount > 0) return;
    const text = input.trim();
    if (!text && attachments.length === 0) return;

    const outgoing = attachments;
    setInput('');
    setAttachments([]);
    setAttachmentErrors([]);
    void send(text, outgoing);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  /* ── Dictation ───────────────────────────────────────────────── */

  const stopRecording = () => {
    if (silenceTimeoutRef.current) clearTimeout(silenceTimeoutRef.current);
    if (mediaRecorderRef.current?.state === 'recording') mediaRecorderRef.current.stop();
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: 'CloseStream' }));
      setTimeout(() => socketRef.current?.close(), 1000);
    }
    if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop());
    setIsRecording(false);
  };

  const toggleRecording = async () => {
    if (isRecording) {
      stopRecording();
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Via our backend, which holds the Deepgram key. Nothing secret ships
      // to the browser.
      const socket = new WebSocket(
        `${WS_BASE_URL}/voice/listen?model=nova-2&smart_format=true&interim_results=true`
      );
      socketRef.current = socket;

      socket.onopen = () => {
        const recorder = new MediaRecorder(stream);
        mediaRecorderRef.current = recorder;

        recorder.ondataavailable = e => {
          if (e.data.size > 0 && socket.readyState === WebSocket.OPEN) socket.send(e.data);
        };

        recorder.start(250);
        setIsRecording(true);

        if (silenceTimeoutRef.current) clearTimeout(silenceTimeoutRef.current);
        silenceTimeoutRef.current = setTimeout(stopRecording, 1500);
      };

      socket.onmessage = message => {
        const received = JSON.parse(message.data);
        const transcript = received?.channel?.alternatives?.[0]?.transcript;
        if (!transcript) return;

        if (silenceTimeoutRef.current) clearTimeout(silenceTimeoutRef.current);
        silenceTimeoutRef.current = setTimeout(stopRecording, 1500);

        if (received.is_final) {
          setInput(prev => prev + (prev.length > 0 ? ' ' : '') + transcript);
        }
      };

      socket.onerror = error => {
        console.error('Deepgram WebSocket error:', error);
        setIsRecording(false);
      };
    } catch (err) {
      console.error('Error accessing microphone:', err);
      alert('Could not access the microphone.');
    }
  };

  const canSend =
    (input.trim().length > 0 || attachments.length > 0) && !isStreaming && uploadingCount === 0;

  return (
    <div className="relative">
      <div className="composer">
        <form onSubmit={handleSend}>
          <AttachmentTray
            attachments={attachments}
            uploadingCount={uploadingCount}
            onRemove={removeAttachment}
            className="px-3 pt-3"
          />

          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={
              isHomeworkMode
                ? 'Ask for a hint — I will guide you, not hand it over'
                : 'Ask anything, or drop a photo, PDF or document'
            }
            className="composer-field block w-full bg-transparent px-5 pt-4 pb-1.5 text-[0.9375rem] leading-relaxed text-ink placeholder:text-ink-4 focus:outline-none max-h-[168px] overflow-y-auto"
          />

          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5 pt-1">
            {/* How the tutor should answer */}
            <div className="flex items-center gap-1 min-w-0">
              <button
                type="button"
                onClick={() => setHomeworkMode(!isHomeworkMode)}
                title={
                  isHomeworkMode
                    ? 'Hint mode — guided, step by step'
                    : 'Direct mode — straight answers'
                }
                aria-pressed={isHomeworkMode}
                className={`inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-[0.8125rem] font-medium transition-colors ${
                  isHomeworkMode
                    ? 'bg-ink text-white hover:bg-[#262626]'
                    : 'text-ink-2 hover:bg-surface-2 hover:text-ink'
                }`}
              >
                {isHomeworkMode ? (
                  <Lightbulb className="w-4 h-4" strokeWidth={1.8} />
                ) : (
                  <Zap className="w-4 h-4" strokeWidth={1.8} />
                )}
                {isHomeworkMode ? 'Hint' : 'Direct'}
              </button>

              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={attachments.length >= MAX_FILES}
                className="icon-btn disabled:opacity-40 disabled:cursor-not-allowed"
                title="Attach a photo, PDF or document"
                aria-label="Attach files"
              >
                <Paperclip className="w-[18px] h-[18px]" strokeWidth={1.7} />
              </button>
            </div>

            {/* Ways to send */}
            <div className="flex items-center gap-1 shrink-0">
              <button
                type="button"
                onClick={toggleRecording}
                className={`icon-btn relative ${isRecording ? 'bg-ink text-white hover:bg-[#262626] hover:text-white' : ''}`}
                title={isRecording ? 'Listening — tap to stop' : 'Dictate'}
                aria-label="Dictate"
              >
                {isRecording ? (
                  <>
                    <span className="absolute inset-0 rounded-lg bg-ink/15 animate-ping" />
                    <Square className="w-3.5 h-3.5 relative fill-current" strokeWidth={0} />
                  </>
                ) : (
                  <Mic className="w-[18px] h-[18px]" strokeWidth={1.7} />
                )}
              </button>

              <LiveTutorButton />

              <button
                type="submit"
                disabled={!canSend}
                title="Send · Enter"
                aria-label="Send message"
                className={`ml-0.5 w-8 h-8 rounded-full grid place-items-center transition-all duration-200 ${
                  canSend
                    ? 'bg-ink text-white hover:bg-[#262626] active:scale-95'
                    : 'bg-surface-3 text-ink-4 cursor-not-allowed'
                }`}
              >
                <ArrowUp className="w-[18px] h-[18px]" strokeWidth={2.2} />
              </button>
            </div>
          </div>
        </form>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED_TYPES}
          className="hidden"
          onChange={e => {
            void addFiles(e.target.files);
            // Reset, or picking the same file twice in a row fires nothing.
            e.target.value = '';
          }}
        />
      </div>

      {/* Whatever the backend would not read, named so it can be fixed. */}
      {attachmentErrors.length > 0 && (
        <ul className="mt-2 space-y-1 px-1">
          {attachmentErrors.map((r, i) => (
            <li key={`${r.name}-${i}`} className="text-[0.75rem] text-ink-3">
              {r.name ? <span className="text-ink-2 font-medium">{r.name}</span> : null}
              {r.name ? ' — ' : ''}
              {r.error}
            </li>
          ))}
        </ul>
      )}

      {/* The drop target. Fixed, so it covers the page rather than the slab —
          the drop handler listens on the window, and the overlay should say so. */}
      {isDragging && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-canvas/80 backdrop-blur-sm pointer-events-none animate-fadeIn">
          <div className="flex flex-col items-center gap-3 px-10 py-8 rounded-3xl border-2 border-dashed border-ink bg-canvas shadow-pop">
            <span className="grid place-items-center w-12 h-12 rounded-full bg-surface-2 border border-line">
              <Upload className="w-5 h-5 text-ink" strokeWidth={1.8} />
            </span>
            <p className="text-[0.9375rem] font-medium text-ink">Drop to attach</p>
            <p className="text-[0.8125rem] text-ink-3">
              Images, PDF, Word or text · up to {MAX_FILES} files
            </p>
          </div>
        </div>
      )}
    </div>
  );
};
