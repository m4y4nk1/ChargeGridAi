# 0004: Provider-agnostic LLM abstraction

## Status
Accepted

## Context
The agent layer (LangGraph, Phase 9) needs an LLM for planning and
explanation, but the brief is explicit that the LLM never computes — every
number comes from deterministic engines, and the LLM's role is bounded to
planning tool calls and writing evidence-cited narrative. Given that
constraint, the choice of model provider is an operational/cost decision
that should not leak into engine or agent-graph code, and model identifiers
change faster than the code that calls them.

## Decision
Define a single `LLMProvider` interface (`backend/app/agents/llm/base.py`,
implemented starting Phase 9) that every agent node calls through. Concrete
adapters — `anthropic.py`, `openai.py` — implement that interface. Model
names are never hardcoded; they come from `LLM_MODEL_PLANNER` /
`LLM_MODEL_REPORT` environment variables, resolved per the provider's
current model list at deploy time (`LLM_PROVIDER=anthropic|openai` selects
the adapter). This mirrors the existing `POIProvider`/`RoutingProvider`/etc.
pattern already used for data providers (`backend/app/providers/base.py`).

## Consequences
- Swapping providers, or running Anthropic for one agent and OpenAI for
  another, is a config change, not a code change.
- Every LLM call must go through the interface so token/cost logging
  (Langfuse, Phase 9) and the numeric-claim validator (Section 10.3) have one
  choke point to instrument, rather than being scattered per-adapter.
- The interface has to be designed for the lowest common denominator of
  tool-calling/streaming semantics across providers; provider-specific
  features that don't fit the shared interface are deliberately left out
  rather than special-cased into the abstraction.
