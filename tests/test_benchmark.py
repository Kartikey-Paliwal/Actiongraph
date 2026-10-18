import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark.py"


def _load():
    spec = importlib.util.spec_from_file_location("benchmark", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_suite_meets_every_expected_outcome(fresh_graph, capsys):
    assert _load().main(["--iterations", "1", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["unsafe_plans_not_passed"] == "5/5"
    assert summary["safe_plans_falsely_blocked"] == "0/2"
    assert summary["blocks_with_invariant_and_path"] == "5/5"
    assert summary["expected_verdict"] == "7/7"
    assert summary["expected_execution_status"] == "4/4"
    assert summary["verified_labels_with_true_postcondition"] == "2/2"
    assert summary["edge_ablation_pass_then_block"] == "1/1"
