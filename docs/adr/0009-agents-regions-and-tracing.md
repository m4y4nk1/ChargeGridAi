# 0009: Agent layer on LangGraph + Claude, local tracing, local policy retrieval, and a region registry

## Status
Accepted (Phase 9). Narrows ADR 0004.

## Context
Phase 9 adds the agent layer from Section 10 of the brief:

- eight agents;
- a typed tool registry;
- a human confirmation before costly runs;
- SSE streaming;
- a numeric-claim validator;
- versioned prompts with traced model calls;
- 30+ golden evals.

It must also extend the product from the Pune Metropolitan Region to the
Mumbai–Pune corridor, so that the brief's own example (Section 10.5) runs.

These constraints shape the design:

- **The LLM never computes.** Every number comes from the deterministic
  engines built in Phases 3–8.
- **Memory is tight.** Docker Desktop has about 3.8 GB. The Celery worker
  runs 2 slots, and planning runs fill both.
- **The brief lists self-hosted Langfuse.** It needs Postgres, ClickHouse,
  Redis, S3 and two app containers.
- **Some choices were the user's.** They picked Claude, a local log table
  for traces, local embeddings for policy search, and the corridor
  extension.

## Decision
1. **Claude through the official Anthropic SDK.**
   - The planner uses `claude-opus-5-5`; specialists and the report use
     `claude-sonnet-5`. Both are in `config/agents.yaml` and can be
     overridden with `LLM_MODEL_PLANNER` / `LLM_MODEL_REPORT`.
   - Calls stream and use adaptive thinking, with explicit `effort` per
     role. The system prompt is cached.
   - The message history is append-only: assistant turns, including their
     thinking blocks, are sent back verbatim.
   - A `refusal` stop reason ends the session with an error; it is not
     retried.
   - `ModelBackend` is the seam ADR 0004 asked for. Only the Anthropic
     adapter exists, plus a `ScriptedBackend` for offline tests.
     `LLM_PROVIDER` values other than `anthropic` are rejected with a clear
     message.
2. **A LangGraph `StateGraph`, checkpointed in Postgres.**
   - We use `AsyncPostgresSaver`, with the agent session id as the thread
     id.
   - **The pipeline steps run in code.** `prepare` (corridor → scenario →
     run to scoring) and `optimise` call the registered tools themselves,
     so the platform pipeline never depends on a model choosing to call
     them.
   - **Specialists interpret and dig further**, each limited to its tool
     groups.
   - **Confirmation is a LangGraph `interrupt()`.** Its payload shows the
     funnel, the findings so far, open questions and cost. The user
     resumes with `Command(resume=…)`.
   - A question skips the pipeline: it goes to the specialists the Planner
     names, then to the report.
3. **Sessions run as asyncio tasks in the API process, not in Celery.**
   - They are I/O-bound: model calls, and waiting for the worker's planning
     run.
   - Putting them on the worker would take one of its two slots, and a
     session waits on a run that needs a slot too.
   - An API restart loses only the node in flight. The session shows as
     `interrupted` and can be resumed from its checkpoint.
4. **Tools call the platform's own API in-process** through
   `httpx.ASGITransport`, or run parameterised read-only queries.
   - They reuse every endpoint's validation. The model never writes SQL or
     URLs.
   - Retrieved text is wrapped as data.
   - Every number in a tool result becomes a fact for the validator.
5. **Traces go to local tables instead of Langfuse.**
   - `agent_llm_call` records one row per model call: agent, model, prompt
     name@version, tokens including cache, USD cost and latency.
   - `agent_event` records the SSE stream, and the session row keeps
     running totals.
   - Limits come from `config/agents.yaml`: 80 tool calls, 14 per agent,
     45 model calls, $3 per session. Hitting one stops the session
     gracefully and keeps what was done.
6. **Policy retrieval runs locally.**
   - Two national PDFs are chunked by page: the MoP 2024 guidelines and the
     PM E-DRIVE PCS guidelines.
   - Chunks are embedded with fastembed (BAAI/bge-small-en-v1.5, 384-d,
     runs on CPU with no API) into pgvector (HNSW), next to a Postgres
     full-text GIN index.
   - Search fuses the two rankings with reciprocal rank fusion.
7. **Regions come from a registry.**
   - `config/regions.yaml` defines `pmr` (default) and `mumbai_pune`.
   - Cell membership lives in `region_cell`, because regions overlap.
   - Demand scenario keys are namespaced outside the default region, e.g.
     `mumbai_pune:base`.
   - A scenario can carry a corridor: a GeoJSON line plus a buffer. It
     restricts candidates and the gap cells used for them.
   - Place names resolve through an OSM place-node gazetteer
     (`osm_place`), so no paid geocoder is needed.

## Consequences
- **The end-to-end corridor example works without a key.**
  `python -m app.agents.smoke` runs it with the scripted model; it takes
  about 3.5 minutes, most of it the planning run. With a key, the same
  graph runs on Claude.
- **Accepted numbers stay accepted.** The validator can only confirm that a
  number appeared in a tool result, not that the model used it in the right
  context. Golden evals check selected facts end to end.
- **One API process.** Scaling to several API workers would need sessions
  moved to a dedicated queue, since the in-process task registry is per
  process.
- **Langfuse can still be added later** by exporting `agent_llm_call`; no
  agent code depends on the trace backend.
- **Maharashtra's 2025 EV policy GR is not in the corpus.** It is published
  in Marathi and the embeddings are English.
