"""Opens every source cited by the recommendations BEFORE the summary and email are written.

Public docs go to the docs reader (HTTP, browser as fallback); Jira tickets and Confluence pages go to the Atlassian MCP reader;
other source types have no reader and are reported as skipped. Each source becomes a `SourceReview`: did the link work, where does
it really lead, and the passage that matters for the recommendation. It never raises, and one slow or broken source cannot
stop the others.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from callcopilot.shared.contracts import RecommendationSet, Source, SourceReview, SourceType

from .atlassian import AtlassianReader, CallTool
from .config import ReferenceConfig, load_reference_config
from .docs import DocsReader
from .excerpt import select_excerpt
from .fixtures import FixtureReader
from .types import Fetched

MAX_WORKERS = 4


class ReferenceReviewer:
    def __init__(self, cfg: ReferenceConfig, *, docs: DocsReader | None = None, atlassian: AtlassianReader | None = None,
                 fixtures: FixtureReader | None = None):
        self.cfg = cfg
        self.fixtures = (fixtures or FixtureReader()) if cfg.mode == "fixtures" else None
        self.docs = docs or (DocsReader(cfg) if cfg.mode == "on" else None)
        self.atlassian = atlassian or (AtlassianReader(cfg) if cfg.mode == "on" else None)

    def review(self, recs: RecommendationSet | None) -> list[SourceReview]:
        if recs is None:
            return []
        sources: dict[str, Source] = {}
        queries: dict[str, list[str]] = {}
        for r in sorted(recs.recommendations, key=lambda r: r.rank):
            for s in r.sources:
                sources.setdefault(s.url, s)
                queries.setdefault(s.url, []).append(f"{r.title} {r.action} {r.rationale}")
        picked = list(sources.values())[: self.cfg.max_sources]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            return list(pool.map(lambda s: self._review_one(s, " ".join(queries[s.url])), picked))

    def review_source(self, source: Source, query: str = "") -> SourceReview:
        """Open a single source (used by `callcopilot review --url ...`)."""
        return self._review_one(source, query)

    def _review_one(self, source: Source, query: str) -> SourceReview:
        try:
            fetched = self._open(source)
        except Exception as exc:
            fetched = Fetched("error", note=f"unexpected {type(exc).__name__} while opening the source")
        excerpt = ""
        if fetched.has_content:
            rest = select_excerpt(fetched.text, query, max(self.cfg.excerpt_chars - len(fetched.lead), 200))
            excerpt = "\n".join(p for p in (fetched.lead, rest) if p)
        return SourceReview(
            url=source.url, type=source.type, label=source.label, jira_key=source.jira_key, status=fetched.status,
            final_url=fetched.final_url if fetched.status in ("ok", "redirected") else None,
            title=fetched.title, excerpt=excerpt, note=fetched.note,
        )

    def _open(self, source: Source) -> Fetched:
        if self.fixtures is not None:
            return self.fixtures.read(source.url)
        if source.type == SourceType.public_doc and self.docs is not None:
            return self.docs.read(source.url)
        if source.type in (SourceType.jira, SourceType.confluence) and self.atlassian is not None:
            return self.atlassian.read(source.type.value, source.url, source.jira_key)
        return Fetched("skipped", note=f"no reader for {source.type.value} sources")


def make_reviewer(cfg: ReferenceConfig | None = None, *, provider: str | None = None, call_tool: CallTool | None = None) -> ReferenceReviewer | None:
    """The reviewer for this environment, or None when source review is switched off."""
    cfg = cfg or load_reference_config(provider=provider)
    if not cfg.enabled:
        return None
    return ReferenceReviewer(cfg, atlassian=AtlassianReader(cfg, call_tool) if cfg.mode == "on" else None)
