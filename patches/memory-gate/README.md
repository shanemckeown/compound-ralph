# gstack memory gate overlay

`skills/gstack` is an ignored, independently versioned checkout, absent in a
compound-ralph worktree. These patches update both generated and template files
for ship's test section and review. Do not vendor that checkout into this repo.

After integrating this branch, from the compound-ralph root run:

```sh
python3 scripts/install-memory-gate-gstack.py ~/.claude/skills/gstack --check
python3 scripts/install-memory-gate-gstack.py ~/.claude/skills/gstack
```

The installer checks all patches before applying any and recognizes already
installed patches. A context conflict stops for reconciliation; unrelated edits
are preserved. Reapply after a gstack upgrade if it removed the local policy.
The new canonical `scripts/gated-test-run.py` must be present in `~/.claude` first.
The rollout was tested against an isolated copy; live activation belongs to the
integrator, because this task explicitly forbids modifying the live directory.
