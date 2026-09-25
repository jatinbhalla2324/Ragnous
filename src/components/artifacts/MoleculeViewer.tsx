import { useEffect, useRef, useState } from 'react';
import { load3Dmol } from '../../lib/lazyScript';

export interface MoleculeData {
  source: 'cid' | 'pdb';
  id: string;
  /** False when PubChem holds only a flat 2D depiction of this compound. */
  has3d?: boolean;
  topic?: string;
}

type Style = 'ball' | 'space' | 'stick';

const STYLE_LABEL: Record<Style, string> = {
  ball: 'Ball and stick',
  space: 'Space filling',
  stick: 'Stick',
};

/** CPK colouring, the convention every NCERT textbook uses. */
const ELEMENT_COLORS: Record<string, string> = {
  H: '#F5F5F5', C: '#3B3B3B', N: '#3050F8', O: '#E63946',
  S: '#E6C229', P: '#FF8000', Cl: '#3EA055', Na: '#AB5CF2',
  F: '#90E050', Br: '#A62929', I: '#940094', Fe: '#E06633',
};

const MoleculeViewer = ({ data }: { data: MoleculeData }) => {
  const mountRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const [style, setStyle] = useState<Style>('ball');
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [elements, setElements] = useState<string[]>([]);

  // ── Create the viewer once, and destroy it on unmount ───────────────────
  /**
   * The previous version never released the viewer. Each molecule in a thread
   * created another WebGL context and clearing innerHTML does not free one, so
   * a long chemistry session eventually hit the browser's ~16-context ceiling
   * and every canvas on the page went blank.
   */
  useEffect(() => {
    let cancelled = false;

    const start = async () => {
      const $3Dmol = await load3Dmol();
      const mount = mountRef.current;
      if (cancelled || !$3Dmol || !mount) {
        if (!cancelled) { setFailed(true); setLoading(false); }
        return;
      }

      mount.innerHTML = '';
      const viewer = $3Dmol.createViewer(mount, { backgroundColor: '#FFFFFF' });
      viewerRef.current = viewer;

      const query = data.source === 'cid' ? `cid:${data.id}` : `pdb:${data.id}`;
      $3Dmol.download(query, viewer, {}, () => {
        if (cancelled) return;
        try {
          const atoms = viewer.getModel()?.selectedAtoms({}) || [];
          setElements([...new Set(atoms.map((a: any) => a.elem).filter(Boolean))] as string[]);
          applyStyle(viewer, data.source === 'pdb' ? 'stick' : 'ball', data.source);
          viewer.zoomTo();
          viewer.render();
          setLoading(false);
        } catch {
          setFailed(true);
          setLoading(false);
        }
      });

      // 3Dmol sizes its canvas once, so a viewer built inside a chat bubble
      // that later grows (or goes full width on rotate) stays the old size.
      const ro = new ResizeObserver(() => {
        try { viewer.resize(); viewer.render(); } catch { /* torn down */ }
      });
      ro.observe(mount);
      (viewer as any).__ro = ro;
    };

    start();

    return () => {
      cancelled = true;
      const viewer = viewerRef.current;
      if (viewer) {
        try { (viewer as any).__ro?.disconnect(); } catch { /* ignore */ }
        // removeAllModels + clear releases the GL context in 3Dmol 2.x.
        try { viewer.removeAllModels(); } catch { /* ignore */ }
        try { viewer.clear(); } catch { /* ignore */ }
        viewerRef.current = null;
      }
      if (mountRef.current) mountRef.current.innerHTML = '';
    };
  }, [data.id, data.source]);

  const applyStyle = (viewer: any, next: Style, source: string) => {
    if (source === 'pdb' && next === 'stick') {
      viewer.setStyle({}, { cartoon: { color: 'spectrum' } });
      return;
    }
    if (next === 'ball') viewer.setStyle({}, { stick: { radius: 0.13 }, sphere: { scale: 0.28 } });
    else if (next === 'space') viewer.setStyle({}, { sphere: { scale: 0.95 } });
    else viewer.setStyle({}, { stick: { radius: 0.18 } });
  };

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || loading) return;
    try {
      applyStyle(viewer, style, data.source);
      viewer.render();
    } catch { /* torn down */ }
  }, [style, loading, data.source]);

  return (
    <div className="w-full bg-canvas">
      <div className="relative w-full h-[380px]">
        <div ref={mountRef} className="absolute inset-0" />

        {loading && !failed && (
          <div className="absolute inset-0 grid place-items-center bg-canvas">
            <p className="text-[0.6875rem] text-ink-3">Loading the molecule…</p>
          </div>
        )}

        {failed && (
          <div className="absolute inset-0 grid place-items-center bg-canvas px-6 text-center">
            <p className="text-[0.8125rem] text-ink-2">
              This molecule could not be loaded.
            </p>
          </div>
        )}

        {!loading && !failed && (
          <div className="absolute top-2.5 right-2.5 flex items-center gap-1">
            {(['ball', 'space', 'stick'] as Style[]).map(s => (
              <button
                key={s}
                onClick={() => setStyle(s)}
                className={`mv-btn !w-auto px-2 text-[0.625rem] font-medium ${
                  style === s ? '!bg-ink !text-white !border-ink' : ''
                }`}
                title={STYLE_LABEL[s]}
              >
                {STYLE_LABEL[s]}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* A legend beats floating text labels: it never overlaps the model and
          it teaches the CPK convention the textbook uses. */}
      {!loading && !failed && elements.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 pb-3">
          {elements.map(el => (
            <span key={el} className="flex items-center gap-1.5">
              <span
                className="w-2.5 h-2.5 rounded-full border border-line-strong"
                style={{ background: ELEMENT_COLORS[el] || '#8E8E8E' }}
              />
              <span className="text-[0.6875rem] text-ink-2">{el}</span>
            </span>
          ))}
        </div>
      )}

      {/* PubChem quietly serves the flat 2D depiction for compounds that have
          no 3D conformer, which would otherwise be presented as a 3D model. */}
      {data.has3d === false && (
        <p className="px-4 pb-3 text-[0.6875rem] text-ink-3">
          PubChem has no 3D conformer for this compound, so this is its flat
          structural formula rather than a true 3D shape.
        </p>
      )}
    </div>
  );
};

export default MoleculeViewer;
