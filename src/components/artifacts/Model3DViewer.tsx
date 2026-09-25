import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Download, Maximize2, Minimize2, RotateCcw, Play, Pause, Tag, Layers } from 'lucide-react';
import { loadModelViewer } from '../../lib/lazyScript';
import { API_BASE } from '../../api/config';

export interface PartLabel {
  name: string;
  note?: string;
  /** Direction from the model centre, each axis in -1..1. */
  offset: [number, number, number];
  /** Diagram colour for this part, chosen by the backend. */
  color?: string;
}

export interface Model3DData {
  url: string;
  labels?: PartLabel[];
  credit?: string;
  /** True when the mesh came from text-to-3D rather than a real scan. */
  generated?: boolean;
  topic?: string;
}

/** Cached meshes are served from the API host as /media/..., not from Vite. */
const resolveModelUrl = (url: string) => {
  if (!url || /^(https?:|blob:|data:)/.test(url)) return url;
  const origin = API_BASE.replace(/\/api\/v1\/?$/, '');
  return `${origin}${url.startsWith('/') ? '' : '/'}${url}`;
};

/**
 * Fallback colours for a mesh that carries no texture of its own.
 *
 * Text-to-3D output is always untextured, and a fair number of free scanned
 * models are too, which is why a "human heart" could arrive as a plain white
 * lump. Nothing can recover real anatomy from that, but a mesh split across
 * several materials can at least be given one distinct colour per material,
 * and a single-material mesh gets a subject-appropriate tint instead of white.
 */
/** For a mesh with one material: a muted tissue tone, not a signal colour. */
const NEUTRAL_TINT = '#C1897E';

const MATERIAL_PALETTE = [
  '#C05A54', '#4A7FB5', '#5A9367', '#B8894A',
  '#7B6BA8', '#4A9096', '#B5705A', '#6B7280',
];

const hexToLinearRgba = (hex: string): [number, number, number, number] => {
  const value = hex.replace('#', '');
  const srgb = [0, 2, 4].map(i => parseInt(value.slice(i, i + 2), 16) / 255);
  // glTF baseColorFactor is linear; feeding it sRGB makes everything look
  // washed out and slightly glowing.
  const linear = srgb.map(c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return [linear[0], linear[1], linear[2], 1];
};

type Placement = { x: number; y: number; visible: boolean };
type LabelBox = { x: number; y: number; side: 'left' | 'right' };

const GUTTER = 12;
const LABEL_W = 164;
// Tall enough for a name plus a two-line note; the de-overlap below spaces
// the gutters by this, so it has to match what the chip actually renders.
const ROW_H = 64;

const Model3DViewer = ({ data }: { data: Model3DData }) => {
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const frameRef = useRef<number>(0);
  const signatureRef = useRef<string>('');
  // Base colour + alpha mode of each material as the file shipped it, so
  // see-through mode can be switched back off without reloading the mesh.
  const originalMaterialsRef = useRef<{ factor: number[]; alphaMode: string }[]>([]);
  const xrayRef = useRef(false);

  const [ready, setReady] = useState(false);       // web component registered
  const [loaded, setLoaded] = useState(false);     // mesh finished loading
  const [failed, setFailed] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [attempt, setAttempt] = useState(0);       // bumped by Retry

  const [spinning, setSpinning] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [compact, setCompact] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [xray, setXray] = useState(false);
  xrayRef.current = xray;

  const [placements, setPlacements] = useState<Placement[]>([]);
  const [anchors, setAnchors] = useState<({ position: string; normal?: string } | null)[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });

  const labels = useMemo(() => (data.labels ?? []).slice(0, 6), [data.labels]);
  const src = useMemo(() => resolveModelUrl(data.url), [data.url]);

  // ── Load the web component on demand ────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    loadModelViewer()
      .then(() => !cancelled && setReady(true))
      .catch(() => !cancelled && setFailed('The 3D viewer could not be loaded.'));
    return () => { cancelled = true; };
  }, []);

  // ── Mesh load / progress / failure ──────────────────────────────────────
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !ready) return;

    const onProgress = (e: any) => setProgress(Math.round((e.detail?.totalProgress ?? 0) * 100));
    const onLoad = () => { setLoaded(true); setFailed(null); };
    const onError = () =>
      setFailed('This model could not be loaded. It may no longer be available.');

    viewer.addEventListener('progress', onProgress);
    viewer.addEventListener('load', onLoad);
    viewer.addEventListener('error', onError);

    // A mesh already in the browser cache finishes loading before this effect
    // runs, so the event never arrives and the skeleton would cover a model
    // that is sitting there fully loaded.
    if (viewer.loaded) onLoad();

    return () => {
      viewer.removeEventListener('progress', onProgress);
      viewer.removeEventListener('load', onLoad);
      viewer.removeEventListener('error', onError);
    };
  }, [ready, attempt]);

  // ── Colour an untextured mesh, then remember how it ended up ────────────
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !loaded) return;

    /** Snapshot the visible material state so see-through mode is reversible. */
    const captureOriginals = (materials: any[]) => {
      originalMaterialsRef.current = materials.map((m: any) => {
        let factor: number[] = [1, 1, 1, 1];
        let alphaMode = 'OPAQUE';
        try { factor = [...(m.pbrMetallicRoughness?.baseColorFactor ?? factor)]; } catch { /* older build */ }
        try { alphaMode = m.getAlphaMode?.() ?? 'OPAQUE'; } catch { /* older build */ }
        return { factor, alphaMode };
      });
    };

    try {
      const materials = viewer.model?.materials ?? [];
      if (!materials.length) return;

      const textured = materials.some((m: any) => {
        try { return !!m.pbrMetallicRoughness?.baseColorTexture?.texture; }
        catch { return false; }
      });

      // A mesh can carry real colour without carrying a texture: a diagram
      // model of a cell or a volcano is often a dozen flat-shaded materials,
      // each already the right colour for its part. Repainting those from our
      // palette threw that away and turned a red magma chamber an arbitrary
      // hue, so only a mesh whose materials are all white or grey — nothing
      // to lose — gets tinted.
      const hasOwnColour = materials.some((m: any) => {
        try {
          const [r, g, b] = m.pbrMetallicRoughness?.baseColorFactor ?? [1, 1, 1, 1];
          return Math.max(r, g, b) - Math.min(r, g, b) > 0.06;
        } catch { return false; }
      });

      // A real scanned model already looks right; repainting it would destroy
      // exactly the detail we routed to Sketchfab to get.
      if (!textured && !hasOwnColour) {
        // A mesh split across several materials can be read part by part, so
        // each one takes a label colour. A single-material mesh cannot — every
        // part would share one hue — so it gets a neutral tissue tone instead.
        // Painting it the first label's colour turned a whole brain signal-red.
        const perPart = materials.length > 1 && labels.length >= materials.length;
        const tints = perPart
          ? labels.map(l => l.color).filter(Boolean) as string[]
          : [];

        materials.forEach((material: any, i: number) => {
          const hex = tints[i]
            ?? (materials.length === 1 ? NEUTRAL_TINT : MATERIAL_PALETTE[i % MATERIAL_PALETTE.length]);
          const pbr = material.pbrMetallicRoughness;
          try { pbr.setBaseColorFactor(hexToLinearRgba(hex)); } catch { /* older build */ }
          // Teaching models read better matte: a metallic sheen hides contour.
          try { pbr.setMetallicFactor(0.05); } catch { /* not supported */ }
          try { pbr.setRoughnessFactor(0.8); } catch { /* not supported */ }
        });
      }

      captureOriginals(materials);
    } catch (e) {
      console.warn('[3D] could not recolour the mesh', e);
    }
  }, [loaded, labels]);

  // ── See-through mode ────────────────────────────────────────────────────
  /**
   * Drops every material to partial opacity so the student can see the
   * chambers, cavities and inner parts a solid surface hides — the thing a
   * printed diagram gets for free by being a cross-section.
   *
   * Alpha multiplies the base colour, so this works on a textured scan as
   * well as on a mesh we tinted ourselves. Models with no interior geometry
   * simply turn to glass, which still reveals the markers pinned behind them.
   */
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !loaded) return;

    const materials = viewer.model?.materials ?? [];
    const originals = originalMaterialsRef.current;
    if (!materials.length || !originals.length) return;

    materials.forEach((material: any, i: number) => {
      const original = originals[i] ?? { factor: [1, 1, 1, 1], alphaMode: 'OPAQUE' };
      const [r, g, b] = original.factor;
      try {
        if (xray) {
          material.setAlphaMode('BLEND');
          material.pbrMetallicRoughness.setBaseColorFactor([r, g, b, 0.38]);
          // Without both faces the inside of a hollow model reads as a hole.
          material.setDoubleSided?.(true);
        } else {
          material.setAlphaMode(original.alphaMode);
          material.pbrMetallicRoughness.setBaseColorFactor(original.factor);
        }
      } catch { /* material API not available in this build */ }
    });
  }, [xray, loaded]);

  // ── Project the hotspots into the overlay every frame that matters ──────
  const measure = useCallback((): string => {
    const viewer = viewerRef.current;
    const wrap = wrapRef.current;
    if (!viewer || !wrap || !labels.length) return '';

    const wrapRect = wrap.getBoundingClientRect();
    const next: Placement[] = labels.map((_, i) => {
      const el = viewer.querySelector(`[slot="hotspot-${i}"]`) as HTMLElement | null;
      if (!el) return { x: 0, y: 0, visible: false };
      const r = el.getBoundingClientRect();
      return {
        x: r.left + r.width / 2 - wrapRect.left,
        y: r.top + r.height / 2 - wrapRect.top,
        // model-viewer toggles data-visible (see data-visibility-attribute)
        // once the surface the marker sits on faces away from the camera.
        // In see-through mode the student can see the far side, so a marker
        // being behind the surface is no longer a reason to hide its label.
        visible: xrayRef.current || el.hasAttribute('data-visible'),
      };
    });

    const signature = JSON.stringify(next) + `|${Math.round(wrapRect.width)}`;
    // Re-measuring is cheap; re-rendering is not. Only publish a change.
    if (signature !== signatureRef.current) {
      signatureRef.current = signature;
      setPlacements(next);
      setSize({ w: wrapRect.width, h: wrapRect.height });
    }
    return signature;
  }, [labels]);

  const scheduleMeasure = useCallback(() => {
    // A hidden or throttled tab never runs requestAnimationFrame, which would
    // leave the leader lines pointing at wherever the markers were when the
    // student last looked. Measure straight away in that case.
    if (typeof document !== 'undefined' && document.hidden) {
      measure();
      return;
    }
    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(measure);
  }, [measure]);

  // ── Anchor the labels to the mesh ───────────────────────────────────────
  /**
   * The backend knows where on the object a part sits ("back-bottom"), never
   * the mesh's real coordinates — those differ per model. Rather than trusting
   * a fraction of the bounding box (which leaves markers floating in the air
   * beside the model), a ray is cast at the screen position the anchor implies
   * and the marker is pinned to whatever surface it hits, with that surface's
   * normal. The normal is what lets model-viewer hide a marker once the model
   * turns away from it.
   *
   * The result is kept in state and rendered as data-position/data-normal
   * rather than pushed in with updateHotspot(): those are the attributes
   * model-viewer reads declaratively, so they survive a React re-render.
   * Setting them imperatively meant the very next render reset every marker
   * to the model's origin and stacked all six labels on one point.
   */
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !loaded || !labels.length) return;

    let cancelled = false;
    let tries = 0;

    const attempt = () => {
      if (cancelled) return;
      const rect = viewer.getBoundingClientRect();

      let centre: any = null;
      let dims: any = null;
      try {
        centre = viewer.getBoundingBoxCenter?.();
        dims = viewer.getDimensions?.();
      } catch { /* not framed yet */ }

      const next: ({ position: string; normal?: string } | null)[] = labels.map(label => {
        const [ox, oy, oz] = label.offset || [0, 0, 0];

        // Walk inward from the anchor direction until the ray hits the mesh.
        // These are CLIENT coordinates: model-viewer measures against the
        // viewport, and element-relative values miss the model entirely and
        // return null for every point.
        let hit: any = null;
        for (const reach of [0.38, 0.3, 0.22, 0.12, 0.0]) {
          const px = rect.left + rect.width / 2 + ox * rect.width * reach;
          const py = rect.top + rect.height / 2 - oy * rect.height * reach;
          try {
            hit = viewer.positionAndNormalFromPoint(px, py);
          } catch { hit = null; }
          if (hit) break;
        }

        if (hit) {
          let { x, y, z } = hit.position;
          const n = hit.normal;
          let nz = n.z;

          // A ray only ever finds the surface facing the camera, so a part the
          // student is told sits at the back ("cerebellum: back-bottom") would
          // be pinned to the front. Mirroring the hit through the bounding box
          // puts it on the far side, where its own normal then hides it until
          // the model is turned round.
          if (oz < -0.2 && centre) {
            z = 2 * centre.z - z;
            nz = -nz;
          }
          return { position: `${x}m ${y}m ${z}m`, normal: `${n.x} ${n.y} ${nz}` };
        }

        // Nothing under any of those points (a hollow or very thin model):
        // fall back to the bounding box so the label still has a home.
        if (centre && dims) {
          return {
            position:
              `${centre.x + ox * dims.x * 0.5}m ` +
              `${centre.y + oy * dims.y * 0.5}m ` +
              `${centre.z + oz * dims.z * 0.5}m`,
          };
        }
        return null;
      });

      // The element is still being sized (the gutters shrink it right after
      // mount), so nothing was under any ray. Try again next frame.
      if (next.every(a => a === null) && tries < 20) {
        tries += 1;
        requestAnimationFrame(attempt);
        return;
      }
      setAnchors(next);
    };

    const id = requestAnimationFrame(() => requestAnimationFrame(attempt));
    return () => { cancelled = true; cancelAnimationFrame(id); };
  }, [loaded, labels]);

  // model-viewer only reads data-position when a hotspot is first registered,
  // so the attributes above set the initial value and this pushes every later
  // change through. Keeping both means a re-render cannot lose the anchors and
  // an update is never ignored.
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !anchors.length) return;

    anchors.forEach((anchor, i) => {
      if (!anchor) return;
      try {
        viewer.updateHotspot({
          name: `hotspot-${i}`,
          position: anchor.position,
          ...(anchor.normal ? { normal: anchor.normal } : {}),
        });
      } catch { /* hotspot not registered yet */ }
    });

    measure();
  }, [anchors, measure]);

  // model-viewer moves its markers on its own render tick, and emits no event
  // for it: the delay is unbounded when the tab is throttled or in the
  // background, so neither a fixed set of timers nor a settle-and-stop poll is
  // enough — both latch onto the pre-anchor positions and leave every leader
  // line converging on one point. Re-measuring on a slow interval is the only
  // reading that cannot go stale, and it costs a handful of rect reads that
  // publish no state unless something actually moved.
  useEffect(() => {
    if (!loaded || !labels.length) return;
    const id = window.setInterval(measure, 250);
    return () => clearInterval(id);
  }, [loaded, labels, measure]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !loaded) return;

    viewer.addEventListener('camera-change', scheduleMeasure);
    document.addEventListener('visibilitychange', scheduleMeasure);
    scheduleMeasure();

    const ro = new ResizeObserver(() => {
      setCompact((wrapRef.current?.clientWidth ?? 0) < 560);
      scheduleMeasure();
    });
    if (wrapRef.current) ro.observe(wrapRef.current);

    return () => {
      viewer.removeEventListener('camera-change', scheduleMeasure);
      document.removeEventListener('visibilitychange', scheduleMeasure);
      ro.disconnect();
      cancelAnimationFrame(frameRef.current);
    };
  }, [loaded, scheduleMeasure]);

  useLayoutEffect(() => {
    setCompact((wrapRef.current?.clientWidth ?? 0) < 560);
  }, []);

  // ── Lay the labels out in gutters without letting them overlap ──────────
  const boxes = useMemo<LabelBox[]>(() => {
    if (compact || !size.w || !placements.length) return [];

    const mid = size.w / 2;

    // Parts on the midline — a brain's lobes, a volcano's vent — all project
    // to the same x, so a plain "left of centre?" test dumps every one of them
    // into the same gutter. Only those are dealt out alternately. Anything even
    // slightly off-centre keeps its own side: dealing it to the far gutter made
    // its leader line cross the model and meet a neighbour's at the same
    // height, so a heart's two ventricles read as swapped.
    const onMidline = size.w * 0.02;
    let flip = 0;
    const rows = placements.map((p, i) => {
      let side: 'left' | 'right';
      if (Math.abs(p.x - mid) < onMidline) {
        side = flip++ % 2 === 0 ? 'left' : 'right';
      } else {
        side = p.x < mid ? 'left' : 'right';
      }
      return { i, side, y: p.y };
    });

    const out: LabelBox[] = new Array(placements.length).fill(null).map(() => ({
      x: 0, y: 0, side: 'left' as const,
    }));

    (['left', 'right'] as const).forEach(side => {
      const column = rows.filter(r => r.side === side).sort((a, b) => a.y - b.y);
      // Push each row below the previous one, then lift the whole column if it
      // has run past the bottom edge — two labels sharing a y is the normal
      // case (a heart's atria sit at the same height), not the exception.
      let cursor = -Infinity;
      column.forEach(row => {
        const y = Math.max(row.y, cursor + ROW_H);
        cursor = y;
        out[row.i] = {
          side,
          x: side === 'left' ? GUTTER : size.w - GUTTER - LABEL_W,
          y,
        };
      });
      const overflow = cursor + ROW_H / 2 - size.h;
      if (overflow > 0) column.forEach(row => { out[row.i].y -= overflow; });
    });

    return out;
  }, [placements, size, compact]);

  // ── Controls ────────────────────────────────────────────────────────────
  const resetView = () => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    viewer.cameraOrbit = viewer.getAttribute('camera-orbit') || '0deg 75deg auto';
    viewer.fieldOfView = 'auto';
    viewer.cameraTarget = 'auto auto auto';
    setActive(null);
  };

  /** Swing the camera round to look at one part, the way a teacher would. */
  const focusPart = (index: number) => {
    const viewer = viewerRef.current;
    setActive(prev => (prev === index ? null : index));
    if (!viewer || !labels[index]) return;
    const [ox, oy] = labels[index].offset || [0, 0, 0];
    // Azimuth from the part's left/right offset, elevation from up/down.
    const theta = Math.round(ox * 60);
    const phi = Math.round(75 - oy * 35);
    try { viewer.cameraOrbit = `${theta}deg ${phi}deg auto`; } catch { /* ignore */ }
  };

  const toggleFullscreen = async () => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await wrap.requestFullscreen();
    } catch { /* denied by the browser */ }
  };

  useEffect(() => {
    const onChange = () => {
      setFullscreen(document.fullscreenElement === wrapRef.current);
      scheduleMeasure();
    };
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, [scheduleMeasure]);

  const download = async () => {
    try {
      const res = await fetch(src);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${(data.topic || 'model').replace(/\s+/g, '-')}.glb`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      window.open(src, '_blank', 'noopener');
    }
  };

  const retry = () => {
    setFailed(null);
    setLoaded(false);
    setProgress(0);
    setAttempt(a => a + 1);
  };

  // ── Render ──────────────────────────────────────────────────────────────
  const isMobile = typeof navigator !== 'undefined' && /Android|iPhone|iPad/i.test(navigator.userAgent);

  if (failed) {
    return (
      <div className="w-full h-[300px] bg-surface flex flex-col items-center justify-center gap-3 text-center px-6">
        <p className="text-[0.8125rem] text-ink-2 max-w-xs">{failed}</p>
        <div className="flex gap-2">
          <button onClick={retry} className="btn btn-secondary">Try again</button>
          <a href={src} target="_blank" rel="noopener noreferrer" className="btn btn-quiet">
            Open the file
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-surface">
      <div
        ref={wrapRef}
        className={`mv-stage relative w-full ${fullscreen ? 'h-screen' : 'h-[470px]'}`}
      >
        {ready && (
          <model-viewer
            key={attempt}
            ref={viewerRef}
            src={src}
            alt={data.topic ? `3D model of the ${data.topic}` : '3D model'}
            camera-controls
            bounds="tight"
            loading="eager"
            touch-action="pan-y"
            auto-rotate={spinning || undefined}
            rotation-per-second="18deg"
            camera-orbit="0deg 75deg auto"
            // Stops the student tumbling under the floor and losing the model.
            min-camera-orbit="auto 5deg auto"
            max-camera-orbit="auto 170deg auto"
            interaction-prompt="none"
            shadow-intensity="0.9"
            shadow-softness="1"
            exposure="1"
            tone-mapping="neutral"
            environment-image="neutral"
            ar={isMobile || undefined}
            ar-modes="webxr scene-viewer quick-look"
            style={{
              // In wide mode the model is inset so the label gutters sit on
              // empty background instead of on top of the model itself.
              width: compact ? '100%' : `calc(100% - ${(LABEL_W + GUTTER + 8) * 2}px)`,
              height: '100%',
              margin: '0 auto',
              display: 'block',
              outline: 'none',
              backgroundColor: 'transparent',
            }}
          >
            {labels.map((label, i) => (
              // Only the marker lives on the model; the text sits in the
              // overlay below so it can be laid out without overlapping.
              <button
                key={label.name}
                slot={`hotspot-${i}`}
                type="button"
                data-position={anchors[i]?.position}
                data-normal={anchors[i]?.normal}
                onClick={() => focusPart(i)}
                data-visibility-attribute="visible"
                className="mv-hotspot"
                aria-label={label.name}
              >
                <span
                  className="block w-full h-full rounded-full border-2 border-white transition-transform"
                  style={{
                    background: label.color || '#0D0D0D',
                    transform: active === i ? 'scale(1.45)' : 'scale(1)',
                    boxShadow: '0 1px 4px rgba(0,0,0,.35)',
                  }}
                />
                {compact && (
                  <span className="mv-hotspot-index" style={{ background: label.color || '#0D0D0D' }}>
                    {i + 1}
                  </span>
                )}
              </button>
            ))}

            <div slot="progress-bar" />
          </model-viewer>
        )}

        {/* Leader lines + label chips. Rebuilt whenever the camera moves. */}
        {showLabels && !compact && loaded && boxes.length > 0 && (
          <>
            <svg
              className="absolute inset-0 w-full h-full pointer-events-none"
              width={size.w}
              height={size.h}
            >
              {boxes.map((box, i) => {
                const dot = placements[i];
                if (!box || !dot?.visible) return null;
                const edgeX = box.side === 'left' ? box.x + LABEL_W : box.x;
                const elbowX = box.side === 'left' ? edgeX + 16 : edgeX - 16;
                const colour = labels[i].color || '#8E8E8E';
                const strong = active === null || active === i;
                return (
                  <polyline
                    key={labels[i].name}
                    points={`${dot.x},${dot.y} ${elbowX},${box.y} ${edgeX},${box.y}`}
                    fill="none"
                    stroke={colour}
                    strokeWidth={active === i ? 2 : 1.25}
                    strokeOpacity={strong ? 0.9 : 0.25}
                    strokeLinejoin="round"
                  />
                );
              })}
            </svg>

            {boxes.map((box, i) => {
              const dot = placements[i];
              if (!box || !dot?.visible) return null;
              const label = labels[i];
              const strong = active === null || active === i;
              return (
                <button
                  key={label.name}
                  type="button"
                  onClick={() => focusPart(i)}
                  // Only colour/emphasis may animate. Transitioning position
                  // would make the chip slide to its new spot while its leader
                  // line — an SVG attribute, which does not animate — snaps
                  // there instantly, so the two disagree on every camera move.
                  className="absolute text-left rounded-lg px-2 py-1 bg-canvas border [transition:border-color_140ms_ease,opacity_140ms_ease]"
                  style={{
                    left: box.x,
                    top: box.y - ROW_H / 2 + 2,
                    width: LABEL_W,
                    borderColor: active === i ? label.color || '#0D0D0D' : 'var(--line-strong, #D9D9D9)',
                    opacity: strong ? 1 : 0.4,
                    boxShadow: '0 1px 3px rgba(13,13,13,.06)',
                  }}
                  title={label.note}
                >
                  <span className="flex items-center gap-1.5">
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ background: label.color || '#0D0D0D' }}
                    />
                    <span className="text-[0.6875rem] font-semibold text-ink leading-tight truncate">
                      {label.name}
                    </span>
                  </span>
                  {label.note && (
                    // A name alone only tests recall. What the part does is the
                    // part worth reading, so it is always on the diagram.
                    <span className="mt-0.5 block text-[0.625rem] text-ink-3 leading-snug clamp-3">
                      {label.note}
                    </span>
                  )}
                </button>
              );
            })}
          </>
        )}

        {/* Loading. model-viewer's own poster is not used: a skeleton that
            matches the rest of the app reads better than a grey box. */}
        {!loaded && !failed && (
          <div className="absolute inset-0 grid place-items-center bg-surface">
            <div className="flex flex-col items-center gap-3">
              <div className="w-28 h-1 rounded-full bg-surface-3 overflow-hidden">
                <div
                  className="h-full bg-ink transition-[width] duration-200"
                  style={{ width: `${Math.max(8, progress)}%` }}
                />
              </div>
              <p className="text-[0.6875rem] text-ink-3">Loading the 3D model…</p>
            </div>
          </div>
        )}

        {/* Controls */}
        {loaded && (
          <div className="absolute top-2.5 right-2.5 flex items-center gap-1">
            {labels.length > 0 && (
              <button
                onClick={() => setShowLabels(v => !v)}
                className="mv-btn"
                title={showLabels ? 'Hide labels' : 'Show labels'}
                aria-label={showLabels ? 'Hide labels' : 'Show labels'}
              >
                <Tag className="w-3.5 h-3.5" strokeWidth={1.8} style={{ opacity: showLabels ? 1 : 0.45 }} />
              </button>
            )}
            <button
              onClick={() => setXray(v => !v)}
              className="mv-btn"
              title={xray ? 'Show the solid model' : 'See inside the model'}
              aria-label={xray ? 'Show the solid model' : 'See inside the model'}
              aria-pressed={xray}
            >
              <Layers className="w-3.5 h-3.5" strokeWidth={1.8} style={{ opacity: xray ? 1 : 0.45 }} />
            </button>
            <button
              onClick={() => setSpinning(v => !v)}
              className="mv-btn"
              title={spinning ? 'Stop rotating' : 'Rotate automatically'}
              aria-label={spinning ? 'Stop rotating' : 'Rotate automatically'}
            >
              {spinning ? <Pause className="w-3.5 h-3.5" strokeWidth={1.8} />
                        : <Play className="w-3.5 h-3.5" strokeWidth={1.8} />}
            </button>
            <button onClick={resetView} className="mv-btn" title="Reset the view" aria-label="Reset the view">
              <RotateCcw className="w-3.5 h-3.5" strokeWidth={1.8} />
            </button>
            <button onClick={download} className="mv-btn" title="Download the model" aria-label="Download the model">
              <Download className="w-3.5 h-3.5" strokeWidth={1.8} />
            </button>
            <button
              onClick={toggleFullscreen}
              className="mv-btn"
              title={fullscreen ? 'Exit full screen' : 'Full screen'}
              aria-label={fullscreen ? 'Exit full screen' : 'Full screen'}
            >
              {fullscreen ? <Minimize2 className="w-3.5 h-3.5" strokeWidth={1.8} />
                          : <Maximize2 className="w-3.5 h-3.5" strokeWidth={1.8} />}
            </button>
          </div>
        )}

        {loaded && (
          <p className="absolute bottom-2 left-3 text-[0.625rem] text-ink-4 pointer-events-none">
            Drag to rotate · scroll to zoom
            {data.generated ? ' · AI-generated shape, not a scan' : ''}
          </p>
        )}
      </div>

      {/* On a narrow screen there is no room for gutters, so the model keeps
          numbered markers and the names are listed underneath instead. */}
      {labels.length > 0 && (compact || !loaded) && (
        <ol className="flex flex-col gap-1 px-4 pb-3 pt-1">
          {labels.map((label, i) => (
            <li key={label.name}>
              <button
                onClick={() => focusPart(i)}
                className={`w-full flex items-start gap-2 text-left rounded-lg px-2 py-1.5 transition-colors ${
                  active === i ? 'bg-surface-2' : 'hover:bg-surface-2'
                }`}
              >
                <span
                  className="mt-[3px] grid place-items-center w-4 h-4 shrink-0 rounded-full text-[0.5625rem] font-bold text-white"
                  style={{ background: label.color || '#0D0D0D' }}
                >
                  {i + 1}
                </span>
                <span className="min-w-0">
                  <span className="block text-[0.75rem] font-semibold text-ink leading-tight">
                    {label.name}
                  </span>
                  {label.note && (
                    <span className="block text-[0.6875rem] text-ink-3 leading-snug">{label.note}</span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
};

export default Model3DViewer;
