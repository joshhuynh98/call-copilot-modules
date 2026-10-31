from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures"
SCENARIOS_DIR = FIXTURES_DIR / "scenarios"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
