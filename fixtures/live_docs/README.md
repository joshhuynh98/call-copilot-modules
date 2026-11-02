# Live docs variants of the scenario recommendations

Same recommendations as `fixtures/scenarios/<id>/recommendations.json`, but where a real page exists on
docs.opswat.com / opswat.com/docs the fictional `docs.example-opswat.invalid` link is replaced by the real one, so the source
review (`shared/references`) has real pages to open. The scenarios themselves are untouched, so the offline tests and
`--fake` runs stay deterministic. **These files need internet access and are not part of `pytest`.**

| File | Real docs links | Left fictional (no real page found) |
|---|---|---|
| `core_service_wont_start` | 3 (MetaDefender Core: service not starting, product upgrading, changing the web port) | none |
| `icap_upload_timeouts` | 2 (ICAP large-file uploads, Core archive extraction engine configuration) | none |
| `sandbox_disk_full` | 2 (Aether Sandbox: low disk space, retention policy) | analysis VM images |
| `third_party_vdi_issue` | 1 (Endpoint: registration and setup, a loose stand-in for the non-persistent desktops link) | server certificate requirements |
| `messy_noisy_call` | 3 (Central Management v7: adding a Core instance, upgrading, Core license) | none |

Jira, Confluence, internal KB and past-case sources are unchanged: they are fictional and report `no_access` or `skipped`.
Links are matched by topic, not written for these scenarios, so a page may open fine and still not back the recommendation.
That is a useful outcome to see in the review.

## Run

Add `--live-docs` to any command that takes `--scenario`. It uses the scenario's transcript and style profile with the
recommendations from this folder. It needs a real AI server in `.env`: with `--fake` the review stays on the offline
fixtures and reports these links as skipped.

```powershell
# which pages open, and the passage picked for each recommendation
.venv\Scripts\callcopilot review --scenario core_service_wont_start --live-docs

# summary / email / chat / demo with real docs
.venv\Scripts\callcopilot summary --scenario core_service_wont_start --live-docs
.venv\Scripts\callcopilot email   --scenario core_service_wont_start --live-docs
```

In the HTTP demo (`.venv\Scripts\callcopilot serve`), tick **Real docs links** in the header, or open
`http://127.0.0.1:8000/?scenario=core_service_wont_start&live=1`. The API form is
`GET /v1/scenarios/<id>?live_docs=true`; PUT the result as a meeting and generate as usual.

To use a file directly (for example one you edited), pass it with `--recs` and your own `--transcript`:

```powershell
.venv\Scripts\callcopilot review --recs fixtures\live_docs\core_service_wont_start.recommendations.json
```

Pages are pinned to the version that was current when these files were made (for example Core `v5.23.0`, ICAP `v5.16.1`,
Aether `3.3.0`, Endpoint `v2610`, Central Management `7.40.0`). If OPSWAT renames or removes a page, the review reports
`not_found` for it: regenerate the link from the docs index or `callcopilot review --url <page>`.
