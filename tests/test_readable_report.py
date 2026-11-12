"""The plain-language report: every sentence matches the analysis, nothing is lost, nothing is overstated."""
import pytest

from workbench import analytics, api as WA, export, readable as R, simulations as S, store

DESIGN = {"environment": "builtin-field-hospital", "agents": {"X": "builtin-priority", "Y": "builtin-hardliner"},
          "conditions": ["baseline_v1", "structured_v1"], "repetitions": 3}


@pytest.fixture
def doc(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "w.db")
    sim = S.start(DESIGN, background=False)
    return store.get("simulations", sim.id)


def test_the_api_sends_the_plain_summary_with_each_experiment(doc):
    body, code = WA.get(f"/api/workbench/simulations/{doc['id']}")
    assert code == 200 and body["readable"]["headline"] and body["readable"]["numbers"]


def test_every_plain_number_is_the_analysis_number(doc):
    a = analytics.analyse(doc)
    out = R.summary(doc, a)
    done = [t for t in doc["trials"] if t["status"] == "completed"]
    agreed = sum(t["outcome"] == "agreed" for t in done)
    assert f"{agreed} of {len(done)} negotiations" in out["headline"]
    nums = {n["label"]: n for n in out["numbers"]}
    assert nums["Deals reached"]["value"] == R.pct(agreed / len(done))
    assert nums["Best result captured"]["value"] == R.pct(a["overall"]["rwe"])
    assert nums["Best result captured"]["exact"] == f"RWE {a['overall']['rwe']:.3f}"
    names = {k: v["name"] for k, v in doc["config"]["agents"].items()}
    shares = {names[k]: p["share_of_value"]["mean"] for k, p in a["sandbox"]["profiles"].items()}
    split = next(p["text"] for p in out["points"] if p["text"].startswith("When they made a deal"))
    for who, v in shares.items():
        assert R.pct(v) in split and who in split


def test_ranges_are_the_analysis_intervals_and_unclear_patterns_are_called_unclear():
    s = {"mean": 0.36, "ci": [0.333, 0.381], "n": 3}
    assert R._range(s) == "likely between 33% and 38%, from 3 repetitions"
    assert R._range({"mean": 0.7, "ci": [0.71, 0.71], "n": 3}) == "71% in every repetition (3 repetitions)"
    assert R._range({"mean": 0.5, "ci": None, "n": 1}) == "too few repetitions for a likely range (1 repetition)"


def test_half_rounds_up_like_the_page():
    assert R.pct(0.125) == "13%" and R.pct(0.875) == "88%" and R.pct(None) == "—"


def test_report_md_keeps_every_detail_and_adds_the_plain_version_first(doc):
    md = export.build(doc, "report.md")["content"]
    order = ["## The short version", "## Key numbers", "## Each agent, in plain words", "## Good to know",
             "## Configuration", "## Behaviour profiles", "## What the measures mean", "## Results by condition",
             "## Observations", "## Reproducibility", "## Limitations", "## Words used in this report"]
    at = [md.index(h) for h in order]
    assert at == sorted(at)


def test_an_experiment_with_nothing_finished_still_reads(doc):
    empty = {**doc, "trials": [], "status": "cancelled"}
    out = R.summary(empty, analytics.analyse(empty))
    assert "nothing to summarise" in out["headline"] and out["points"] == [] and out["glossary"]


def test_names_cannot_inject_html_into_the_markdown(doc):
    doc["config"]["agents"]["X"]["name"] = "<script>x</script>"
    md = export.build(doc, "report.md")["content"]
    assert "<script" not in md and "&lt;script" in md
