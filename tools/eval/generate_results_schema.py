"""Regenerate the readable eval schema.

Run: uv run --offline --frozen python -m tools.eval.generate_results_schema

The drift test compares parsed JSON; formatting does not change the schema content.
"""

import json

from tools.eval.grade import Result
from tools.eval.tasks import HERE


def main() -> None:
    (HERE / "results.schema.json").write_text(
        json.dumps(Result.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
