"""fixtures/live_docs: each scenario's recommendations with real docs.opswat.com links, selectable via --live-docs / ?live_docs=true.
Offline: these tests never open the links (the pages themselves are checked with `callcopilot review`)."""
import pytest
from fastapi.testclient import TestClient

import callcopilot.app.api as api
from callcopilot.app.cli import main
from callcopilot.modules.recommendations.checks import check_recommendations
from callcopilot.shared.checks import failed
from callcopilot.shared.llm import LLMConfig
from callcopilot.shared.sources import has_live_docs, list_scenarios, load_scenario

FAKE = LLMConfig(provider="fake")
SCENARIOS = list_scenarios()
FICTIONAL = "docs.example-opswat.invalid"
REAL_PREFIXES = ("https://www.opswat.com/docs/", "https://docs.opswat.com/")


def _doc_urls(recs):
    return [s.url for s in recs.sources() if s.type.value == "public_doc"]


@pytest.mark.parametrize("sid", SCENARIOS)
def test_every_scenario_has_a_live_variant_with_the_same_recommendations(sid):
    sc = load_scenario(sid)
    assert has_live_docs(sid)
    live = sc.live_recommendations
    assert live.case_id == sc.recommendations.case_id
    key = lambda recs: [(r.id, r.rank, r.title, r.action, [s.type for s in r.sources]) for r in recs.recommendations]
    assert key(live) == key(sc.recommendations), "only the public_doc links may differ from the scenario"


@pytest.mark.parametrize("sid", SCENARIOS)
def test_live_variant_passes_quality_bar_and_has_real_links(sid):
    sc = load_scenario(sid)
    assert not failed(check_recommendations(sc.live_recommendations, sc.transcript))
    urls = _doc_urls(sc.live_recommendations)
    real = [u for u in urls if u.startswith(REAL_PREFIXES)]
    assert real, "a live variant must carry at least one real docs link"
    assert all(u.startswith(REAL_PREFIXES) or FICTIONAL in u for u in urls)
    for s in sc.live_recommendations.sources():
        if s.url in real:
            assert not s.snippet, "a fixture snippet describes a made-up page; do not attribute it to a real one"


def test_scenario_endpoint_serves_live_variant_only_on_request():
    c = TestClient(api.create_app(FAKE, cors_origins=[], api_key=""))
    default = c.get("/v1/scenarios/core_service_wont_start").json()
    live = c.get("/v1/scenarios/core_service_wont_start", params={"live_docs": "true"}).json()
    urls = lambda ctx: [s["url"] for r in ctx["recommendations"]["recommendations"] for s in r["sources"] if s["type"] == "public_doc"]
    assert all(FICTIONAL in u for u in urls(default))
    assert any(u.startswith(REAL_PREFIXES) for u in urls(live))
    assert live["transcript"] == default["transcript"]
    assert all(s["live_docs"] for s in c.get("/v1/scenarios").json())


def test_scenario_endpoint_404_when_no_live_variant(tmp_path, monkeypatch):
    import callcopilot.shared.sources.scenarios as scenarios
    monkeypatch.setattr(scenarios, "LIVE_DOCS_DIR", tmp_path)
    c = TestClient(api.create_app(FAKE, cors_origins=[], api_key=""))
    r = c.get("/v1/scenarios/core_service_wont_start", params={"live_docs": "true"})
    assert r.status_code == 404 and "no live-docs variant" in r.json()["error"]
    assert c.get("/v1/scenarios/core_service_wont_start").status_code == 200
    assert not any(s["live_docs"] for s in c.get("/v1/scenarios").json())


def test_cli_live_docs_flag_swaps_the_recommendations(capsys, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "fake")  # restored after the test; the CLI sets it for --fake
    monkeypatch.setenv("REFERENCE_REVIEW", "fixtures")  # offline: the real pages are not opened here
    main(["summary", "--fake", "--scenario", "core_service_wont_start", "--live-docs"])
    out = capsys.readouterr().out
    assert "MetaDefender Core: Why does the MetaDefender Core service not start?" in out
    main(["summary", "--fake", "--scenario", "core_service_wont_start"])
    assert "MetaDefender Core: Why does the MetaDefender Core service not start?" not in capsys.readouterr().out


def test_cli_live_docs_needs_a_scenario(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "fake")
    with pytest.raises(SystemExit, match="--scenario"):
        main(["summary", "--fake", "--transcript", "fixtures/scenarios/core_service_wont_start/transcript.json", "--live-docs"])
