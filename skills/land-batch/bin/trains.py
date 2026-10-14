#!/usr/bin/env python3
"""trains.py: LAND runs may start any time (LUCY-x0mop, Shane 14 Oct: no clock windows).

    trains.py check [--now] [--reason "<text>"] [--at ISO]
    trains.py announce          # retired no-op (the launchd announce job is gone)

History: LUCY-z3ykz.4 gated LAND starts to 12:30 and 18:30 windows. Retired: a train now
leaves whenever the land lock is free and reviewed branches are waiting; the lock and FIFO
queue (land-state.py) do the batching, and the lander sweeps every PASSed branch ready at
admission.

`check` always prints one JSON line `{"allowed": true, "train": "open"}` and exits 0 for LAND.
`--now`, `--reason` and `--at` are accepted as no-ops for back-compat. A supplied --reason is
echoed back so run evidence still records it. TRAINS.json is retained only for
`deploy_not_before` (the /safe-deploy weekday embargo, which governs DEPLOY, not LAND);
nothing here reads it.
"""
import argparse
import json
import sys
from datetime import datetime


def check(config=None, now=None, land_now=False, reason=None):
    """Always allowed. Arguments are ignored; kept so old callers keep working."""
    result = {"allowed": True, "train": "open"}
    if (reason or "").strip():
        result["reason"] = reason.strip()
    return result


def announce(config=None, now=None, log=None):
    """Retired: no trains to announce."""
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--now", action="store_true", dest="land_now")
    c.add_argument("--reason")
    c.add_argument("--at", help="accepted and ignored (back-compat)")
    sub.add_parser("announce")
    args = ap.parse_args(argv)
    if args.cmd == "announce":
        return announce()
    print(json.dumps(check(None, datetime.now().astimezone(), args.land_now, args.reason)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
