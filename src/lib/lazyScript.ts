/**
 * Loads a third-party script the first time something actually needs it.
 *
 * model-viewer and 3Dmol together are ~2.5 MB and used to sit in index.html as
 * render-blocking tags, so every student paid for them on every page load even
 * though most turns never show a 3D model. They are pulled in here instead,
 * once, when the first 3D artifact appears.
 */

const pending = new Map<string, Promise<void>>();

export const loadScript = (src: string, opts: { module?: boolean } = {}): Promise<void> => {
  const existing = pending.get(src);
  if (existing) return existing;

  const promise = new Promise<void>((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    if (opts.module) el.type = 'module';
    el.async = true;
    el.onload = () => resolve();
    el.onerror = () => {
      // Let a later attempt retry rather than caching the failure forever —
      // the usual cause is a flaky network on a phone, not a bad URL.
      pending.delete(src);
      reject(new Error(`Could not load ${src}`));
    };
    document.head.appendChild(el);
  });

  pending.set(src, promise);
  return promise;
};

/** Waits for a global a script defines, since some set it after onload. */
export const waitForGlobal = async <T,>(
  name: string,
  timeoutMs = 8000
): Promise<T | null> => {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = (window as any)[name];
    if (value) return value as T;
    await new Promise(r => setTimeout(r, 60));
  }
  return null;
};

/** The 3D mesh viewer web component. Registers <model-viewer>. */
export const loadModelViewer = () =>
  loadScript(
    'https://cdn.jsdelivr.net/npm/@google/model-viewer@3.5.0/dist/model-viewer.min.js',
    { module: true }
  );

/**
 * The molecule viewer.
 *
 * Previously fetched from 3Dmol.csb.pitt.edu — a university host with no
 * uptime guarantee — on every page load. jsDelivr is a real CDN.
 */
export const load3Dmol = async () => {
  await loadScript('https://cdn.jsdelivr.net/npm/3dmol@2.1.0/build/3Dmol-min.js');
  return waitForGlobal<any>('$3Dmol');
};
