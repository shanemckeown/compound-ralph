#!/usr/bin/env python3
"""trains.py: LAND runs ride scheduled land trains (LUCY-z3ykz.4).

    trains.py check [--now --reason "<P0 / dated client promise>"] [--at ISO]
    trains.py announce          # launchd: tell the orchestrator a train is open

Finished branches wait for the next train instead of each one taking the land lock on its
own (12 Oct: a lander per bead, 60-90 min each, serialised on one lock). Trains live in
../TRAINS.json: 12:30 (midday) and 18:30 (pre-deploy), Europe/London. A train's window is
when a LAND run may START. The pre-deploy window closes at least 30 min before
`deploy_not_before` (19:30, the /safe-deploy weekday embargo end) so its batch lands before
the evening deploy.

`check` exits 0 inside a window, or outside one with --now plus a non-empty --reason (a P0
or a dated client promise; the reason goes in the run evidence). Otherwise it exits 1 and
names the next train. Output is one JSON line either way.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TRAINS_FILE = Path(__file__).resolve().parent.parent / "TRAINS.json"
RELAY = Path.home() / ".claude" / "scripts" / "relay-to-orchestrator.sh"
LOG = Path.home() / ".claude" / "fleet" / "land-train.log"


def load(path=TRAINS_FILE):
    return json.loads(Path(path).read_text())


def _at(day, hhmm, tz):
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def windows(config, day):
    """[(name, opens, closes)] for the trains on this local date."""
    tz = ZoneInfo(config["timezone"])
    out = []
    for t in config["trains"]:
        opens = _at(day, t["start"], tz)
        out.append((t["name"], opens, opens + timedelta(minutes=t["window_minutes"])))
    return out


def current_train(config, now):
    local = now.astimezone(ZoneInfo(config["timezone"]))
    for name, opens, closes in windows(config, local.date()):
        if opens <= local < closes:
            return name
    return None


def next_train(config, now):
    local = now.astimezone(ZoneInfo(config["timezone"]))
    for offset in (0, 1):
        for name, opens, _ in sorted(windows(config, local.date() + timedelta(days=offset)),
                                     key=lambda w: w[1]):
            if opens > local:
                return name, opens
    return None, None


def check(config, now, land_now=False, reason=None):
    train = current_train(config, now)
    if train:
        return {"allowed": True, "train": train}
    if land_now and (reason or "").strip():
        return {"allowed": True, "train": "now", "reason": reason.strip()}
    name, opens = next_train(config, now)
    why = ("--now needs --reason=\"<P0 or dated client promise>\"" if land_now
           else "outside a land train window")
    return {"allowed": False, "why": why, "next_train": name,
            "next_opens": opens.isoformat() if opens else None}


def announce(config, now):
    train = current_train(config, now)
    if not train:
        return 0
    message = (f"Land train '{train}' is open: run /land-batch now for the finished branches "
               f"queued since the last train (LUCY-z3ykz.4).")
    code = subprocess.run([str(RELAY), message], capture_output=True, text=True).returncode
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"{now.isoformat()} train={train} relay_exit={code}\n")
    except OSError:
        pass
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--now", action="store_true", dest="land_now")
    c.add_argument("--reason")
    c.add_argument("--at", help="ISO timestamp to check instead of the clock (tests, dry runs)")
    sub.add_parser("announce")
    args = ap.parse_args(argv)
    config = load()
    now = datetime.now().astimezone()
    if args.cmd == "announce":
        return announce(config, now)
    if args.at:
        now = datetime.fromisoformat(args.at)
    result = check(config, now, args.land_now, args.reason)
    print(json.dumps(result))
    return 0 if result["allowed"] else 1


if __name__ == "__main__":
    sys.exit(main())
