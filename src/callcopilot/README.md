# Package map

All functional code is in three folders. If you only read one, read `modules/`.

```
callcopilot/
├── modules/                      THE MODULES: one folder each, self-contained
│   ├── summary/                    Meeting summary (organise first, then write)
│   │   ├── summarize.py              the module
│   │   ├── checks.py                 its quality checks
│   │   └── prompts/                  organize, organize_reduce, summary, judge rubrics
│   ├── email/                      Customer follow-up email
│   │   ├── draft.py                  generate → check → repair → enforce
│   │   ├── review.py                 re-check an email the agent edited
│   │   ├── checks.py                 hard rules (plain text, links, ticket ids, ...)
│   │   └── prompts/                  email, judge rubric
│   ├── chatbot/                    In-app chat
│   │   ├── session.py                streaming, tool loop, search-before-"I don't know" guard
│   │   ├── checks.py                 chat checks
│   │   ├── tools/                    tool registry + fixture stubs (search_docs, search_jira, ...)
│   │   └── prompts/                  chat
│   └── recommendations/            Spec + checker + triage for the recommendation module's output
│       ├── checks.py                 the quality bar (what good recommendations look like)
│       ├── triage.py                 sets aside duplicate or unrelated recommendations
│       └── prompts/                  judge rubric
│
├── shared/                       Used by all modules. Not module logic.
│   ├── contracts/                  Data shapes (Pydantic): transcript, recommendations, summary, email, chat
│   ├── llm/                        One LLM client interface; OpenAI-compatible, Anthropic and fake adapters
│   ├── safety/                     Redaction before any LLM call; untrusted-content wrapping
│   ├── sources/                    Transcript/recommendation source interfaces + fixture scenario loader
│   ├── references/                 Opens recommendation sources before writing: docs pages (HTTP + browser), Jira/Confluence (MCP)
│   ├── policy.py                   Content rules (markdown, meeting offers, ticket ids, third-party terms)
│   ├── checks.py                   Check result type + soft style checks
│   ├── prompt_loader.py            Finds and renders versioned prompts
│   ├── prompts/                    Shared prompts: writing-style rules, clarity rubric
│   └── data/                       Third-party term list
│
└── app/                          Ways to run the modules
    ├── api.py                      HTTP API (FastAPI) + serves the demo page
    ├── cli.py                      `callcopilot` command
    ├── pipeline.py                 Builds the LLM client and chains summary → email
    ├── render.py                   Plain-text rendering for the CLI
    ├── evals/                      Eval runner, LLM judge, report
    ├── export_schemas.py           `callcopilot schemas`: writes schemas/ (JSON Schema + OpenAPI)
    └── static/demo.html            Demo page
```

## Rules of thumb

- A module imports from `shared/` and, where needed, from another module (email and summary use `recommendations/triage`; chat reuses the email checks). `shared/` never imports from `modules/` or `app/`.
- Prompts live next to the module that uses them, in `prompts/<name>/vN.md`, found by name (`load_prompt("organize")`). Names are unique across the package.
- Checks live next to the module they check. They all return `CheckResult` (`shared/checks.py`); a "soft" result is a warning, not a failure.
- Outside this package: `schemas/` (generated JSON Schema + OpenAPI), `fixtures/` (fictional mock calls), `docs/`, `tests/`. None of them contain module logic.
