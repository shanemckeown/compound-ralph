---
name: deploy-staging
description: Deploy Aestheticc to STAGING Cloud Run. Runs pre-flight checks, submits Cloud Build, monitors until complete.
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
          command: osascript -e 'display notification "Staging Deploy Complete" with title "Aestheticc" sound name "Glass"'
---

# Staging Deploy Agent

Deploy Aestheticc to **staging** (`aestheticc-next-staging-343806611198.europe-west2.run.app`) via Google Cloud Build.

## Codebase Location
`/Users/shane/Documents/GitReBase/AestheticcNext`

ALL commands must run from this directory.

## Step-by-Step Process

### 1. Pre-Flight Checks (ALL must pass before submitting)

```bash
cd /Users/shane/Documents/GitReBase/AestheticcNext

# Type check
npx tsc --noEmit

# Lint (fix any prettier issues automatically)
npx next lint --fix
# Then verify lint passes clean
npx next lint

# Run test suite — ALL tests must pass
npm test
```

If lint `--fix` changed files, commit them:
```bash
git add -A && git commit -m "fix: prettier formatting" && git push
```

### 2. Verify Git State

```bash
# Ensure everything is committed and pushed
git status
git log --oneline -3
```

- There must be NO uncommitted changes
- Local must be up to date with remote (`git push` if needed)

### 3. Submit Cloud Build

**CRITICAL:** You MUST unset proxy env vars that Claude Code's sandbox injects — they break gcloud.

```bash
unset CLOUDSDK_PROXY_TYPE CLOUDSDK_PROXY_ADDRESS CLOUDSDK_PROXY_PORT

gcloud builds submit \
  --config=cloudbuild-staging.yaml \
  --region=europe-west1 \
  --project=aestheticc \
  /Users/shane/Documents/GitReBase/AestheticcNext
```

This command uploads the source and starts the build. It will stream logs. **Wait for it to complete** (timeout: 15 min).

### 4. Verify Deployment

After the build succeeds:

```bash
unset CLOUDSDK_PROXY_TYPE CLOUDSDK_PROXY_ADDRESS CLOUDSDK_PROXY_PORT

# Check the Cloud Run service is serving the new revision
gcloud run services describe aestheticc-next-staging \
  --region=europe-west2 \
  --project=aestheticc \
  --format="value(status.url, status.traffic[0].revisionName)"
```

Then health check:
```bash
curl -sf https://aestheticc-next-staging-343806611198.europe-west2.run.app/api/health && echo "Staging OK" || echo "Health check failed"
```

### 5. Report Results

Report back:
- Build ID and status
- New revision name
- Health check result
- Any warnings or issues

## Cloud Build Config
- File: `cloudbuild-staging.yaml`
- Image tag: `:staging`
- Cloud Run service: `aestheticc-next-staging`
- Build region: `europe-west1`
- Run region: `europe-west2`
- Machine: `E2_HIGHCPU_8`

## Troubleshooting

- **gcloud proxy error**: Always `unset CLOUDSDK_PROXY_TYPE CLOUDSDK_PROXY_ADDRESS CLOUDSDK_PROXY_PORT` before any gcloud command
- **OOM during build**: Verify `cloudbuild-staging.yaml` has `machineType: E2_HIGHCPU_8`
- **Lint failures**: Run `npx next lint --fix` first, then re-check
- **Type errors**: Fix them before deploying — never skip type checks
