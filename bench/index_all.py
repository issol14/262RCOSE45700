"""Index every benchmark PDF once (cached under data/index)."""
import sys
import time

from lb import index
from bench.sets import doc_ids, pdf_path

force = "--force" in sys.argv
t0 = time.time()
pages = 0
for d in doc_ids():
    if index.exists(d) and not force:
        continue
    t = time.time()
    index.build(d, pdf_path(d))
    meta = index.load(d).meta
    pages += meta["pages"]
    print(f"{d} pages={meta['pages']} chunks={meta['chunks']} {time.time()-t:.1f}s", flush=True)
print(f"done {pages} pages in {time.time()-t0:.1f}s")
