# Evals

```powershell
.venv\Scripts\callcopilot eval --fake          # offline: deterministic checks on all scenarios
.venv\Scripts\callcopilot eval                 # real AI server from .env; adds the LLM judge when configured
.venv\Scripts\callcopilot eval --no-judge icap_upload_timeouts
```

Writes `evals/reports/report-<timestamp>.md` and `.json` (gitignored) and exits non-zero if any hard check fails. The latest baseline is in [baseline-report.md](baseline-report.md).

## Deterministic checks (no AI server needed)

Per scenario, against the generated (or fake) outputs and the golden expectations:

- **Recommendations:** the full quality bar from [recommendations-spec.md](recommendations-spec.md), plus golden expectations (top action, required source types).
- **Summary:** evidence ids exist; claims grounded or marked uncertain; required sections (issues, next actions); plain-language blurb ≤ 80 words (target 60); version not invented (must appear in the transcript or be marked uncertain); low-confidence segments listed; golden must-mention, expected version, action owners, third-party detection, uncertainty on noisy calls.
- **Email:** all hard rules in [email-rules.md](email-rules.md), plus golden must-mention / must-not-mention / required public links. `email_first_pass_clean` (soft/WARN) shows when the model needed repairs or auto-fixes.
- **Style (soft, WARN only):** `no_filler`, `concise_bullets` (≤ 30 words per list line), `plain_sentences` (average ≤ 22 words) on the summary text and the email. The goldens are kept clean of all three.
- **Chat probes** (`golden/chat.json`): answers contain expected facts; "I don't know" answers were preceded by a search and say what was checked; URLs come from known sources; `<email>` blocks follow the email rules.

**What the fake provider proves:** it serves the golden outputs, so passing proves the pipeline, the checks and the report work, not model quality. The negative tests (`tests/test_checks.py`, `tests/test_chat.py`) prove the checks catch violations.

## LLM-as-judge (needs a real server)

Versioned rubrics in `src/callcopilot/prompts/judge_*/vN.md`, each scoring 1–5 with reasons: `summary_faithfulness`, `next_actions` (completeness against the golden), `email_tone` (style match), `clarity_conciseness` (brief, no filler, plain English, grouped, easy to scan), `recommendation_relevance`. The judge uses `AI_JUDGE_*`, falling back to `AI_*`. With only the fake provider, judge checks are skipped and the report says so.

## Adding a scenario

Create `fixtures/scenarios/<id>/` with `transcript.json`, `recommendations.json`, `style_profile.json`, `kb.json` and `golden/{summary,email,chat,expectations}.json`, copying an existing one. `pytest tests/test_fixtures.py` validates it and checks that the goldens pass our own quality bar.
