import { useState, useRef, useEffect } from 'react';
import { useChatStore } from '../../store/chatStore';
import { mockChatResponse } from '../../api/chat';
import { WS_BASE_URL } from '../../api/client';

/* The agent talks out of the same speakers the mic is listening to, so every
   frame the tutor speaks comes back in through the mic. Left alone, Deepgram
   transcribes the tutor's own voice as the student and the agent answers
   itself. Two things keep that from happening:

   1. Browser AEC, asked for explicitly rather than left to defaults.
   2. A half-duplex gate — while the agent's audio is still scheduled to play,
      the mic is fed silence instead of the room. The gate reopens on a
      sustained burst of real energy so the student can still cut in. */

const AGENT_TAIL_S = 0.28;      // let residual echo decay after the last sample
const AEC_SETTLE_S = 0.3;       // AEC needs a moment to converge each turn
const BARGE_RMS_FLOOR = 0.045;  // absolute floor for "this is a person"
const BARGE_FRAMES = 3;         // ~250ms sustained before we call it a barge-in
const OUTPUT_RATE = 24000;      // exact 2:1 with 48k hardware, no resampler mush

/* Vocabulary Deepgram routinely mishears out of a general-purpose ASR — NCERT
   subject terms, chapter names, and Indian proper nouns that show up in
   school questions. A wrong transcript poisons both the retrieval query and
   the spoken answer, so biasing recognition here is the cheapest quality win
   on the whole path. Kept intentionally tight: flux keyterm biasing degrades
   when the list is padded with words that never actually occur in a turn. */
const NCERT_KEYTERMS = [
  // Meta / scope
  'NCERT', 'CBSE', 'chapter', 'syllabus',
  // Biology
  'photosynthesis', 'chlorophyll', 'mitochondria', 'nucleus', 'ribosome',
  'osmosis', 'diffusion', 'respiration', 'transpiration', 'reproduction',
  'chromosome', 'DNA', 'RNA', 'genes', 'enzyme', 'protein',
  // Chemistry
  'atom', 'molecule', 'element', 'compound', 'oxidation', 'reduction',
  'electrolysis', 'covalent', 'ionic', 'valency', 'isotope', 'catalyst',
  'hydrocarbon', 'alkane', 'alkene', 'alkyne',
  // Physics
  'velocity', 'acceleration', 'momentum', 'inertia', 'friction', 'gravity',
  'refraction', 'reflection', 'diffraction', 'electromagnetism', 'capacitor',
  'resistor', 'transistor', 'voltage', 'current', 'ohm',
  // Maths
  'algebra', 'geometry', 'trigonometry', 'calculus', 'theorem', 'polynomial',
  'quadratic', 'coordinate', 'probability', 'Pythagoras',
  // History (India-specific proper nouns that get badly mangled)
  'Sarvodaya', 'Kshatriya', 'Brahmin', 'Vaishya', 'Shudra',
  'Mahabharata', 'Ramayana', 'Ashoka', 'Chandragupta', 'Mughal',
  'Non-Cooperation Movement', 'Civil Disobedience', 'Quit India',
  'Mahatma Gandhi', 'Jawaharlal Nehru', 'Subhas Chandra Bose', 'Ambedkar',
  // Geography / civics / economics
  'monsoon', 'tributary', 'plateau', 'peninsula', 'lithosphere',
  'Panchayat', 'Lok Sabha', 'Rajya Sabha', 'GDP', 'inflation', 'demand',
];

export const LiveTutorButton = () => {
  const [isActive, setIsActive] = useState(false);
  const [status, setStatus] = useState<'idle' | 'connecting' | 'listening' | 'speaking' | 'searching...'>('idle');

  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const activeAgentMsgId = useRef<string | null>(null);
  const activeAgentMsgText = useRef<string>('');
  const activeUserMsgId = useRef<string | null>(null);
  const activeUserMsgText = useRef<string>('');
  const lastRagData = useRef<{ confidenceMode?: any, confidenceScore?: number, citations?: any[] }>({});
  const hasAddedRagMessage = useRef<boolean>(false);

  const inputAudioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const settingsAppliedRef = useRef<boolean>(false);
  const silenceFrameRef = useRef<Int16Array | null>(null);

  const audioContextRef = useRef<AudioContext | null>(null);
  const nextStartTimeRef = useRef<number>(0);
  const activeSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());

  const bargeFramesRef = useRef<number>(0);
  const echoFloorRef = useRef<number>(0);
  const agentAudioStartedAtRef = useRef<number>(0);

  const openOutputContext = () => {
    const Ctor = window.AudioContext || (window as any).webkitAudioContext;
    const ctx = new Ctor({ sampleRate: OUTPUT_RATE });
    audioContextRef.current = ctx;
    nextStartTimeRef.current = 0;
    activeSourcesRef.current.clear();
    void ctx.resume();
  };

  /* Kill whatever the agent still has queued. Used for barge-in — the context
     itself stays alive, because churning AudioContexts per turn runs into the
     per-page limit and drops audio entirely mid-conversation. */
  const stopPlayback = () => {
    const ctx = audioContextRef.current;
    activeSourcesRef.current.forEach((src) => {
      try { src.onended = null; src.stop(); } catch { /* already finished */ }
      try { src.disconnect(); } catch { /* already detached */ }
    });
    activeSourcesRef.current.clear();
    // Keep the tail window honest: echo of the last played sample is still in the room.
    nextStartTimeRef.current = ctx ? ctx.currentTime : 0;
  };

  const isAgentAudible = () => {
    const ctx = audioContextRef.current;
    if (!ctx || ctx.state === 'closed') return false;
    return nextStartTimeRef.current + AGENT_TAIL_S > ctx.currentTime;
  };

  const playAudioChunk = (data: ArrayBuffer) => {
    const ctx = audioContextRef.current;
    if (!ctx || ctx.state === 'closed' || data.byteLength < 2) return;

    // Convert 16-bit PCM to Float32
    const int16Array = new Int16Array(data);
    const float32Array = new Float32Array(int16Array.length);
    for (let i = 0; i < int16Array.length; i++) {
      float32Array[i] = int16Array[i] / 32768.0;
    }

    const audioBuffer = ctx.createBuffer(1, float32Array.length, OUTPUT_RATE);
    audioBuffer.getChannelData(0).set(float32Array);

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);

    if (nextStartTimeRef.current < ctx.currentTime) {
      // First chunk of a turn: a little lead so the scheduler never starts in the past.
      nextStartTimeRef.current = ctx.currentTime + 0.06;
      agentAudioStartedAtRef.current = nextStartTimeRef.current;
      echoFloorRef.current = 0;
    }
    source.onended = () => { activeSourcesRef.current.delete(source); };
    activeSourcesRef.current.add(source);
    source.start(nextStartTimeRef.current);
    nextStartTimeRef.current += audioBuffer.duration;
  };

  const rmsOf = (frame: Float32Array) => {
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    return Math.sqrt(sum / frame.length);
  };

  const teardown = () => {
    settingsAppliedRef.current = false;
    bargeFramesRef.current = 0;
    if (processorRef.current) {
      processorRef.current.onaudioprocess = null;
      try { processorRef.current.disconnect(); } catch { /* already detached */ }
      processorRef.current = null;
    }
    if (sourceNodeRef.current) {
      try { sourceNodeRef.current.disconnect(); } catch { /* already detached */ }
      sourceNodeRef.current = null;
    }
    if (inputAudioContextRef.current) {
      if (inputAudioContextRef.current.state !== 'closed') void inputAudioContextRef.current.close();
      inputAudioContextRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (socketRef.current) {
      const s = socketRef.current;
      socketRef.current = null;
      s.onclose = null;
      s.onerror = null;
      s.onmessage = null;
      if (s.readyState === WebSocket.OPEN || s.readyState === WebSocket.CONNECTING) s.close(1000, 'client ended session');
    }
    stopPlayback();
    if (audioContextRef.current) {
      if (audioContextRef.current.state !== 'closed') void audioContextRef.current.close();
      audioContextRef.current = null;
    }
  };

  const toggleTutor = async () => {
    if (isActive) {
      useChatStore.getState().setVoiceActive(false);
      teardown();
      setIsActive(false);
      setStatus('idle');
      return;
    }

    // Turn on
    try {
      setStatus('connecting');
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Without these the tutor hears itself and answers its own greeting.
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      streamRef.current = stream;

      openOutputContext();

      /* Build the capture graph up front so Settings can advertise the mic's
         real rate. Forcing a rate the device doesn't run at makes the browser
         resample before AEC sees it, which is exactly when echo leaks through. */
      const Ctor = window.AudioContext || (window as any).webkitAudioContext;
      const inputCtx = new Ctor();
      inputAudioContextRef.current = inputCtx;
      await inputCtx.resume();
      const inputRate = Math.round(inputCtx.sampleRate);

      /* Through our own backend, which attaches the Deepgram credentials.
         The browser never sees them. */
      const socket = new WebSocket(`${WS_BASE_URL}/voice/agent`);
      socket.binaryType = 'arraybuffer'; // Blob + await reorders chunks and garbles the voice
      socketRef.current = socket;

      socket.onopen = () => {
        useChatStore.getState().setVoiceActive(true);
        // The tutor is class-scoped, so bake the student's own class and
        // language into the Think prompt. Otherwise gpt-4o-mini free-answers
        // for "Indian school students" in general and can hand a Class 8
        // student a Class 12 explanation.
        const session = useChatStore.getState().activeSession();
        const sClass = session?.studentClass || '8th';
        const lang = session?.language || 'English';
        const setupMessage = {
          "type": "Settings",
          "audio": {
            "input": {
              "encoding": "linear16",
              "sample_rate": inputRate
            },
            "output": {
              "encoding": "linear16",
              "sample_rate": OUTPUT_RATE,
              "container": "none"
            }
          },
          "agent": {
            "speak": {
              "provider": {
                "type": "eleven_labs",
                "model_id": "eleven_multilingual_v2",
                "voice_id": "cgSgspJ2msm6clMCkdW9"
              }
            },
            "listen": {
              "provider": {
                "type": "deepgram",
                "version": "v2",
                "model": "flux-general-multi",
                // NCERT-domain vocabulary the general ASR routinely mangles.
                // A wrong transcript poisons both the retrieval query and the
                // spoken answer, so this is the cheapest quality win on the
                // whole path — Deepgram only biases towards these when the
                // audio is actually close.
                "keyterms": NCERT_KEYTERMS
              }
            },
            "think": {
              "provider": {
                "type": "open_ai",
                "model": "gpt-4o-mini"
              },
              "functions": [{
                "name": "query_knowledge_base",
                "description": "Fetches NCERT textbook context AND runs a curriculum-scoped web fallback for the student's question. The returned text is the authoritative answer to teach from. Call this for EVERY academic question before answering; do not answer from your own knowledge.",
                "parameters": {
                  "type": "object",
                  "properties": {
                    "query": {
                      "type": "string",
                      "description": "The student's question rewritten in formal NCERT terminology (e.g. 'ncp' -> 'Non-Cooperation Movement', 'plants make food' -> 'photosynthesis'). Keep it to one focused topic."
                    }
                  },
                  "required": ["query"]
                }
              }],
              "prompt": `You are the RAGNOUS AI Tutor for an Indian school student in Class ${sClass}, following the NCERT curriculum. Respond entirely in ${lang}.

RETRIEVAL RULES (non-negotiable):
1. For ANY academic question — a definition, a chapter topic, a concept, a formula, a date, an "explain X" / "what is X" / "why does X happen" — you MUST call query_knowledge_base FIRST, before you say the answer. Never answer an academic question from your own memory.
2. Rewrite the student's spoken question into the formal NCERT term before calling ("ncp" -> "Non-Cooperation Movement"). Call it at most once per turn.
3. When the function returns text, teach FROM that text. Do not contradict it. If it says the topic is outside the Class ${sClass} syllabus, relay that verdict — do not override it with your own knowledge.
4. Skip the function ONLY for: greetings, thanks, one-word acknowledgments ("ok", "cool", "got it"), and meta-turns like "say that again" or "speak slower".

DELIVERY:
- Warm, patient, encouraging — like a favourite teacher.
- Pitch the language for a Class ${sClass} student. Short sentences, one idea at a time.
- Keep the spoken reply to roughly 30-60 seconds.
- Never speak your own previous turn back. If a turn is empty or unintelligible, stay silent and wait for the student.`
            },
            "greeting": "Hello! How may I help you?"
          }
        };

        socket.send(JSON.stringify(setupMessage));

        // Start recording via AudioContext (Raw Linear16 PCM)
        const source = inputCtx.createMediaStreamSource(stream);
        sourceNodeRef.current = source;
        const processor = inputCtx.createScriptProcessor(4096, 1, 1);
        processorRef.current = processor;

        const gainNode = inputCtx.createGain();
        gainNode.gain.value = 0;

        source.connect(processor);
        processor.connect(gainNode);
        gainNode.connect(inputCtx.destination);

        processor.onaudioprocess = (e) => {
          if (socket.readyState !== WebSocket.OPEN) return;
          // Deepgram rejects audio that arrives before it has applied Settings.
          if (!settingsAppliedRef.current) return;

          const inputData = e.inputBuffer.getChannelData(0);
          const ctx = audioContextRef.current;

          if (isAgentAudible()) {
            const rms = rmsOf(inputData);
            const settled = !ctx || ctx.currentTime - agentAudioStartedAtRef.current > AEC_SETTLE_S;

            if (rms < BARGE_RMS_FLOOR) {
              // Clearly not speech — use it to learn how loud the leak-through is.
              echoFloorRef.current = echoFloorRef.current
                ? echoFloorRef.current * 0.9 + rms * 0.1
                : rms;
              bargeFramesRef.current = 0;
            } else if (settled && rms > Math.max(BARGE_RMS_FLOOR, echoFloorRef.current * 3)) {
              bargeFramesRef.current += 1;
            } else {
              bargeFramesRef.current = 0;
            }

            if (bargeFramesRef.current < BARGE_FRAMES) {
              // Feed silence rather than nothing: the byte stream is Deepgram's
              // clock, and a gap in it skews endpointing on the next real turn.
              if (!silenceFrameRef.current || silenceFrameRef.current.length !== inputData.length) {
                silenceFrameRef.current = new Int16Array(inputData.length);
              }
              socket.send(silenceFrameRef.current.buffer);
              return;
            }

            // Sustained real speech over the agent — student is cutting in.
            bargeFramesRef.current = 0;
            stopPlayback();
            setStatus('listening');
          }

          const int16Array = new Int16Array(inputData.length);
          for (let i = 0; i < inputData.length; i++) {
            const s = Math.max(-1, Math.min(1, inputData[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          socket.send(int16Array.buffer);
        };

        setIsActive(true);
        setStatus('listening');
      };

      socket.onmessage = (event) => {
        if (typeof event.data === 'string') {
          const msg = JSON.parse(event.data);
          console.log("Deepgram Agent:", msg);
          if (msg.type === 'Error') {
            alert(`Deepgram Agent Error: ${msg.description || msg.message || JSON.stringify(msg)}`);
          } else if (msg.type === 'SettingsApplied') {
            settingsAppliedRef.current = true;
          } else if (msg.type === 'UserStartedSpeaking') {
            stopPlayback();
            setStatus('listening');
            lastRagData.current = {}; // Reset RAG data for new user turn
            hasAddedRagMessage.current = false;
          } else if (msg.type === 'AgentStartedSpeaking') {
            setStatus('speaking');
            activeAgentMsgId.current = Math.random().toString();
            activeAgentMsgText.current = '';
            activeUserMsgId.current = null;
          } else if (msg.type === 'AgentAudioDone') {
            setStatus('listening');
          } else if (msg.type === 'ConversationText' || msg.type === 'UserText' || msg.type === 'AgentText' || msg.type === 'AgentResponse') {
            const isUser = msg.role === 'user' || msg.type === 'UserText';
            const role = isUser ? 'user' : 'assistant';
            const contentChunk = (msg.content || msg.text || '').trim();
            if (contentChunk) {
              const store = useChatStore.getState();

              if (role === 'assistant') {
                  activeUserMsgId.current = null; // Ensure user bubble is closed when agent speaks
                  // If we already added the full RAG response from backend, skip streaming duplicate
                  if (hasAddedRagMessage.current) return;

                  if (!activeAgentMsgId.current) {
                      activeAgentMsgId.current = Math.random().toString();
                      activeAgentMsgText.current = '';
                  }
                  activeAgentMsgText.current += (activeAgentMsgText.current ? ' ' : '') + contentChunk;
                  const exists = store.messages().find(m => m.id === activeAgentMsgId.current);
                  if (exists) {
                      store.updateMessage(activeAgentMsgId.current, {
                          content: activeAgentMsgText.current,
                          confidenceMode: lastRagData.current.confidenceMode,
                          confidenceScore: lastRagData.current.confidenceScore,
                      });
                  } else {
                      store.addMessage({
                          id: activeAgentMsgId.current,
                          role: 'assistant',
                          content: activeAgentMsgText.current,
                          createdAt: new Date().toISOString(),
                          isVoice: true,
                          confidenceMode: lastRagData.current.confidenceMode,
                          confidenceScore: lastRagData.current.confidenceScore,
                      });
                  }
              } else {
                  activeAgentMsgId.current = null; // Ensure agent bubble is closed when user speaks

                  if (!activeUserMsgId.current) {
                      activeUserMsgId.current = Math.random().toString();
                      activeUserMsgText.current = '';
                  }
                  activeUserMsgText.current += (activeUserMsgText.current ? ' ' : '') + contentChunk;

                  const exists = store.messages().find(m => m.id === activeUserMsgId.current);
                  if (exists) {
                      store.updateMessage(activeUserMsgId.current, { content: activeUserMsgText.current });
                  } else {
                      store.addMessage({
                          id: activeUserMsgId.current,
                          role: 'user',
                          content: activeUserMsgText.current,
                          createdAt: new Date().toISOString(),
                          isVoice: true,
                      });
                  }
              }
            }
          } else if (msg.type === 'FunctionCallRequest') {
            console.log("Deepgram Function Call:", msg);

            /* Deepgram v1 wraps the call in a `functions[]` array with keys
               `id` / `name` / `arguments` (arguments is a JSON string). Older
               builds sent `function_name` / `function_id` / `input` at the top
               level; read both shapes so we don't wedge on either. */
            const call = Array.isArray(msg.functions) && msg.functions[0]
              ? msg.functions[0]
              : msg;
            const fnName: string | undefined = call.name || call.function_name;
            const fnId: string | undefined = call.id || call.function_id;
            const rawArgs = call.arguments ?? call.input;
            let query = '';
            try {
                const inputArgs = typeof rawArgs === 'string' ? JSON.parse(rawArgs) : rawArgs;
                query = inputArgs?.query || '';
            } catch (e) {
                query = typeof rawArgs === 'string' ? rawArgs : '';
            }

            /* Response shape Deepgram now expects: {type, id, name, content}.
               Sending the old {function_id, output} keys triggers a hard
               "Text message received from client did not match any of the
               formats we expect." and closes the socket. */
            const sendResponse = (content: string) => {
                if (socket.readyState !== WebSocket.OPEN) return;
                socket.send(JSON.stringify({
                    type: 'FunctionCallResponse',
                    id: fnId,
                    name: fnName,
                    content,
                }));
            };

            if (fnName === 'query_knowledge_base' && query) {
                setStatus('searching...');
                const state = useChatStore.getState();
                const session = state.activeSession();
                const lang = session?.language || 'English';
                const sClass = session?.studentClass || '8th';
                const model = session?.selectedModel || 'ragnous_x1';

                const currentMessages = session?.messages ?? [];
                const history = currentMessages
                  .slice(-6)
                  .map(m => ({ role: m.role, content: m.content }));

                mockChatResponse(query, lang, sClass, model, history)
                    .then((chatMsg) => {
                        hasAddedRagMessage.current = true;

                        // Push full RAG message with confidence & citations directly into chat window
                        state.addMessage({
                            ...chatMsg,
                            isVoice: true
                        });

                        sendResponse(chatMsg.content);
                        setStatus('listening');
                    })
                    .catch(() => {
                        sendResponse('I could not access the knowledge base at this time.');
                        setStatus('listening');
                    });
            } else {
                console.warn("Unhandled or invalid function call:", fnName, query);
                sendResponse("Invalid function call or missing query parameters.");
            }
          }
        } else {
          playAudioChunk(event.data as ArrayBuffer);
        }
      };

      socket.onerror = (e) => {
        console.error('Deepgram Agent Error:', e);
        alert('Live tutor connection failed — is the backend running? Check console.');
        useChatStore.getState().setVoiceActive(false);
        teardown();
        setIsActive(false);
        setStatus('idle');
      };

      socket.onclose = (event) => {
        console.log(`Deepgram closed: Code ${event.code}, Reason: ${event.reason}`);
        if (event.code !== 1000 && event.code !== 1005) {
          alert(`Live tutor disconnected.\nCode: ${event.code}\nReason: ${event.reason || 'Is the backend running with DEEPGRAM_API_KEY set?'}`);
        }
        useChatStore.getState().setVoiceActive(false);
        teardown();
        setIsActive(false);
        setStatus('idle');
      }

    } catch (e) {
      console.error(e);
      alert('Could not start tutor mode.');
      teardown();
      setStatus('idle');
    }
  };

  useEffect(() => {
    return () => { teardown(); };
  }, []);

  return (
    <button
      type="button"
      onClick={toggleTutor}
      className={`icon-btn relative ${isActive ? 'bg-ink text-white hover:bg-[#262626] hover:text-white' : ''}`}
      title={isActive ? `Live tutor — ${status}` : 'Talk to the tutor live'}
      aria-label="Live voice tutor"
      aria-pressed={isActive}
    >
      {isActive && <span className="absolute inset-0 rounded-lg bg-ink/15 animate-ping" />}

      {/* Three bars that stand up while the line is open. */}
      <span className="relative flex items-end justify-center gap-[2.5px] h-4">
        {(isActive ? ['h-2.5', 'h-4', 'h-3'] : ['h-2', 'h-3', 'h-2']).map((h, i) => (
          <span
            key={i}
            className={`w-[2.5px] rounded-full bg-current transition-all duration-300 ${h} ${
              isActive ? 'animate-bounce' : ''
            }`}
            style={{ animationDelay: `${i * 140}ms` }}
          />
        ))}
      </span>
    </button>
  );
};
