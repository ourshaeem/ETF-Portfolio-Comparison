"""CLI: python -m etf_pipeline [run|schedule] [--steps extract,transform,...]"""
from __future__ import annotations

import argparse
import logging

from . import pipeline


def main() -> None:
    parser = argparse.ArgumentParser(prog="etf_pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Run the pipeline once")
    run_p.add_argument(
        "--steps",
        default=",".join(pipeline.STEPS),
        help=f"Comma-separated subset of: {', '.join(pipeline.STEPS)}",
    )
    sub.add_parser("schedule", help="Run now, then every weekday after the US market close")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.WARNING)

    if args.command == "run":
        pipeline.run([s.strip() for s in args.steps.split(",") if s.strip()])
    else:
        from .scheduler import start

        start()


if __name__ == "__main__":
    main()
