#!/usr/bin/env node
// State file: ~/.aestheticc-ops/sentry-state.json
// {
//   "issues": {
//     "<sentry_issue_id>": {
//       "title": "...",
//       "lastCount": 14,
//       "lastSeen": "2026-05-12T07:25:00Z",
//       "firstObserved": "2026-04-29T07:25:00Z",
//       "lastTriagedAt": "2026-05-12T07:25:00Z"
//     }
//   },
//   "lastRun": "2026-05-12T06:25:00Z",
//   "lastDispatched": ["<issue_id>", "..."]
// }

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const DRY_RUN = process.env.SENTRY_TRIAGE_DRY_RUN === '1';
const FORCE_RUN = process.env.SENTRY_TRIAGE_FORCE === '1';
const HOME = process.env.HOME || os.homedir();
const STATE_FILE = process.env.STATE_FILE || path.join(HOME, '.aestheticc-ops', 'sentry-state.json');
const CLAUDE_BIN = '/Users/shane/.local/bin/claude';
const REPO = '/Users/shane/Documents/GitReBase/AestheticcNext';
// Scope the sentry fetch to ONLY the aestheticc-ops MCP server so the spawn does not
// boot (and hang on) the full local MCP stack (chrome/gbrain/etc.). Fixes the 300s
// ETIMEDOUT that silently broke sentry triage (LUCY-0ecs, 2026-06-27).
const SENTRY_MCP_CONFIG = path.join(HOME, '.claude', 'hooks', 'night-batch', 'sentry-triage-mcp.json');
const MAX_DISPATCH = 3;
const IDEMPOTENCY_WINDOW_MS = 6 * 60 * 60 * 1000;

function emptyState() {
  return { issues: {}, lastRun: null, lastDispatched: [] };
}

function readState() {
  try {
    const parsed = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    return {
      issues: parsed && typeof parsed.issues === 'object' && parsed.issues !== null ? parsed.issues : {},
      lastRun: parsed?.lastRun || null,
      lastDispatched: Array.isArray(parsed?.lastDispatched) ? parsed.lastDispatched : [],
    };
  } catch {
    return emptyState();
  }
}

function writeStateAtomic(state) {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  const tmp = `${STATE_FILE}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(state, null, 2)}\n`, 'utf8');
  fs.renameSync(tmp, STATE_FILE);
}

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function stripJsonFence(raw) {
  const trimmed = String(raw || '').trim();
  const match = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return match ? match[1].trim() : trimmed;
}

function normaliseIssue(issue) {
  return {
    id: String(issue.id || '').trim(),
    title: String(issue.title || '').trim(),
    count: Number.isFinite(Number(issue.count)) ? Number(issue.count) : 0,
    firstSeen: String(issue.firstSeen || '').trim(),
    lastSeen: String(issue.lastSeen || '').trim(),
    where: String(issue.where || '').trim(),
    level: String(issue.level || '').trim(),
  };
}

function mockIssues(state) {
  const now = new Date();
  const firstSeen = new Date(now.getTime() - 2 * 60 * 60 * 1000).toISOString();
  const lastSeen = now.toISOString();
  state.issues['DRY-SPIKE-1'] = {
    title: 'DryRun spiking',
    lastCount: 10,
    lastSeen: new Date(now.getTime() - 60 * 60 * 1000).toISOString(),
    firstObserved: new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString(),
    lastTriagedAt: null,
  };
  return [
    { id: 'DRY-NEW-1', title: 'DryRun new issue', count: 5, firstSeen, lastSeen, where: 'api/test', level: 'error' },
    { id: 'DRY-SPIKE-1', title: 'DryRun spiking', count: 50, firstSeen, lastSeen, where: 'api/test', level: 'error' },
    { id: 'DRY-NEW-2', title: 'DryRun new issue 2', count: 30, firstSeen, lastSeen, where: 'api/test', level: 'error' },
    { id: 'DRY-NEW-3', title: 'DryRun new issue 3', count: 25, firstSeen, lastSeen, where: 'api/test', level: 'error' },
    { id: 'DRY-NEW-4', title: 'DryRun new issue 4', count: 20, firstSeen, lastSeen, where: 'api/test', level: 'error' },
  ];
}

function fetchSentryIssues() {
  if (DRY_RUN) {
    return { issues: null, error: null };
  }

  const prompt = [
    'Call mcp__aestheticc-ops__get_sentry_errors with hours=24 limit=30.',
    'Return ONLY a JSON array of objects with fields:',
    '{id, title, count, firstSeen, lastSeen, where, level}.',
    'No prose. No markdown fence.',
  ].join(' ');

  const result = spawnSync(CLAUDE_BIN, ['--dangerously-skip-permissions', '--strict-mcp-config', '--mcp-config', SENTRY_MCP_CONFIG, '-p', prompt], {
    encoding: 'utf8',
    timeout: 300_000,
  });

  if (result.error) {
    return { issues: null, error: result.error.message };
  }
  if (result.status !== 0) {
    const stderr = String(result.stderr || '').trim().slice(0, 300);
    return { issues: null, error: `claude exited ${result.status}${stderr ? `: ${stderr}` : ''}` };
  }

  try {
    const parsed = JSON.parse(stripJsonFence(result.stdout));
    if (!Array.isArray(parsed)) {
      return { issues: null, error: 'claude returned non-array JSON' };
    }
    return { issues: parsed.map(normaliseIssue).filter((issue) => issue.id), error: null };
  } catch (error) {
    const excerpt = String(result.stdout || '').trim().slice(0, 300);
    return { issues: null, error: `${error.message}${excerpt ? `; stdout=${excerpt}` : ''}` };
  }
}

function codexAudit(issue) {
  const brief = [
    `Sentry ${issue.id}: ${issue.title} at ${issue.where}. ${issue.count} hits since ${issue.firstSeen}, last ${issue.lastSeen}.`,
    'Find caller pattern, gating, polling/retry config, severity. Recommend fix. Read-only. Under 300 words.',
  ].join('\n');
  const safeId = issue.id.replace(/[^A-Za-z0-9_.-]/g, '_');
  const outPath = `/tmp/codex-sentry-${safeId}.txt`;

  try {
    fs.rmSync(outPath, { force: true });
  } catch {
    // Non-fatal; spawn below will overwrite through codex if possible.
  }

  const result = spawnSync('codex', ['exec', '--cd', REPO, '-s', 'read-only', '-o', outPath, '-'], {
    input: brief,
    encoding: 'utf8',
    timeout: 300_000,
  });

  const exitCode = result.status ?? 1;
  let body = '';
  try {
    body = fs.readFileSync(outPath, 'utf8').trim();
  } catch {
    body = '';
  }

  if (exitCode !== 0 || !body) {
    const stderr = String(result.error?.message || result.stderr || '').trim().slice(0, 300);
    console.error(`codex audit failed for ${issue.id} exit=${exitCode}${stderr ? `: ${stderr}` : ''}`);
    return { snippet: `(codex audit failed exit=${exitCode})`, exitCode };
  }

  const lines = body.split(/\r?\n/).slice(0, 10).join('\n');
  return { snippet: lines.slice(0, 800), exitCode };
}

function classifyIssues(issues, state) {
  const classifications = { new: [], spiking: [], stale_count: 0 };
  const candidates = [];

  for (const issue of issues) {
    const previous = state.issues[issue.id];
    if (!previous) {
      issue.classification = 'NEW';
      classifications.new.push(issue.id);
      candidates.push(issue);
      continue;
    }

    if (issue.count - Number(previous.lastCount || 0) > 20) {
      issue.classification = 'SPIKING';
      classifications.spiking.push(issue.id);
      candidates.push(issue);
      continue;
    }

    issue.classification = 'STALE';
    classifications.stale_count += 1;
  }

  return { candidates, classifications };
}

function buildSlackText({ dispatched, newCount, spikeCount, skipped }) {
  if (newCount + spikeCount === 0) {
    return '';
  }

  const lines = [
    `*Sentry triage* — ${new Date().toISOString()}`,
    `NEW: ${newCount} · SPIKING: ${spikeCount} · dispatched: ${dispatched.length}/${MAX_DISPATCH}`,
  ];

  for (const issue of dispatched) {
    lines.push(`• Sentry ${issue.id} (${issue.classification}, ${issue.count} hits): ${issue.title}`);
    const snippet = issue.auditSnippet || '(no audit output)';
    for (const line of snippet.split(/\r?\n/)) {
      lines.push(`> ${line}`);
    }
  }

  if (skipped.length > 0) {
    lines.push(`_(${skipped.length} more NEW/SPIKING issues skipped due to cap-of-3)_`);
  }

  return lines.join('\n');
}

const state = readState();

if (!DRY_RUN && !FORCE_RUN && state.lastRun) {
  const lastRunMs = Date.parse(state.lastRun);
  if (!Number.isNaN(lastRunMs)) {
    const elapsedMs = Date.now() - lastRunMs;
    if (elapsedMs >= 0 && elapsedMs < IDEMPOTENCY_WINDOW_MS) {
      emit({
        slack_text: '',
        dispatched: 0,
        health: 'SKIP_IDEMPOTENT',
        reason: `ran ${Math.floor(elapsedMs / 60000)}m ago`,
      });
      process.exit(0);
    }
  }
}

let issues;
if (DRY_RUN) {
  issues = mockIssues(state).map(normaliseIssue).filter((issue) => issue.id);
} else {
  const fetched = fetchSentryIssues();
  if (fetched.error) {
    emit({
      slack_text: `*Sentry triage* — MCP_FAIL\nCould not fetch Sentry issues: ${fetched.error.slice(0, 500)}`,
      dispatched: 0,
      health: 'MCP_FAIL',
    });
    process.exit(0);
  }
  issues = fetched.issues;
}

const { candidates, classifications } = classifyIssues(issues, state);
const sorted = [...candidates].sort((a, b) => b.count - a.count);
const top = sorted.slice(0, MAX_DISPATCH);
const skipped = sorted.slice(MAX_DISPATCH).map((issue) => ({
  id: issue.id,
  classification: issue.classification,
  count: issue.count,
  title: issue.title,
}));
const dispatched = [];

for (const issue of top) {
  if (DRY_RUN) {
    issue.auditSnippet = '(dry run: codex audit skipped)';
  } else {
    const audit = codexAudit(issue);
    issue.auditSnippet = audit.snippet;
  }
  dispatched.push(issue);
}

const nowIso = new Date().toISOString();
const dispatchedIds = dispatched.map((issue) => issue.id);
const dispatchedSet = new Set(dispatchedIds);

for (const issue of issues) {
  const previous = state.issues[issue.id] || {};
  state.issues[issue.id] = {
    title: issue.title,
    lastCount: issue.count,
    lastSeen: issue.lastSeen,
    firstObserved: previous.firstObserved || issue.firstSeen || nowIso,
    lastTriagedAt: dispatchedSet.has(issue.id) ? nowIso : previous.lastTriagedAt || null,
  };
}

state.lastRun = nowIso;
state.lastDispatched = dispatchedIds;

if (!DRY_RUN) {
  writeStateAtomic(state);
}

const slackText = buildSlackText({
  dispatched,
  newCount: classifications.new.length,
  spikeCount: classifications.spiking.length,
  skipped,
});

emit({
  slack_text: slackText,
  dispatched: dispatched.length,
  dispatched_ids: dispatchedIds,
  classifications,
  skipped,
  health: 'OK',
});
