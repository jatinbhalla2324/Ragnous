"""Crop every NCERT figure out of the books on disk, ahead of time.

Figure extraction parses a chapter's vector drawings, which takes a few seconds
per chapter and occasionally much longer (jesc108 measured 85s). That cannot sit
on the notes request path, so run this once after ingesting books — and again
whenever data/books changes:

    python -m scripts.prewarm_figures            # every chapter on disk
    python -m scripts.prewarm_figures jesc106    # just these
    python -m scripts.prewarm_figures --force    # re-crop, ignoring manifests

Results land in data/figures/<chapter>/ with a manifest.json each; the notes
endpoint only ever reads those.
"""

import glob
import sys
import time
from pathlib import Path

from app.services import ncert_figure_service as figures


def main(argv):
    force = "--force" in argv
    codes = [a.lower() for a in argv if not a.startswith("-")]
    if not codes:
        codes = sorted({
            Path(p).stem.lower()
            for p in glob.glob(str(figures.UNPACK_DIR / "**" / "*.pdf"), recursive=True)
        })
        # Touch the index first so zips are unpacked before the glob above is
        # meaningful on a fresh checkout.
        if not codes:
            figures.chapter_pdf("_warm_")
            codes = sorted({
                Path(p).stem.lower()
                for p in glob.glob(str(figures.UNPACK_DIR / "**" / "*.pdf"), recursive=True)
            })

    total, started = 0, time.time()
    for code in codes:
        t0 = time.time()
        figs = figures.figures_for_chapter(code, force=force)
        total += len(figs)
        print(f"{code}: {len(figs)} figures ({time.time() - t0:.1f}s)")
    print(f"\n{total} figures from {len(codes)} chapters in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1:])
