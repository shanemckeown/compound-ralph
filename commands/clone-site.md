# /clone-site — clone a client's existing website into the Aestheticc builder

Port a paying clinic's live website into our own website builder, faithfully, as **data**. Every
feature their site has that our builder can't yet express becomes a builder feature we BUILD — the
client's real site is the spec, never downgraded to fit (strategy: memory
`project_website_port_as_builder_roadmap`, `Aestheticc/Ops/WEBSITE_PORT_PROCESS.md`). First proven
end-to-end on The Aesthetics Guy NI, 2026-08-12.

**Trigger:** "clone <clinic>'s website", "port <clinic>'s site", "rebuild their site in our builder".

## The shape, in one line
Fetch the real site → map each section to a builder block → build the blocks that don't exist yet →
construct the pages as data in `portal_settings.website_config` → apply to prod with the portal
**disabled** → hand the client the two things only they have (images + exact fonts/hex).

---

## 🔴 Hard rules — these were all learned the expensive way
1. **Data-insert, not UI-driving.** Pages live in `portal_settings.website_config.customPages` (a
   `BlockConfig[]` per page). Write the data via an ops script; never puppet the builder UI. It is
   `portal_settings.website_config`, NOT `business_profiles`.
2. **Codex builds go in an ISOLATED git worktree, and MUST commit.** `/land-batch` (and any
   `reset --hard` in the shared `GitReBase/AestheticcNext` checkout) will silently destroy
   uncommitted in-place edits — it happened, 14k passing tests vanished (`LUCY-3sb80`). Create a
   worktree off `origin/main`, dispatch Codex there, tell it to `git commit` (not `/ship`, no push),
   then you push + rebase + ff-merge from the worktree.
3. **Run the FULL relevant test suite before landing render-layer changes** — a Codex dispatch that
   reports "5 suites passed" ran *focused* tests; page-route/render changes touch every clinic's
   site, so run `tsc` + the broad `components/clinic pages/c lib/clinic` jest lane yourself.
4. **The portal stays `enabled = false`.** Cloning is not going live. Going live is a separate,
   gated step: the redirect map (`qleyn`) must land first or the DNS cutover destroys the clinic's
   existing Google rankings — which is the whole reason they bought the site.
5. **Never carry the old booking link.** Their old buttons point at their incumbent
   (`that-time.co.uk`, Squarespace scheduling, etc.). Replace with the Aestheticc booking action.
6. **Images: ask the client, do not scrape.** `curl` is bot-blocked on most site builders; a login
   or a client export is one message and gets the real assets. Gallery blocks start empty.
7. **Every gap → a builder-feature bead under the port epic.** That's the roadmap; don't paper over
   a gap with a lossy fallback and move on silently.

---

## Phase 1 — Capture the source site (durable)
- `WebFetch` **every page** (curl gets bot-blocked; WebFetch renders). For each page get: section
  order top→bottom, content **verbatim**, and any CTAs with their exact link URLs (especially
  `wa.me/<num>?text=<encoded>` WhatsApp deep-links — clinics live on these).
- Capture **design**: fonts (family names if identifiable), brand colours (accent/primary,
  background, text — hex if you can get it), and any **animated/scrolling** elements (marquee bars,
  carousels). Ask WebFetch explicitly about a "scrolling or moving text bar".
- 🔴 Exact fonts + hex usually need a browser or the client's login — note them as unconfirmed and
  move on; approximate now, refine later. Do not rabbit-hole on scraping CSS.
- Write it all to `Clients/<Name>/website-clone-source.md`: page inventory, verbatim content mapped
  to intended block types, design notes, and an asset list (which images, how many). This is the
  durable spec every later step reads.

## Phase 2 — Map content to builder blocks
Read `components/clinic/blocks/types.ts` + each block's `.tsx` for the **exact config keys**. Current
block catalogue and what each holds:

| Source element | Block | Config notes |
|---|---|---|
| Big banner + headline | `hero` | ONE `description` field (subhead+body merge — a known gap) |
| Scrolling text bar | `announcement` | `{ text, scrolling?: true, bgColor, textColor, speed }` (scrolling added 2026-08-12) |
| Paragraphs / bullet lists / headed sections | `rich_text` / `text` | |
| Feature cards (title+body) | `info_cards` | `config.cards: [{title, body, icon?}]` (arbitrary cards added 2026-08-12); no `cards` = clinic hours/location default |
| FAQs / accordions | `faq` | `config.items: [{question, answer}]` (per-page added 2026-08-12); no `items` = global faqs. Heading is fixed "Frequently Asked Questions" (gap) |
| CTA / button (incl. WhatsApp) | `cta` / `button` | `config.action: "booking"|"link"|"whatsapp"`, `whatsappPhone`, `whatsappMessage` → builds the encoded `wa.me` URL (added 2026-08-12) |
| Image galleries | `gallery` | currently clinic-wide, not per-page (gap); start EMPTY, images come from the client |
| Video | `video` | embed or upload |
| Treatments list | `treatments` | pulls the clinic's real treatments |

Reserved slugs (`about`, `treatments`, `contact`, …) are **built-in routes, not custom pages** —
they render from `website_config` via `lib/reserved-custom-pages.ts` + `reserved-custom-page-content.tsx`.
Custom pages (like a bespoke treatment page) go in `customPages` with a free slug.

## Phase 3 — Gap analysis → beads
List every source element that no block can faithfully hold. File each as a builder-feature bead
under the port epic. Gaps found so far (some now built): WhatsApp CTA ✅, arbitrary info_cards ✅,
per-page faq ✅, scrolling marquee ✅, reserved-page rendering ✅ — still open: **tabs block**,
**global footer settings** (WhatsApp/address/email), **per-page galleries**, **font configuration**
(ClinicTheme has no font field), **hero second description field**, **WhatsApp-button green colour**.

## Phase 4 — Build the gaps (isolated worktree)
For gaps blocking THIS clone:
```bash
cd /Users/shane/Documents/GitReBase/AestheticcNext
git fetch -q origin main
git worktree add .worktrees/codex-<name> -b codex/<name> origin/main
ln -sf $PWD/node_modules .worktrees/codex-<name>/node_modules
# dispatch codex exec -s workspace-write FROM INSIDE the worktree, brief says: build + git commit (no /ship, no push)
```
Then: `tsc --noEmit` + broad clinic jest lane in the worktree → push branch → `git fetch` →
`merge --ff-only origin/codex/<name>` onto main → push main → remove worktree. (Codex's worktree is
a separate clone; its commits aren't in the main checkout's object store until you push+fetch — route
through origin.) Rebase onto current `origin/main` first if main moved (it will), so the branch
carries only its own changes and doesn't revert others' work.

## Phase 5 — Build/extend the clone ops-script
Model on `scripts/ops/chris-flanagan-clone-site.mjs` (reuse it as the template):
- Dry-run default; `--apply` writes and RE-QUERIES to verify.
- Self-resolve `DATABASE_URL` from Secret Manager (copy the `resolveDatabaseUrl()` helper) — zero env setup.
- Resolve the business by name (`ILIKE`), assert exactly one.
- **Scaffold `portal_settings` if missing** (created lazily, often absent — `enabled:false`,
  name-derived unique slug via `slugifyLocationName`, default features).
- Build each page as `BlockConfig[]`; **merge into `customPages`, don't clobber** (replace same-slug,
  keep the rest and every other `website_config` key).
- Set `config.theme` (nearest preset, e.g. `clinical-white`) + `primaryColor` (approx brand hex).
- 🔴 Leave `enabled: false`. Booking CTAs use `action:"booking"`. Galleries empty.

## Phase 6 — Apply + verify
`node scripts/ops/<name>-clone-site.mjs` (dry-run, read it) then `--apply` (allowlisted, no prompt).
Verify by re-query (the script does). **Visual proof** = the authenticated builder preview
(Claude-in-Chrome + login) — NOT enabling the public portal. Public routes 404 until go-live.

## Phase 7 — Hand back to the client + go-live gate
- Ask the client for **images** (login or export) and confirm **exact fonts/brand hex**.
- Go-live is separate and gated: redirect map (`qleyn`) lands → DNS cutover → enable portal. Never
  cut DNS before the redirect map or existing rankings die.

## Record
Update the port epic (`LUCY-ir62t` for the current cohort) and the client's `INDEX.md` with what
landed, what's applied to prod, and what's owed by the client.
