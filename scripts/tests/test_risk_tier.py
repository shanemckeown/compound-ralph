"""risk_tier.py (LUCY-z3ykz.1): diff paths -> light|full, fail closed.
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests/test_risk_tier.py -q"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "risk_tier.py"
spec = importlib.util.spec_from_file_location("risk_tier", SCRIPT)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)


@pytest.mark.parametrize("paths", [
    ["components/booking/SlotPicker.tsx"],
    ["components/ui/button.tsx", "styles/globals.css", "content/blog/botox-aftercare.mdx"],
    ["components/ui/__tests__/button.test.tsx", "tests/components/hero.test.tsx"],
    ["public/images/hero.webp", "app/landing.module.css"],
    ["components/ui/card.tsx", "CHANGELOG.md", ".beads/issues.jsonl", "VERSION"],
    # The ratchet baseline moves on every land that changes a line; it must not force full.
    ["components/ui/card.tsx", "tests/meta/file-length-baseline.json"],
])
def test_ui_only_is_light(paths):
    assert rt.classify(paths) == ("light", [])


@pytest.mark.parametrize("path", [
    "lib/x.ts",
    "drizzle/0123_add_thing.sql",
    "drizzle/meta/_journal.json",
    "app/api/foo/route.ts",
    "pages/api/clients/index.ts",
    "scripts/migrate.mjs",
    "jest.config.js",
    "tests/setup/pg-container.ts",
    "package.json",
    "package-lock.json",
    "migrations/0001.sql",
    "public/sw.js",
    "pages/dashboard/index.tsx",
    "weird-new-dir/thing.ts",
    "components/booking/use-slots.ts",
    "components/ui/format.js",
    ".beads/config.yaml",
    "tests/meta/file-length-ratchet.test.ts",
])
def test_non_ui_paths_are_full(path):
    tier, reasons = rt.classify([path])
    assert tier == "full" and len(reasons) == 1 and path in reasons[0]


@pytest.mark.parametrize("path", [
    "components/payments/X.tsx",
    "components/stripe/Checkout.tsx",
    "components/consent/ConsentForm.tsx",
    "components/patients/Card.tsx",
    "components/comms/SmsComposer.tsx",
    "components/settings/EmailTemplates.tsx",
    "components/auth/LoginForm.tsx",
    "components/billing/Plan.tsx",
    "styles/clinical-notes.css",
    # Review round 1: real files that write prices / upload patient photos.
    "components/settings/membership-plan-form.tsx",
    "components/face-mapping/upload-helper.ts",
    "components/pos/VoucherPanel.tsx",
    "components/clients/PhotoGallery.tsx",
])
def test_sensitive_keyword_beats_ui_prefix(path):
    tier, reasons = rt.classify([path])
    assert tier == "full" and "sensitive" in reasons[0]


def test_mixed_ui_and_lib_is_full_and_names_only_the_culprit():
    tier, reasons = rt.classify(["components/ui/button.tsx", "lib/booking/slots.ts"])
    assert tier == "full"
    assert reasons == ["lib/booking/slots.ts: not UI/style/copy (default full)"]


@pytest.mark.parametrize("paths", [[], [""], ["  ", "\n"]])
def test_empty_diff_is_full(paths):
    tier, reasons = rt.classify(paths)
    assert tier == "full" and "empty diff" in reasons[0]


def test_cli_reads_stdin_and_prints_json():
    out = subprocess.run([sys.executable, str(SCRIPT), "--json"], input="components/a.tsx\nlib/b.ts\n",
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    assert data["tier"] == "full" and data["path_count"] == 2


def test_cli_diffs_a_repo_against_base(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "README").write_text("x")
    git("add", "README")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "feature")
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "Hero.tsx").write_text("export {}")
    git("add", "components/Hero.tsx")
    git("commit", "-qm", "ui")
    out = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--base", "main"],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "tier=light"
