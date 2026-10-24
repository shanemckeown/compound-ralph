REVIEWING: e3opf-structured @ b944c4f
FAIL

P2 references/review-lead/review_lead_check.py:830 — Two distinct round-1 findings with different text but the same `P2 lib/x.ts:12`, both graded fixed by the lead, are closed by one `r1 P2 lib/x.ts:12` entry, producing `BLOCKING=0 ESCALATE=0 FIXED=2`.
P2 references/review-lead/review_lead_check.py:432 — A closed prior appearing only in an example enclosed by a four-backtick Markdown fence is treated as authoritative, producing `FIXED=1` even when the actual final verdict block has `prior:[]`.
P2 references/review-lead/review_lead_check.py:436 — Appending a second same-tip response whose final verdict block has `prior:[]` retains the first response’s closed entry and produces `FIXED=1`, despite the final block lacking confirmation.
P2 references/review-lead/review_lead_check.py:763 — Removing the REVIEWING echo from a current-tip seat containing an open prior, while another closing seat has a later mtime, silently excludes the opener and produces `BLOCKING=0 ESCALATE=0 FIXED=1`.
P2 references/review-lead/review_lead_check.py:367 — A finding containing the unexpected key `priority:"P1"` alongside `severity:"P2"` is accepted, and a later matching closure produces `FIXED=1` instead of refusing the malformed finding schema.

## Noted

Missing or null `file_line` also uses the text-location fallback and can reach `FIXED=1`. Unknown prior-entry keys are silently accepted.

Verification exercised `main()` with in-memory inputs and mocked filesystem operations; no files were written. The existing suite creates temporary files, so it was not run.

## VERIFIED OK

1. Closure at an old tip is refused when a higher-round review identifies another tip.
2. Explicit current-tip closed/open disagreement produces `ESCALATE=1 FIXED=0`.
3. Missing prior confirmation refuses a fixed grade.
4. Different severity refuses closure; line-range versus single-line mismatch also refuses.
5. Missing, empty, null, lowercase, and P4 severity are refused.
6. Duplicate JSON keys at top level, in findings, and in prior entries are refused; conflicting statuses across blocks are refused.
7. Prose-only `closed:` confirmation cannot grade fixed.
8. Conflicting or malformed echoes are refused; hand-edited, nested, and appended-block failures are listed above.
9. All eight supplied prose lines refuse fixed grades, both without a structured block and with an empty prior array.

```review-verdict
{"verdict":"FAIL","findings":[{"severity":"P2","file_line":"references/review-lead/review_lead_check.py:830","text":"Two distinct round-1 findings sharing P2 lib/x.ts:12 are both graded fixed by one closed prior reference, yielding BLOCKING=0 ESCALATE=0 FIXED=2; refuse ambiguous references or distinguish finding identities."},{"severity":"P2","file_line":"references/review-lead/review_lead_check.py:432","text":"A closed prior inside a four-backtick Markdown example is consumed as authoritative and yields FIXED=1 even when the actual final block has prior:[]; exclude nested code-fence content."},{"severity":"P2","file_line":"references/review-lead/review_lead_check.py:436","text":"Appending a same-tip final response with prior:[] retains an earlier response's closure and yields FIXED=1; require closure confirmation in the authoritative final block."},{"severity":"P2","file_line":"references/review-lead/review_lead_check.py:763","text":"Deleting the REVIEWING echo from a non-newest current-tip seat containing an open prior silently excludes its disagreement and yields BLOCKING=0 ESCALATE=0 FIXED=1; refuse prior-bearing reviews whose commit is unknown."},{"severity":"P2","file_line":"references/review-lead/review_lead_check.py:367","text":"A finding with severity:P2 and unexpected priority:P1 is accepted and can subsequently yield FIXED=1 instead of schema refusal; enforce the exact finding key set."}]}
```