#!/usr/bin/env python3
"""Open-model comparison harness (guide 6.12).

Runs every labelled benchmark case through the same pipeline for each model,
three times by default, and writes a CSV, a JSON summary and the report charts.

    python3 benchmark/run_benchmark.py                       # all configured models
    python3 benchmark/run_benchmark.py --models qwen2.5:7b,mistral:7b --runs 3
    python3 benchmark/run_benchmark.py --via-http http://localhost:8000

Fairness controls, all enforced here rather than left to the operator:
  * identical cases, evidence packs, prompts (one --prompt-version) and schema
  * temperature 0 and the same token ceiling for every model (app/config.py)
  * the same host and provider for every model in a run
  * the RAW model decision is scored, never the post-guardrail decision

If the provider is the offline stub, every artefact is stamped simulated=true.
Those numbers are a dry run of the harness, not a model comparison.
"""
import argparse, csv, json, os, sys, time, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, db                      # noqa: E402
from benchmark import score as scoring          # noqa: E402

RESULTS = os.path.join(ROOT, "benchmark", "results")


def load_cases(path):
    with open(path) as f:
        return json.load(f)


def run_local(case, model, version):
    from app import orchestrator
    return orchestrator.investigate({"alert_id": case["id"], "model": model,
                                     "mode": "benchmark", "prompt_version": version})


def run_http(base, token, case, model, version):
    req = urllib.request.Request(
        base.rstrip("/") + "/webhook/investigate",
        data=json.dumps({"alert_id": case["id"], "model": model,
                         "mode": "benchmark", "prompt_version": version}).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def run(models, runs=3, prompt_version="v3", cases_path=None, out=RESULTS,
        via_http=None, token=None, progress=None):
    """Execute a full comparison and write every artefact. Returns the summary.

    Factored out of main() so the dashboard can start a run on a deployed
    instance, where there is no shell to type the CLI command into. Both paths
    go through this one function, so a run started from the UI is the same run
    the CLI produces — same fairness controls, same artefacts, same scoring.

    `progress` is called with (done, total, model) so a caller can report status.
    """
    cases = load_cases(cases_path or os.path.join(ROOT, "data", "benchmark_cases.json"))
    os.makedirs(out, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    total = len(models) * len(cases) * runs

    rows, t0, done = [], time.time(), 0
    for model in models:
        for case in cases:
            for run_index in range(runs):
                try:
                    result = (run_http(via_http, token, case, model, prompt_version)
                              if via_http else run_local(case, model, prompt_version))
                except Exception as e:
                    # A model that errors or times out still counts — as a failed
                    # task, not as a missing row. Dropping it would flatter the model.
                    result = {"route": "EXCEPTION", "raw_decision": None,
                              "latency_ms": None, "reason": f"harness error: {e}"}
                row = scoring.score_row(case, result)
                row.update({"run_id": run_id, "model": model, "run_index": run_index,
                            "prompt_version": prompt_version})
                rows.append(row)
                done += 1
                if progress:
                    progress(done, total, model)

    elapsed = time.time() - t0
    summary = scoring.summarise(rows)
    order = scoring.rank(summary)
    simulated = any(r["simulated"] for r in rows)

    # ---- artefacts -------------------------------------------------------
    csv_path = os.path.join(out, f"benchmark_{run_id}.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    meta = {"run_id": run_id, "simulated": simulated,
            "provider": config.LLM_PROVIDER,
            "endpoint": config.LLM_BASE_URL,
            "prompt_version": prompt_version,
            "runs_per_case": runs, "cases": len(cases),
            "elapsed_seconds": round(elapsed, 1),
            "ranking": order, "models": summary}
    summary_path = os.path.join(out, f"summary_{run_id}.json")
    with open(summary_path, "w") as f:
        json.dump(meta, f, indent=2)

    persist(rows, run_id)
    charts_ok = True
    try:
        charts(summary, order, out, run_id, simulated)
    except ImportError:
        charts_ok = False

    return dict(meta, csv_path=csv_path, summary_path=summary_path,
                charts=charts_ok, rows=len(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(config.MODELS))
    ap.add_argument("--runs", type=int, default=3,
                    help="runs per case per model, for the consistency metric")
    ap.add_argument("--prompt-version", default="v3")
    ap.add_argument("--cases", default=os.path.join(ROOT, "data", "benchmark_cases.json"))
    ap.add_argument("--via-http", help="drive the running server instead of calling in-process")
    ap.add_argument("--token", help="bearer token when using --via-http")
    ap.add_argument("--out", default=RESULTS)
    a = ap.parse_args()

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    cases = load_cases(a.cases)

    print(f"provider={config.LLM_PROVIDER}"
          f"{' (' + config.LLM_BASE_URL + ')' if config.LLM_BASE_URL else ''}"
          f"  prompts={a.prompt_version}")
    print(f"{len(models)} models x {len(cases)} cases x {a.runs} runs "
          f"= {len(models) * len(cases) * a.runs} investigations\n")

    last = {"model": None}
    def progress(done, total, model):
        if model != last["model"]:
            if last["model"]:
                print()
            last["model"] = model
        print(f"\r  {model:<16} {done:>4}/{total}", end="", flush=True)

    meta = run(models, a.runs, a.prompt_version, a.cases, a.out,
               a.via_http, a.token, progress)
    print()
    summary, order, run_id = meta["models"], meta["ranking"], meta["run_id"]
    simulated = meta["simulated"]
    csv_path, summary_path = meta["csv_path"], meta["summary_path"]
    if not meta["charts"]:
        print("  (charts skipped: pip install matplotlib to generate report figures)")

    # ---- console table ---------------------------------------------------
    cols = [("accuracy", "Acc%"), ("macro_f1", "MacroF1"), ("escalation_recall", "Recall%"),
            ("false_positive_rate", "FP%"), ("hallucination_rate", "Halluc%"),
            ("json_compliance", "JSON%"), ("consistency", "Consist%"),
            ("task_completion", "Done%"), ("latency_p50_ms", "p50ms"), ("latency_p95_ms", "p95ms")]
    print(f"\n{'model':<16}" + "".join(f"{h:>9}" for _, h in cols))
    print("-" * (16 + 9 * len(cols)))
    for m in order:
        st = summary[m]
        print(f"{m:<16}" + "".join(f"{st[k] if st[k] is not None else '—':>9}" for k, _ in cols))

    print(f"\nRanking (escalation recall, then hallucination, then accuracy): "
          + " > ".join(order))
    print(f"Recommended for deployment: {order[0]}")
    print(f"\n  {csv_path}\n  {summary_path}")
    if simulated:
        print("\n  !! SIMULATED RUN — the stub reasoner produced these numbers.\n"
              "     Set FS_LLM_PROVIDER=ollama (local) or openrouter (hosted),\n"
              "     then rerun before quoting anything here as an open-model\n"
              "     comparison.")


def persist(rows, run_id):
    """Mirror results into benchmark_results so the dashboard can read them."""
    for r in rows:
        db.ex("""insert into benchmark_results
                 (run_id, model, case_id, run_index, expected, raw_decision, final_route,
                  risk_score, confidence, json_valid, hallucinated, latency_ms)
                 values (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (run_id, r["model"], r["case_id"], r["run_index"], r["expected"],
               r["raw_decision"], r["final_route"], r["risk_score"], r["confidence"],
               1 if r["json_valid"] else 0, 1 if r["hallucinated"] else 0, r["latency_ms"]))


# Categorical slots 1-4 of the validated reference palette. Fixed order, never
# cycled: a model keeps its colour across every panel.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"]
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e6e3", "#fcfcfb"


def charts(summary, order, out_dir, run_id, simulated):
    """Small multiples: one panel per metric, models on the y-axis.

    Deliberately not one grouped bar chart across all metrics — percentages and
    milliseconds do not share an axis, and forcing them onto one would be the
    dual-axis mistake in disguise.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    panels = [("escalation_recall", "Escalation recall", "%", True),
              ("accuracy", "Decision accuracy", "%", True),
              ("false_positive_rate", "False positives on genuine cases", "%", False),
              ("hallucination_rate", "Hallucination rate", "%", False),
              ("json_compliance", "Structured-output compliance", "%", True),
              ("latency_p50_ms", "Median latency", " ms", False)]

    colour = {m: SERIES[i % len(SERIES)] for i, m in enumerate(order)}
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 6.4), facecolor=SURFACE)
    fig.suptitle("Open-model comparison — identical cases, evidence, prompts and schema",
                 x=0.011, ha="left", fontsize=13, color=INK, weight="semibold")
    sub = (f"{run_id} · temperature 0 · raw model decision, scored before guardrails"
           + ("  ·  SIMULATED RUN, NOT A REAL MODEL COMPARISON" if simulated else ""))
    fig.text(0.011, 0.925, sub, ha="left", fontsize=8.5,
             color="#b03030" if simulated else MUTED)

    ys = list(range(len(order)))[::-1]
    for ax, (key, title, unit, higher_better) in zip(axes.flat, panels):
        vals = [summary[m][key] or 0 for m in order]
        ax.barh(ys, vals, height=.52, color=[colour[m] for m in order], zorder=3)
        span = max(vals) or 1
        for y, v, m in zip(ys, vals, order):
            # Direct labels on every bar: the palette's contrast check obliges
            # visible values, and it removes any need to read against the axis.
            ax.text(v + span * .03, y, f"{v:g}{unit}", va="center", fontsize=9,
                    color=INK, zorder=4)
        ax.set_yticks(ys); ax.set_yticklabels(order, fontsize=9, color=INK)
        ax.set_xlim(0, span * 1.26)
        if not max(vals):
            # Every model scored zero. An axis running 0.0-1.2 would imply a
            # resolution the data does not have, so drop it and let the labels talk.
            ax.set_xticks([])
        ax.set_title(f"{title}   ({'higher is better' if higher_better else 'lower is better'})",
                     fontsize=9.5, color=MUTED, loc="left", pad=8)
        ax.set_facecolor(SURFACE)
        ax.grid(axis="x", color=GRID, linewidth=.8, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(length=0, labelsize=8.5, colors=MUTED)
        for s in ("top", "right", "bottom", "left"):
            ax.spines[s].set_visible(False)

    fig.tight_layout(rect=[0, 0.01, 1, 0.90])
    path = os.path.join(out_dir, f"benchmark_{run_id}.png")
    fig.savefig(path, dpi=170, facecolor=SURFACE)
    fig.savefig(path.replace(".png", ".svg"), facecolor=SURFACE)
    plt.close(fig)
    print(f"  {path}")


if __name__ == "__main__":
    main()
