"""Compare an API model with a saved Codex run without crawling or sending email.

Prepare only (no API call):
    .venv/bin/python scripts/evaluate_agent_filter.py
Run the comparison using OPENAI_API_KEY from the environment or --env-file:
    .venv/bin/python scripts/evaluate_agent_filter.py --run
    .venv/bin/python scripts/evaluate_agent_filter.py --model gpt-5-nano --reasoning-effort minimal --run

Results are diagnostic comparisons, not ground-truth accuracy measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent_client import AgentError, OpenAIClient
from agent_filter import AgentJobFilter, DECISION_SCHEMA, SYSTEM_INSTRUCTIONS
from main import _normalize_payload_deadlines

KST = timezone(timedelta(hours=9))
MODEL = "gpt-4o-mini"
MODEL_RATES = {
    "gpt-4o-mini": {"input": "0.15", "cached_input": "0.075", "output": "0.60"},
    "gpt-5-nano": {"input": "0.05", "cached_input": "0.005", "output": "0.40"},
    "gpt-5-mini": {"input": "0.25", "cached_input": "0.025", "output": "2.00"},
    "gpt-6-luna": {"input": "0.10", "cached_input": "0.01", "cache_write": "0.125", "output": "0.50"},
    "gpt-6.1-sol": {"input": "2.00", "cached_input": "0.10", "cache_write": "2.50", "output": "10.00"},
}
MODEL_EFFORTS = {
    "gpt-4o-mini": set(),
    "gpt-5-nano": {"minimal", "low", "medium", "high"},
    "gpt-5-mini": {"minimal", "low", "medium", "high"},
    "gpt-6-luna": {"none", "low", "medium", "high", "xhigh", "max"},
    "gpt-6.1-sol": {"low", "medium", "high", "xhigh", "max"},
}


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def token_cost(usage: dict, model: str = MODEL) -> Decimal:
    rates = MODEL_RATES[model]
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0)
    writes = details.get("cache_write_tokens", 0)
    if writes and "cache_write" not in rates:
        raise ValueError(f"No verified cache-write price for {model}")
    return (
        (usage["input_tokens"] - cached - writes) * Decimal(rates["input"])
        + cached * Decimal(rates["cached_input"])
        + writes * Decimal(rates.get("cache_write", "0"))
        + usage["output_tokens"] * Decimal(rates["output"])
    ) / 1_000_000


def validate_baseline(rows: list[dict], baseline: dict) -> list[dict]:
    if baseline.get("input_count") != len(rows) or len(baseline.get("decisions", [])) != len(rows):
        raise ValueError("Baseline and input must contain the same number of postings")
    decisions = AgentJobFilter._validate_decisions(
        {"decisions": [{key: item[key] for key in ("id", "keep", "reason")}
                       for item in baseline["decisions"]]}, 0, len(rows)
    )
    by_id = {item["id"]: item for item in baseline["decisions"]}
    for row_id, row in enumerate(rows):
        if any(by_id[row_id].get(key) != row.get(key) for key in ("title", "link")):
            raise ValueError(f"Baseline identity mismatch at posting {row_id}")
    return decisions


def compare(rows: list[dict], baseline: list[dict], candidate: list[dict], model: str = MODEL) -> dict:
    disagreements = [
        {"id": i, "posting": row, "codex": old, "candidate": new}
        for i, (row, old, new) in enumerate(zip(rows, baseline, candidate))
        if old["keep"] != new["keep"]
    ]
    return {
        "candidate_model": model,
        "posting_count": len(rows),
        "agreement_count": len(rows) - len(disagreements),
        "agreement_fraction": (len(rows) - len(disagreements)) / len(rows) if rows else None,
        "codex_kept": sum(item["keep"] for item in baseline),
        "candidate_kept": sum(item["keep"] for item in candidate),
        "codex_keep_candidate_reject": sum(item["codex"]["keep"] for item in disagreements),
        "codex_reject_candidate_keep": sum(item["candidate"]["keep"] for item in disagreements),
        "disagreements": disagreements,
        "caveat": "Agreement is not accuracy. Neither model is ground truth; review the posting evidence.",
    }


class RecordedClient(OpenAIClient):
    def __init__(self, directory: Path, model: str = MODEL, reasoning_effort: str | None = None,
                 max_output_tokens: int | None = None) -> None:
        super().__init__(model=model, timeout=120)
        # Disable automatic retries so a failed batch is visible and spending stays bounded.
        self.client = self.client.with_options(max_retries=0)
        original_create = self.client.responses.create
        self.calls: list[dict] = []

        def recorded_create(**kwargs):
            kwargs["service_tier"] = "default"
            if reasoning_effort is not None:
                kwargs["reasoning"] = {"effort": reasoning_effort}
            if max_output_tokens is not None:
                kwargs["max_output_tokens"] = max_output_tokens
            started = time.monotonic()
            response = original_create(**kwargs)
            usage = response.usage.model_dump() if response.usage is not None else None
            record = {
                "batch": len(self.calls) + 1,
                "response_id": response.id,
                "model": response.model,
                "status": response.status,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": usage,
                "estimated_cost_usd": str(token_cost(usage, model)) if usage else None,
                "output_text": response.output_text,
            }
            self.calls.append(record)
            write_json(directory / f"batch_{record['batch']:02}.json", record)
            print(f"Recorded batch {record['batch']}: status={response.status}", flush=True)
            return response

        self.client.responses.create = recorded_create


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "out/ec2_jobs_sample.json")
    parser.add_argument("--baseline", type=Path, default=ROOT / "out/desktop_agent_decisions.json")
    parser.add_argument("--prompt", type=Path, default=ROOT / "filters/new_grad_prompt.txt")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--model", choices=sorted(MODEL_RATES), default=MODEL)
    parser.add_argument("--reasoning-effort", choices=sorted(set().union(*MODEL_EFFORTS.values())))
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--run", action="store_true", help="Make billable OpenAI API requests")
    args = parser.parse_args()
    if args.reasoning_effort is not None and args.reasoning_effort not in MODEL_EFFORTS[args.model]:
        parser.error(f"{args.model} does not support reasoning effort {args.reasoning_effort}")
    if args.max_output_tokens is not None and args.max_output_tokens < 1:
        parser.error("--max-output-tokens must be positive")

    rows = json.loads(args.input.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    old_decisions = validate_baseline(rows, baseline)
    rows = _normalize_payload_deadlines(rows)
    evaluated_at = datetime.fromisoformat(baseline["evaluated_at"])
    if evaluated_at.tzinfo is None:
        raise ValueError("Baseline evaluated_at must include its timezone")
    today = evaluated_at.astimezone(KST).date().isoformat()
    prompt = args.prompt.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("The filtering prompt must not be empty")
    directory = args.output_dir or ROOT / "out" / (args.model.replace("-", "_") + "_eval_" + datetime.now(KST).strftime("%Y%m%d_%H%M%S_%f"))
    # Never overwrite a previous run, including partial paid responses.
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "prepared",
        "model": args.model,
        "reasoning_effort": args.reasoning_effort or "model default",
        "max_output_tokens": args.max_output_tokens,
        "service_tier": "default",
        "comparison_date_kst": today,
        "batch_size": 20,
        "automatic_retries": 0,
        "input_count": len(rows),
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "baseline_model": baseline.get("model"),
        "rates_usd_per_million_tokens": MODEL_RATES[args.model],
        "rates_source": f"https://developers.openai.com/api/docs/models/{args.model}",
        "rates_checked_on": "2026-10-14",
        "limitations": [
            "Historical baseline did not record its prompt hash, exact CLI model, or batch size.",
            "Uses the current policy, baseline evaluation date, and default batch size of 20.",
            "Model-facing input excludes URLs and matches AgentJobFilter's field allowlist.",
            "Recorded usage estimates cost; it is not an invoice and excludes unreported failed requests.",
        ],
    }
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "input.json", rows)
    write_json(directory / "codex_baseline.json", baseline)
    write_json(directory / "schema.json", DECISION_SCHEMA)
    (directory / "instructions.txt").write_text(SYSTEM_INSTRUCTIONS + prompt, encoding="utf-8")
    print(f"Prepared {len(rows)} postings for {today}: {directory}", flush=True)
    if not args.run:
        print("No API calls made. Add --run to execute with OPENAI_API_KEY.")
        return

    load_dotenv(args.env_file, override=False)
    try:
        client = RecordedClient(directory, args.model, args.reasoning_effort, args.max_output_tokens)
        result = AgentJobFilter(client, prompt, batch_size=20).filter(rows, today=today)
    except AgentError as exc:
        manifest.update(status="failed", error=str(exc))
        write_json(directory / "manifest.json", manifest)
        raise SystemExit(str(exc)) from exc
    write_json(directory / "candidate_decisions.json", result.decisions)
    comparison = compare(rows, old_decisions, result.decisions, args.model)
    usages = [call["usage"] for call in client.calls if call["usage"] is not None]
    cost = sum((token_cost(usage, args.model) for usage in usages), Decimal(0))
    comparison["billing"] = {
        "usage_complete": len(usages) == len(client.calls),
        "input_tokens": sum(usage["input_tokens"] for usage in usages),
        "cached_input_tokens": sum((usage.get("input_tokens_details") or {}).get("cached_tokens", 0) for usage in usages),
        "cache_write_tokens": sum((usage.get("input_tokens_details") or {}).get("cache_write_tokens", 0) for usage in usages),
        "output_tokens": sum(usage["output_tokens"] for usage in usages),
        "reasoning_tokens": sum((usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0) for usage in usages),
        "estimated_cost_usd": str(cost) if len(usages) == len(client.calls) else None,
        "same_workload_30_days_usd": str(cost * 30) if len(usages) == len(client.calls) else None,
    }
    write_json(directory / "comparison.json", comparison)
    manifest["status"] = "completed"
    write_json(directory / "manifest.json", manifest)
    print(json.dumps({key: value for key, value in comparison.items() if key != "disagreements"}, indent=2))


if __name__ == "__main__":
    main()
