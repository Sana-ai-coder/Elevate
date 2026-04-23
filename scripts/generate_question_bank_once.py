"""Generate AI question bank once and optionally enable hourly growth.

Usage:
  python scripts/generate_question_bank_once.py --count 900 --start-hourly --hourly-batch-size 10
"""

from __future__ import annotations

import argparse

from backend.app import create_app
from backend.question_bank_automation import (
    generate_ai_question_batch,
    start_hourly_question_automation,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate AI question bank into DB")
    parser.add_argument("--count", type=int, default=700, help="Number of questions to generate now")
    parser.add_argument("--start-hourly", action="store_true", help="Enable hourly automation after initial generation")
    parser.add_argument("--hourly-batch-size", type=int, default=10, help="Questions per hourly run")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app("development")
    with app.app_context():
        stats = generate_ai_question_batch(
            target_count=max(1, min(int(args.count or 700), 2000)),
            source="manual_script_bootstrap",
            started_by=None,
        )
        print(f"[question-bank] generated={stats.get('generated')} requested={stats.get('requested')} duplicates={stats.get('duplicates')}")
        if stats.get("errors"):
            print(f"[question-bank] errors={stats.get('errors')[:3]}")

        if args.start_hourly:
            state = start_hourly_question_automation(
                started_by=None,
                hourly_batch_size=max(1, min(int(args.hourly_batch_size or 10), 100)),
            )
            print(f"[question-bank] hourly_enabled={state.get('is_enabled')} hourly_batch_size={state.get('hourly_batch_size')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

