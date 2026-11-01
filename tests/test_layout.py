"""Keeps the package layout honest: modules/ + shared/ + app/, with a one-way dependency direction."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "callcopilot"
MODULES = {"summary", "email", "chatbot", "recommendations"}
# Allowed module -> module imports (a module may reuse another module's pieces, never the reverse direction).
ALLOWED_MODULE_DEPS = {
    "summary": {"recommendations"},
    "email": {"recommendations"},
    "chatbot": {"summary", "email"},
    "recommendations": set(),
}


def imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return {m for m in found if m.startswith("callcopilot")}


def test_only_three_top_level_folders_hold_code():
    folders = {p.name for p in PACKAGE.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert folders == {"modules", "shared", "app"}, folders
    assert {p.name for p in (PACKAGE / "modules").iterdir() if p.is_dir() and p.name != "__pycache__"} == MODULES


def test_shared_never_imports_modules_or_app():
    bad = {(str(f.relative_to(PACKAGE)), m) for f in (PACKAGE / "shared").rglob("*.py") for m in imports(f)
           if m.startswith(("callcopilot.modules", "callcopilot.app"))}
    assert not bad, bad


def test_modules_never_import_app():
    bad = {(str(f.relative_to(PACKAGE)), m) for f in (PACKAGE / "modules").rglob("*.py") for m in imports(f) if m.startswith("callcopilot.app")}
    assert not bad, bad


def test_modules_only_depend_on_the_allowed_modules():
    for module in MODULES:
        deps = {m.split(".")[2] for f in (PACKAGE / "modules" / module).rglob("*.py") for m in imports(f)
                if m.startswith("callcopilot.modules.") and m.split(".")[2] != module}
        assert deps <= ALLOWED_MODULE_DEPS[module], (module, deps)


def test_each_module_owns_its_checks_and_prompts():
    for module in MODULES:
        assert (PACKAGE / "modules" / module / "checks.py").is_file(), module
        assert (PACKAGE / "modules" / module / "prompts").is_dir(), module


def test_prompt_names_are_unique_and_discoverable():
    from callcopilot.shared.prompt_loader import _prompt_folders, load_prompt
    names = _prompt_folders()
    for name in ("organize", "organize_reduce", "summary", "email", "chat", "style_rules", "judge_clarity_conciseness",
                 "judge_summary_faithfulness", "judge_next_actions", "judge_email_tone", "judge_recommendation_relevance"):
        assert name in names, name
        assert load_prompt(name).system
