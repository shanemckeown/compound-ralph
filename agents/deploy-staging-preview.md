---
name: deploy-staging-preview
description: Deploy the CURRENT BRANCH to a tagged Cloud Run preview URL on the staging service. Use when shipping a Conductor workspace branch for QA without stomping other parallel previews. Does NOT merge to main and does NOT shift traffic.
model: sonnet
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
hooks:
  Stop:
    - hooks:
        - type: command
          command: osascript -e 'display notification "Preview Deploy Complete" with title "Aestheticc" sound name "Glass"'
---

# Staging Preview Deploy Agent

Deploy the **current branch** in the calling worktree to a tagged preview revision on the staging Cloud Run service. Each preview gets its own URL, so 4 parallel Conductor agents can deploy simultaneously without stomping each other.

Preview URL: `https://<TAG>---aestheticc-next-staging-343806611198.europe-west2.run.app`

**Use this instead of `@deploy-staging` when:**
- Working inside a Conductor workspace (feature branch)
- Want QA on the branch BEFORE merging to main
- Other sessions are also shipping and you don't want to overwrite their staging

**Use `@deploy-staging` (no preview) only when:**
- Shane is batch-deploying `main` after merging approved previews
- Doing a canonical "main staging" sanity check before prod

## Working Directory

This agent runs from the **calling worktree**, NOT the canonical main repo. It deploys whatever branch is checked out in the cwd. Do NOT `cd /Users/shane/Documents/GitReBase/AestheticcNext` — that would deploy main instead of the branch.

```bash
# Verify you are in a worktree on a feature branch, not main
pwd
git rev-parse --abbrev-ref HEAD  # must NOT be "main"
```

If the current branch is `main`, abort and tell the caller to use `@deploy-staging` instead.

## Step-by-Step Process

### 1. Compute the preview tag

Cloud Run tags must match `[a-z]([-a-z0-9]*[a-z0-9])?` and be at most 63 chars. Derive from the branch name:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
TAG=$(echo "$BRANCH" \
  | tr '[:upper:]' '[:lower:]' \
  | sed 's|[^a-z0-9]|-|g' \
  | sed 's|^-*||; s|-*$||' \
  | cut -c1-40)
# Guard: must start with a letter
[[ "$TAG" =~ ^[a-z] ]] || TAG="b-$TAG"
echo "Branch: $BRANCH"
echo "Tag:    $TAG"
echo "URL:    https://${TAG}---aestheticc-next-staging-343806611198.europe-west2.run.app"
```

### 2. Pre-flight checks

```bash
npx tsc --noEmit
npx next lint --fix
npx next lint
npm test
```

If `lint --fix` changed files, commit them on the feature branch:

```bash
git add -A && git commit -m "fix: prettier formatting" && git push
```

### 3. Verify git state

```bash
git status                  # must be clean
git log --oneline -3        # sanity check latest commits
git push                    # ensure remote is up to date
```

### 4. Submit Cloud Build

**CRITICAL:** unset proxy env vars before gcloud.

```bash
unset CLOUDSDK_PROXY_TYPE CLOUDSDK_PROXY_ADDRESS CLOUDSDK_PROXY_PORT

gcloud builds submit \
  --config=cloudbuild-staging-preview.yaml \
  --region=europe-west1 \
  --project=aestheticc \
  --substitutions=_TAG=${TAG} \
  .
```

Run this from the worktree root (uses `.` as the source dir, not the main repo). Wait for completion (timeout: 20 min — builds can stack behind other Cloud Build jobs when multiple previews run in parallel).

### 5. Verify the preview URL

```bash
unset CLOUDSDK_PROXY_TYPE CLOUDSDK_PROXY_ADDRESS CLOUDSDK_PROXY_PORT

# Confirm the tag exists on the service
gcloud run services describe aestheticc-next-staging \
  --region=europe-west2 \
  --project=aestheticc \
  --format="value(status.traffic[].tag)" | tr ';' '\n' | grep -F "$TAG"

# Health check on the preview URL
curl -sf "https://${TAG}---aestheticc-next-staging-343806611198.europe-west2.run.app/api/health" \
  && echo "Preview OK" \
  || echo "Health check failed"
```

### 6. Report

Report back:
- Branch + slugified tag
- Preview URL (full)
- Build ID and status
- Health check result
- Reminder: **do NOT merge to main until Shane approves the preview QA**

## What this agent MUST NOT do

- Do not merge the feature branch to main.
- Do not run `@deploy` (production).
- Do not shift traffic on the staging service (no `--update-traffic`, no removal of `--no-traffic`).
- Do not delete other tagged revisions — they belong to parallel sessions.

## Cleanup (Shane's responsibility, not this agent)

Tagged preview revisions accumulate. Shane prunes them periodically:

```bash
gcloud run services update-traffic aestheticc-next-staging \
  --region=europe-west2 --project=aestheticc \
  --remove-tags=<tag-to-remove>
```

## Troubleshooting

- **Tag rejected by Cloud Run**: Check slug matches `[a-z]([-a-z0-9]*[a-z0-9])?`. Re-run the slug step.
- **`gcloud` proxy error**: Always unset `CLOUDSDK_PROXY_TYPE CLOUDSDK_PROXY_ADDRESS CLOUDSDK_PROXY_PORT`.
- **Build queued forever**: Cloud Build has concurrency limits. Four simultaneous builds is fine; more may queue.
- **Preview URL 404**: The revision may still be starting. Wait 30s and retry the health check.
- **"Current branch is main"**: You are in the canonical repo, not a worktree. Switch into the Conductor workspace and re-run.
