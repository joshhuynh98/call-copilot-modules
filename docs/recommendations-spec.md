# Recommendations: expected results (spec) and how they are scored

We do not build the recommendation engine. This is the spec for what good output looks like, and `check_recommendations` scores any payload (mock or real) against it.

## Shape (`RecommendationSet`, JSON Schema in `contracts/recommendation_set.schema.json`)

```json
{ "case_id": "CASE-1", "recommendations": [{
  "id": "rec_1", "rank": 1,
  "title": "Raise the ICAP scan timeout above the archive scan time",
  "action": "Have the customer raise the ICAP Server scan timeout from 60s to 300s, then retest.",
  "rationale": "Log shows ICAP Server gives up after 60s waiting for Core, matching large-archive failures.",
  "sources": [{"type": "public_doc", "label": "ICAP Server: configuring scan timeouts", "url": "https://..."},
              {"type": "jira", "label": "...", "url": "https://.../browse/ABC-1", "jira_key": "ABC-1"}],
  "confidence": 0.86, "trigger_segment_ids": ["seg_004", "seg_008"] }] }
```

`type` is one of `public_doc`, `internal_kb`, `confluence`, `jira`, `past_case`. Optional: `confidence`, `snippet`.

## Quality bar (each is a named check)

| Check | Rule |
|---|---|
| `glanceable_text` | `action` is one line ≤ 120 chars; `rationale` is 1–240 chars (one to two lines) |
| `source_count` | 1–4 sources per recommendation. **No source = fails the bar.** |
| `source_types_and_urls` | Valid http(s) URL and a label; Jira sources have a `jira_key` that matches `/browse/KEY` in the URL; `jira_key` only on Jira sources; `public_doc` must not be an Atlassian/internal URL; `jira`/`confluence` must be Atlassian-style |
| `ranks_ordered` | Ranks unique, ascending, starting at 1 (most relevant / likely next step first) |
| `trigger_segments_valid` | Every recommendation cites ≥ 1 transcript segment, and the ids exist |
| `relevance_to_trigger_segments` | Keyword overlap between the recommendation (title/action/rationale/source labels) and its trigger segments (deterministic floor; the judge scores real relevance) |
| `no_third_party_troubleshooting` | No instructions for third-party products (VDI, proxy, firewall, SSL inspection, ...). Telling the agent to say it is out of scope is fine. |
| `no_duplicates` | No two recommendations with near-identical title+action or the same source set |
| `ordering_plausible` | Rank 1 does not have much lower confidence than later items |

The contract itself is lenient (it accepts items that miss the bar) so the report can name each failing item instead of rejecting the whole payload.

## Usage

```powershell
.venv\Scripts\callcopilot recs-eval path\to\recommendations.json --transcript path\to\transcript.json -v
```

or `POST /v1/recommendations/evaluate` with `{"recommendations": {...}, "transcript": {...}}`. The judge rubric `judge_recommendation_relevance` (versioned in `src/callcopilot/prompts/`) scores relevance 1–5 when an AI server is configured.

## Triage when the input is messy

The recommendation module may send duplicates or items that do not fit the call. The summary's organise stage flags these (`MeetingSummary.recommendation_notes`: `rec_id`, `relevant`, `issue` = index of the issue it addresses, short `note`), and code flags near-duplicates itself. Only the relevant ones feed the customer email (links and context); the ticket ids of every provided recommendation stay forbidden in the email, and the chatbot still shows the agent the full list. Triage never removes everything. Sending clean, deduplicated recommendations of course makes this a no-op.
