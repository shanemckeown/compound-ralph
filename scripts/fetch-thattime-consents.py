#!/usr/bin/env python3
"""fetch-thattime-consents.py — pull a clinic's signed consent PDFs out of That Time Pro.

Why this exists: when a clinic closes its That Time Pro account the signed consent
PDFs go with it. Those are clinical-legal records. For Chris Flanagan that is 421
forms (125 Botulinum consents, 28 dermal filler, 165 medical forms, and the rest)
referenced only by URL inside 649-all-medical-history-forms.csv.

🔴 The vault recorded this as needing Shane and the client live at the same moment
for 2FA — "the one thing that cannot be done async". Measured 2026-08-12: the PDF
URLs under api.that-time.co.uk/storage/uploads/forms/ answer 200 with no auth at
all. It is a plain unauthenticated fetch. (Which is also a finding about That Time
Pro's security, noted separately — those URLs are guessable-ish and unprotected.)

Design notes, all of them tonight's lesson applied:
- Resumable. Skips files already on disk, so a rerun is free and safe.
- Rate limited. 421 requests at someone else's expense; be polite.
- COUNTED. Reports downloaded / skipped / failed against a denominator, and exits
  non-zero if anything failed. A partial success must never read as success.
- Verifies content-type and size per file; an HTML error page saved as .pdf is the
  exact silent failure this whole codebase keeps producing.
- Writes outside both git repos (client PII), per the existing migrations convention.

Usage:
  fetch-thattime-consents.py <forms.csv> <outdir> [--limit N] [--dry-run]
"""
import csv, os, sys, time, urllib.request, urllib.error, json, re

DELAY = 0.4          # be polite to a third party's server
MIN_PDF_BYTES = 1000 # anything smaller is an error page, not a consent form


def safe_name(row, url):
    """Stable, collision-free, and readable. Keeps the source filename as the key."""
    src = url.rstrip("/").split("/")[-1] or "form.pdf"
    who = re.sub(r"[^A-Za-z0-9 '-]", "", row.get("user_name", "") or "unknown").strip()[:40]
    tpl = re.sub(r"[^A-Za-z0-9 '-]", "", row.get("form_template_name", "") or "form").strip()[:45]
    date = (row.get("created_at", "") or "")[:10]
    return f"{date} · {who} · {tpl} · {src}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) < 2:
        print(__doc__); sys.exit(2)
    csv_path, outdir = args[0], args[1]
    dry = "--dry-run" in flags
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    rows = [r for r in csv.DictReader(open(csv_path)) if (r.get("pdf_full_path") or "").strip()]
    if limit:
        rows = rows[:limit]
    os.makedirs(outdir, exist_ok=True)

    total = len(rows)
    done = skipped = failed = 0
    failures, manifest = [], []

    for i, row in enumerate(rows, 1):
        url = row["pdf_full_path"].strip()
        name = safe_name(row, url)
        dest = os.path.join(outdir, name)

        if os.path.exists(dest) and os.path.getsize(dest) > MIN_PDF_BYTES:
            skipped += 1
            manifest.append({"file": name, "url": url, "status": "already-present"})
            continue
        if dry:
            print(f"[{i}/{total}] WOULD FETCH  {name}")
            continue

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Aestheticc-migration/1.0"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read()
            # Verify the ARTIFACT, not the request. A 200 that returns an HTML error
            # page is the failure mode that looks like success.
            if "pdf" not in ctype.lower() or len(body) < MIN_PDF_BYTES or body[:4] != b"%PDF":
                raise ValueError(f"not a pdf (type={ctype}, bytes={len(body)}, magic={body[:4]!r})")
            with open(dest, "wb") as fh:
                fh.write(body)
            done += 1
            manifest.append({"file": name, "url": url, "status": "downloaded", "bytes": len(body)})
            if done % 25 == 0:
                print(f"  … {done} downloaded, {i}/{total} processed")
        except Exception as e:
            failed += 1
            failures.append({"url": url, "name": name, "error": str(e)})
            manifest.append({"file": name, "url": url, "status": "FAILED", "error": str(e)})
        time.sleep(DELAY)

    if not dry:
        with open(os.path.join(outdir, "_manifest.json"), "w") as fh:
            json.dump({"total": total, "downloaded": done, "already_present": skipped,
                       "failed": failed, "entries": manifest}, fh, indent=2)

    print(f"\n=== {done} downloaded · {skipped} already present · {failed} FAILED · of {total} referenced ===")
    if failures:
        print("FAILURES (these consent forms were NOT recovered):")
        for f in failures[:20]:
            print(f"  {f['name']}\n     {f['error']}")
        sys.exit(1)
    if done + skipped != total:
        print(f"🔴 accounted for {done + skipped} of {total} — do not report this as complete")
        sys.exit(1)


if __name__ == "__main__":
    main()
