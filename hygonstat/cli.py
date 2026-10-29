"""Small CLI using gpustat for terminal formatting only."""

import argparse
import json
import math
import sys
import time

from . import __version__
from .backend import QueryError, new_query


def positive_float(value):
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def indices(value):
    try:
        result = [int(item) for item in value.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError("use comma-separated GPU indices, e.g. 0,1")
    if any(index < 0 for index in result):
        raise argparse.ArgumentTypeError("GPU indices must be non-negative")
    return result


def parser():
    p = argparse.ArgumentParser(description="Read-only Hygon GPU monitor with gpustat-style output")
    p.add_argument("--version", action="version", version="hygonstat " + __version__)
    p.add_argument("-i", "--interval", "--watch", nargs="?", const=1.0,
                   type=positive_float, help="refresh every N seconds (default: 1)")
    p.add_argument("--json", action="store_true", help="JSON; with --watch, one JSON object per line")
    p.add_argument("--id", type=indices, help="SMI device indices, e.g. 0,1")
    p.add_argument("-u", "--show-user", action="store_true")
    p.add_argument("-c", "--show-cmd", action="store_true")
    p.add_argument("-p", "--show-pid", action="store_true")
    p.add_argument("-P", "--show-power", nargs="?", const="draw,limit",
                   choices=("draw", "limit", "draw,limit"))
    p.add_argument("-a", "--show-all", action="store_true",
                   help="show user, command, PID, power and power limit")
    p.add_argument("--no-processes", action="store_true")
    p.add_argument("--no-header", action="store_true")
    colors = p.add_mutually_exclusive_group()
    colors.add_argument("--color", action="store_true")
    colors.add_argument("--no-color", action="store_true")
    p.add_argument("--smi", help="explicit path to a driver-provided hy-smi or rocm-smi")
    p.add_argument("--timeout", type=positive_float, default=10,
                   help="SMI subprocess timeout in seconds (default: 10)")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    previous_warnings = None
    try:
        while True:
            started = time.monotonic()
            stats = new_query(smi=args.smi, id=args.id,
                              processes=not args.no_processes, timeout=args.timeout)
            if args.json:
                data = stats.jsonify()
                data.update(backend="hygon", smi_path=stats.smi_path,
                            memory_unit="MiB", warnings=stats.warnings)
                print(json.dumps(data, default=lambda d: d.isoformat(),
                                 indent=None if args.interval else 2, allow_nan=False), flush=True)
            else:
                redraw = bool(args.interval and sys.stdout.isatty())
                if redraw:
                    sys.stdout.write("\033[H\033[2J")
                stats.print_formatted(
                    fp=sys.stdout, force_color=args.color, no_color=args.no_color,
                    show_user=args.show_user or args.show_all,
                    show_cmd=args.show_cmd or args.show_all,
                    show_pid=args.show_pid or args.show_all,
                    show_power="draw,limit" if args.show_all else args.show_power,
                    show_header=not args.no_header, no_processes=args.no_processes,
                )
                sys.stdout.flush()
                if redraw or stats.warnings != previous_warnings:
                    for warning in stats.warnings:
                        print("hygonstat: " + warning, file=sys.stderr)
                    previous_warnings = stats.warnings
            if args.interval is None:
                return 0
            time.sleep(max(0, args.interval - (time.monotonic() - started)))
    except QueryError as exc:
        print("hygonstat: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
