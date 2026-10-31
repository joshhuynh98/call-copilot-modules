# 0003: Validate and repair instead of provider JSON modes

**Status:** accepted

**Decision:** Structured outputs are requested in the prompt together with the JSON Schema, extracted from the reply (fences and chatter tolerated), validated with Pydantic, and on failure sent back with the validation errors for up to 2 repair turns (`generate_structured`). After that a clear `StructuredOutputError` is raised.

**Why:** Local models have weaker structured-output support and some servers ignore `response_format`. Validation in our code works everywhere.

**Related guards:** the summary post-processor deletes unknown evidence ids and marks unsupported claims `uncertain`; long transcripts are map-reduced so small context windows work.
