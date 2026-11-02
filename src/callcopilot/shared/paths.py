from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # src/callcopilot
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO_ROOT / "fixtures"
SCENARIOS_DIR = FIXTURES_DIR / "scenarios"
LIVE_DOCS_DIR = FIXTURES_DIR / "live_docs"  # scenario recommendations with real docs.opswat.com links (need internet)
SCHEMAS_DIR = REPO_ROOT / "schemas"  # exported JSON Schema + OpenAPI for non-Python teammates
