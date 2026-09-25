
import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';
import { Download, ExternalLink, FileText, Mail, Play, Youtube } from 'lucide-react';
import { ArtifactPayload } from '../../types/artifact';
import { useNotesStore } from '../../store/notesStore';

const Model3DViewer = lazy(() => import('./Model3DViewer'));
const MoleculeViewer = lazy(() => import('./MoleculeViewer'));

const ViewerSkeleton = () => (
  <div className="w-full h-[380px] bg-surface grid place-items-center">
    <p className="text-[0.6875rem] text-ink-3">Preparing the 3D view…</p>
  </div>
);

// Old physics simulator removed in favor of new Widget3D pipeline

// ── Mermaid theme init ───────────────────────────────────────────────────────
mermaid.initialize({ 
  startOnLoad: false, 
  theme: 'base',
  themeVariables: {
    fontFamily: 'Inter, sans-serif',
    background: '#FFFFFF',
    primaryColor: '#F4F4F4',
    primaryTextColor: '#0D0D0D',
    primaryBorderColor: '#D9D9D9',
    lineColor: '#8E8E8E',
    secondaryColor: '#F9F9F9',
    tertiaryColor: '#FFFFFF',
  }
});

// ── Mermaid Flowchart Renderer ───────────────────────────────────────────────
const MermaidRenderer = ({ data }: { data: any }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current && data) {
      const id = 'mermaid-svg-' + Math.random().toString(36).substring(7);
      mermaid.render(id, data)
        .then(({ svg }) => {
          if (containerRef.current) containerRef.current.innerHTML = svg;
        })
        .catch(() => {
          if (containerRef.current)
            containerRef.current.innerHTML = `<div class="text-[0.8125rem] text-[#5D5D5D] p-4">This diagram could not be drawn.</div>`;
        });
    }
  }, [data]);

  const handleDownload = () => {
    if (!containerRef.current) return;
    const svgElement = containerRef.current.querySelector('svg');
    if (!svgElement) return;
    if (!svgElement.getAttribute('xmlns')) svgElement.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    const svgData = new XMLSerializer().serializeToString(svgElement);
    const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'ragnous-flowchart.svg';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="relative w-full min-h-[280px] flex flex-col items-center justify-center p-5 bg-surface group">
      <button
        onClick={handleDownload}
        className="absolute top-3 right-3 grid place-items-center w-8 h-8 rounded-lg bg-canvas border border-line text-ink-2 hover:text-ink hover:border-line-strong opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-all"
        title="Download diagram"
        aria-label="Download diagram"
      >
        <Download className="w-4 h-4" strokeWidth={1.8} />
      </button>
      <div ref={containerRef} className="w-full flex items-center justify-center overflow-auto" />
    </div>
  );
};

// ── 3D Widget Renderer ───────────────────────────────────────────────────────
//
// The viewers themselves live in Model3DViewer / MoleculeViewer, which are
// loaded lazily: model-viewer and 3Dmol are ~2.5MB between them and most
// answers carry no 3D at all.

const Widget3DRenderer = ({ data }: { data: any }) => {
  if (!data) return null;

  if (data.type === 'gltf') {
    return (
      <Suspense fallback={<ViewerSkeleton />}>
        <Model3DViewer data={data} />
      </Suspense>
    );
  }

  if (data.type === 'molecule') {
    return (
      <Suspense fallback={<ViewerSkeleton />}>
        <MoleculeViewer data={data} />
      </Suspense>
    );
  }

  // The Sketchfab embed needs no download allowance, so it is what a student
  // gets when the GLB could not be cached. It is interactive, just not ours.
  if (data.type === 'sketchfab') {
    return (
      <div className="w-full h-[400px] bg-surface overflow-hidden relative">
        <iframe
          title="3D model"
          frameBorder="0"
          allowFullScreen
          allow="autoplay; fullscreen; xr-spatial-tracking"
          loading="lazy"
          src={`https://sketchfab.com/models/${data.id}/embed?autostart=0&ui_theme=light&ui_infos=0&ui_watermark=0&ui_controls=1&ui_animations=0&transparent=1`}
          className="w-full h-full"
        />
      </div>
    );
  }

  return null;
};

// ── YouTube Lesson Renderer ──────────────────────────────────────────────────

interface LessonVideo {
  id: string;
  title: string;
  channel?: string;
  thumbnail?: string;
  duration?: string;
  url?: string;
  /** False when the backend could not confirm the video is embeddable. */
  verified?: boolean;
}

interface YouTubePayload {
  topic?: string;
  /** "confused" when the student said they were stuck, "requested" otherwise. */
  reason?: 'confused' | 'requested' | string;
  videos: LessonVideo[];
}

/**
 * Plays the lesson inside the chat.
 *
 * The iframe is mounted only after the student presses play — a thumbnail
 * facade first. Three reasons: the answer often carries a video the student
 * never watches, an idle YouTube iframe is ~1MB of script and cookies each,
 * and several of these can pile up in one long tutoring thread.
 */
const YouTubeRenderer = ({ data }: { data: YouTubePayload }) => {
  const videos = data?.videos ?? [];
  const [activeIndex, setActiveIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [thumbFailed, setThumbFailed] = useState(false);

  if (!videos.length) return null;

  const active = videos[activeIndex];
  const watchUrl = active.url || `https://www.youtube.com/watch?v=${active.id}`;

  const pick = (i: number) => {
    setActiveIndex(i);
    // Autoplay the newly chosen lesson: switching is itself a play intent.
    setPlaying(true);
    setThumbFailed(false);
  };

  return (
    <div className="w-full bg-canvas p-4 flex flex-col gap-3">
      <div className="flex items-center gap-2 text-[0.75rem] text-ink-3">
        <Youtube className="w-3.5 h-3.5 text-ink-2" strokeWidth={1.9} />
        {data.reason === 'confused' ? 'Another way to see it' : 'Video lesson'}
        {data.topic && <span className="text-ink-4">· {data.topic}</span>}
      </div>

      <div className="relative w-full aspect-video rounded-xl overflow-hidden border border-line bg-ink">
        {playing ? (
          <iframe
            key={active.id}
            title={active.title}
            // youtube-nocookie: no tracking cookie is set unless the student
            // actually watches. `origin` keeps the embed API happy on strict hosts.
            src={`https://www.youtube-nocookie.com/embed/${active.id}?autoplay=1&rel=0&modestbranding=1&playsinline=1&origin=${encodeURIComponent(
              window.location.origin
            )}`}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
            className="w-full h-full"
            frameBorder="0"
          />
        ) : (
          <button
            onClick={() => setPlaying(true)}
            className="group/play absolute inset-0 w-full h-full flex items-center justify-center"
            aria-label={`Play: ${active.title}`}
          >
            {!thumbFailed && (
              <img
                src={active.thumbnail || `https://i.ytimg.com/vi/${active.id}/mqdefault.jpg`}
                alt=""
                onError={() => setThumbFailed(true)}
                className="absolute inset-0 w-full h-full object-cover opacity-70 group-hover/play:opacity-90 group-hover/play:scale-[1.03] transition-all duration-500"
              />
            )}
            <span className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/25 to-transparent" />

            <span className="relative w-14 h-14 rounded-full bg-white/95 grid place-items-center shadow-pop group-hover/play:scale-110 transition-transform">
              <Play className="w-6 h-6 text-ink translate-x-[2px]" fill="currentColor" />
            </span>

            <span className="absolute left-4 right-4 bottom-3 text-left">
              <span className="block text-[0.8125rem] font-semibold text-white leading-snug clamp-2">
                {active.title}
              </span>
              <span className="mt-0.5 flex items-center gap-2 text-[0.6875rem] text-white/60">
                {active.channel}
                {active.duration && (
                  <>
                    <span className="text-white/20">·</span>
                    <span className="tabular-nums">{active.duration}</span>
                  </>
                )}
              </span>
            </span>
          </button>
        )}
      </div>

      {/* Alternates. A single result gets no picker. */}
      {videos.length > 1 && (
        <div className="flex flex-col gap-1">
          {videos.map((video, i) =>
            i === activeIndex ? null : (
              <button
                key={video.id}
                onClick={() => pick(i)}
                className="group flex items-center gap-3 p-1.5 pr-3 rounded-xl hover:bg-surface-2 transition-colors text-left"
              >
                <span className="relative w-[68px] h-[38px] shrink-0 rounded-lg overflow-hidden bg-surface-3">
                  <img
                    src={video.thumbnail || `https://i.ytimg.com/vi/${video.id}/mqdefault.jpg`}
                    alt=""
                    className="w-full h-full object-cover transition-opacity"
                  />
                  <span className="absolute inset-0 grid place-items-center bg-ink/30 opacity-0 group-hover:opacity-100 transition-opacity">
                    <Play className="w-3.5 h-3.5 text-white" fill="currentColor" />
                  </span>
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[0.75rem] text-ink-2 group-hover:text-ink leading-snug clamp-2">
                    {video.title}
                  </span>
                  <span className="text-[0.6875rem] text-ink-4">
                    {video.channel}
                    {video.duration ? ` · ${video.duration}` : ''}
                  </span>
                </span>
              </button>
            )
          )}
        </div>
      )}

      {/* Some videos disallow embedding, and the scrape path cannot tell us in
          advance — so the way out is always visible, not a hidden fallback. */}
      <a
        href={watchUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="self-start flex items-center gap-1.5 text-[0.75rem] text-ink-3 hover:text-ink transition-colors"
      >
        <ExternalLink className="w-3 h-3" strokeWidth={1.8} />
        {active.verified === false ? "Won't play? Open on YouTube" : 'Open on YouTube'}
      </a>
    </div>
  );
};

// ── Notes PDF Renderer ────────────────────────────────────────────────────────
/* The PDF is written on demand from the NCERT chapters behind this topic, not
   from the transcript, so it takes a little while — the button says so. */
const NotesRenderer = ({ onAction }: { onAction?: (action: string) => void }) => {
  const exporting = useNotesStore(state => state.exporting);

  return (
  <div className="w-full p-6 bg-canvas flex flex-col items-center text-center gap-4">
    <span className="grid place-items-center w-11 h-11 rounded-xl bg-surface-2">
      <FileText className="w-5 h-5 text-ink-2" strokeWidth={1.7} />
    </span>

    <div>
      <h3 className="text-[0.9375rem] font-semibold text-ink mb-1">Make study notes on this</h3>
      <p className="text-[0.8125rem] text-ink-3 max-w-sm mx-auto leading-relaxed">
        A detailed PDF module built from your NCERT chapters — explanations, key
        terms, textbook diagrams, exam tips and practice questions.
      </p>
    </div>

    <div className="flex flex-wrap gap-2 justify-center">
      <button
        onClick={() => onAction?.('download_pdf')}
        disabled={exporting}
        className="btn btn-primary"
      >
        <Download className="w-4 h-4" strokeWidth={1.8} />
        {exporting ? 'Writing notes…' : 'Download PDF'}
      </button>
      <button
        className="btn btn-secondary"
        disabled
        title="Email delivery is not connected yet"
      >
        <Mail className="w-4 h-4" strokeWidth={1.8} />
        Email it
      </button>
    </div>
  </div>
  );
};

// ── Artifact Router ──────────────────────────────────────────────────────────
export const ArtifactRenderer = ({ payload, onAction }: { payload: ArtifactPayload, onAction?: (action: string) => void }) => {
  switch (payload.type) {
    case 'notes':
      return <NotesRenderer onAction={onAction} />;
    case 'mermaid':
      return <MermaidRenderer data={payload.data} />;
    case 'widget_3d':
      return <Widget3DRenderer data={payload.data} />;
    case 'youtube':
      return <YouTubeRenderer data={payload.data} />;
    case 'image':
      return (
        <div className="w-full p-2 bg-surface">
          <img src={payload.data as string} alt="" className="w-full object-cover rounded-lg" />
        </div>
      );
    default:
      return (
        <div className="p-4 text-[0.8125rem] text-ink-3">
          This attachment type is not supported yet ({payload.type}).
        </div>
      );
  }
};
