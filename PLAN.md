# Implementation Plan: LLM Product Search Agent

## Progress Checklist (Updated: March 5, 2026)

### Snapshot (Current State)

- Offers:
  - `offers_raw`: `1421`
  - `offers_normalized`: `1421`
  - `canonical_products`: `513`
- Matching labels:
  - `match_pairs_total`: `563`
  - `match_pairs_labeled`: `362` (`62` positive, `300` negative)
- Personalization:
  - `user_interactions`: `6000`
  - `real_interactions`: `0` (real-ingest pipeline implemented; waiting production/front-end events)
  - active CF model: `artifacts/cf_model`
- Active matcher:
  - `artifacts/matching_model`
  - holdout report at `artifacts/matcher_data/holdout_report.json`
- LLM orchestration:
  - current mode: MCP-style tool-calling assistant loop implemented (`/assistant`)
  - live web tool: enabled via `search_web_products` (Groq web search-backed)
- Conversation memory:
  - current mode: persisted Mongo conversation sessions + turns with summary memory window

### Phase-by-Phase Status

#### Phase 0: Stabilize Data Ingestion

- [x] Keep existing scrapers as data producers.
- [x] Standardize output schema into unified raw + normalized collections.
- [x] Add key fields: `source`, `last_scraped`, `in_stock`, `raw_price`, `price_pkr`.
- [x] Add repeatable cycle support (one-shot + interval scraper runner).
- [x] Prevent duplicate re-ingestion with fingerprint-based no-repeat logic.

Status: **Done**

#### Phase 1: Query + Retrieval Baseline

- [x] `/search` API endpoint implemented.
- [x] Query parsing into structured constraints (brand/model/storage/budget/rating).
- [x] Mongo retrieval with text search + structured filters + fallback path.
- [x] Ranking by value signals (price + rating + freshness + stock + source).

Status: **Done**

#### Phase 2: Transfer Learning Matcher

- [x] Labeled pair pipeline implemented (candidate generation + manual + auto-labeling).
- [x] Manual-positive review tooling implemented (review queue + decision import).
- [x] Sentence-transformer matcher fine-tuning implemented.
- [x] Holdout split + training + evaluation workflow implemented.
- [x] Strict split protocol implemented (time-based split + cross-source validation-only mode).
- [x] Matcher integrated into retrieval/ranking and clustering flow.
- [x] Model registry wiring for active matcher model.

Status: **Done**

#### Phase 3: Small Model (Clustering)

- [x] Embedding generation for offers.
- [x] Clustering implemented with HDBSCAN (when available) and agglomerative fallback.
- [x] Cluster-based product grouping active in pipeline.
- [~] Cluster representative currently chosen by value scoring (not strictly cheapest-only).

Status: **Done (value-based variant)**

#### Phase 4: LLM Layer

- [x] LLM query understanding integrated (Groq-backed parser with fallback).
- [x] Deterministic retrieval/ranking retained as source of truth.
- [x] Result reasoning text returned with ranked alternatives.

Status: **Done**

#### Phase 5: Collaborative Filtering

- [x] Interaction logging pipeline implemented (`/interactions` + storage).
- [x] CF training + evaluation scripts implemented.
- [x] CF score blending with base ranking implemented.
- [x] Real-vs-synthetic interaction separation implemented (`is_synthetic` + real-only training/eval mode).
- [x] Real-event ingestion pipeline implemented (API idempotency + bulk CSV/JSON/JSONL ingestion + real/synthetic stats reporting).
- [~] Live traffic collection is pending (current DB remains synthetic-only until production events arrive).

Status: **Implemented; production maturity pending real traffic ingestion**

#### Phase 6: MCP-Style Tool-Calling Agent Layer

- [x] Add an internal MCP-style tool server with safe, typed tools over existing pipeline.
- [x] Add live web search tool (`search_web_products`) for real-time online offers.
- [x] Add `/assistant` endpoint with a multi-step tool-call loop (decide -> call tool -> synthesize answer).
- [x] Add guardrails (tool allowlist, max tool calls, schema validation, audit/tool logs).
- [x] Add deterministic fallback when tool-call step fails.

Status: **Done**

#### Phase 7: Chat History and Memory

- [x] Add conversation/session storage (`conversation_id`, messages, metadata, timestamps).
- [x] Add retrieval of recent turns + condensed memory for follow-up context.
- [x] Add memory-window policy and truncation/summarization strategy.
- [x] Add retention controls (TTL/indexes) and optional conversation delete endpoint.

Status: **Done**

### Remaining Work (General Product Backlog)

- [ ] Ingest live real user interaction events (non-synthetic) and retrain/evaluate CF in `--only-real` mode.
- [~] Expand automated tests from unit-level to full end-to-end assistant tool-loop + memory retention behavior on a test Mongo instance.
- [ ] Enable strict production mode with `CONVERSATION_REQUIRE_USER_ID=true` and non-optional user identity in API/UI clients.

These backlog items remain valid, but they are **not blockers** for the assistant reasoning upgrade below.

## ChatGPT-Style Assistant Reasoning Upgrade (Added: April 14, 2026)

### Goal

Make the assistant behave more like ChatGPT during product search:

1. Read the user message in context before acting.
2. Decide whether the next action is:
   - no tool call / answer from existing context
   - local product search
   - follow-up on an already selected product
   - live page inspection of the selected product
   - live web search
   - comparison/refinement across prior results
   - clarification request
3. Execute a short internal plan.
4. Return a grounded answer targeted to the actual question instead of defaulting to another search.
5. Keep the active product/result set stable until the user clearly changes direction.

### Why This Work Is Needed

Current behavior is stronger than the original search-only flow, but it is still too reactive:

- direct product follow-ups already work in many cases
- query-specific answers for delivery/specs/warranty/reviews already exist
- conversation memory already exists
- the assistant can still fall back into a new search when it should continue reasoning over the currently selected product or previous result set

The missing piece is a dedicated intent-routing and action-planning layer between user input and tool execution.

### Current Status Snapshot

- [x] Persistent conversation storage and follow-up memory are implemented.
- [x] Product reference resolution is implemented for:
  - numbered references (`product 2`, `second one`)
  - named references
  - compact model-token references such as `m25`
- [x] Product follow-up answers are query-specific for:
  - price
  - specs/features
  - delivery/shipping
  - warranty
  - availability
  - reviews/ratings
- [x] Selected-product follow-up can reuse prior context instead of forcing a new search in common cases.
- [x] A deterministic router now decides between search, selected-product follow-up, comparison, refinement, and clarification flows.
- [x] A lightweight structured plan record is implemented and persisted; it remains deterministic-first by design.
- [x] Basic compare/refine workflows are implemented over prior results without forcing a fresh search.
- [x] Ambiguous follow-ups now use a clarification branch instead of defaulting to search or guessing the first result.
- [x] UI now exposes active mode, selected product/comparison context, compact reasoning summary, explicit action rationale, and a secondary debug trace.

### Execution Guardrails

To keep this plan deliverable, the following items are explicitly treated as **non-blocking**:

- [x] Do not gate assistant-routing work on real user interaction data or CF retraining.
- [x] Do not gate backend reasoning changes on UI work.
- [x] Do not gate the first pass on new MCP tools if the existing tools are sufficient.
- [x] Do not gate the first pass on a model-generated planner; deterministic routing comes first.
- [x] Do not gate this work on production auth tightening or deployment-only settings.
- [x] Do not require live web search for every follow-up; selected-product context and page inspection remain the default path when available.

### Progress Checklist

#### Milestone 1: Intent Routing

- [x] Add a deterministic query router in `src/agent/assistant.py`.
- [x] Introduce explicit intents:
  - `new_search`
  - `selected_product_followup`
  - `selected_product_logistics`
  - `selected_product_reviews`
  - `selected_product_specs`
  - `compare_products`
  - `refine_previous_results`
  - `general_question`
  - `clarification_needed`
- [x] Route each incoming turn through the intent layer before any search or tool loop starts.
- [x] Ensure clear new-search phrases override previous product context.
- [x] Ensure implicit follow-ups reuse `last_reference_offer` or `last_results` when appropriate.
- [x] Add regression tests for each intent class.

Status: **Done**

#### Milestone 2: Structured Action Planning

- [x] Create a lightweight internal plan record for each turn after the router baseline lands.
- [x] Plan fields should include:
  - `intent`
  - `target_offer`
  - `target_result_indexes`
  - `requires_local_search`
  - `requires_page_inspection`
  - `requires_live_web_search`
  - `response_focus`
  - `clarification_question`
- [x] Validate the plan before any tool call executes.
- [x] Keep plan generation deterministic where possible; use the model only where it adds value.
- [x] Log plan decisions for debugging and regression testing.

Status: **Done**

#### Milestone 3: Conversation State Upgrade

- [x] Expand assistant state to include:
  - `last_intent`
  - `last_plan`
  - `active_offer`
  - `active_offer_details`
  - `active_comparison_set`
  - `last_tool_outputs`
  - `last_results_query`
- [x] Keep selected product state stable across related follow-up turns.
- [x] Reset state only when the user explicitly starts a new search or clears context.
- [x] Preserve both the full last result list and the active selected product at the same time.

Status: **Done**

#### Milestone 4: Tool-Use Policy Hardening

- [x] Add explicit policy helpers for:
  - when to answer from memory only
  - when to inspect the saved product page
  - when to perform a fresh local search
  - when to use live web search
  - when to ask a clarifying question instead of searching
- [x] Prevent search from being used as the default fallback for every under-specified turn.
- [x] Prevent category/listing pages from being treated as product-detail pages when detail extraction is requested.
- [x] Improve product-target continuity after logistics/spec/review follow-ups.

Status: **Done**

#### Milestone 5: Comparison and Refinement Flows

- [x] Add compare flow support:
  - `compare 1 and 3`
  - `which one is better`
  - `is product 2 worth the higher price`
- [x] Add refinement flow support:
  - `show cheaper options`
  - `only Samsung`
  - `above 4.2 rating`
  - `under 50k`
- [x] Introduce helpers/tools to compare offers without forcing a new search unless required.
- [x] Preserve original search results while showing refined subsets.

Status: **Done**

#### Milestone 6: Query-Targeted Response Generation

- [x] Make responses match the question type exactly:
  - direct answer for logistics question
  - direct answer for specs question
  - direct answer for availability question
  - side-by-side comparison for compare questions
  - filtered shortlist for refinement questions
- [x] Avoid generic result blocks when the user asked for a narrow detail.
- [x] Return uncertainty clearly when the page/data does not contain the requested fact.
- [x] Keep answer synthesis grounded in saved data and inspected page evidence.

Status: **Done for current supported intents**

#### Milestone 7: Evaluation Harness

- [x] Add assistant behavior tests for:
  - search -> select product -> ask specs
  - search -> select product -> ask delivery
  - search -> select product -> ask review question
  - search -> ask compare question
  - search -> ask refinement question
  - search -> ambiguous follow-up -> clarification branch
  - selected product follow-up -> explicit new search reset
- [x] Add negative tests to ensure follow-up questions do not accidentally trigger fresh searches.
- [x] Add conversation-state regression coverage for `active_offer` and `last_results`.

Status: **Done for current routing scope**

#### Milestone 8: UI Reasoning Surface

- [x] Show the currently active context in the UI:
  - search mode
  - selected product mode
  - comparison mode
  - refinement mode
- [x] Show the selected product badge or label when follow-up answers stay grounded on one item.
- [x] Replace raw tool-noise trace with a compact reasoning/action summary, with the trace demoted to a secondary debug view.
- [x] Keep results-side behavior consistent with the assistant's active context.

Status: **Done**

### Detailed Execution Plan

#### Phase A: Build the Router First

Implement an explicit routing step inside `src/agent/assistant.py` before calling:

- `_handle_product_followup`
- `_handle_review_followup`
- `_llm_tool_loop`
- deterministic local search fallback

This router should answer:

1. Is the user asking a new search?
2. Is the user continuing with a selected product?
3. Is the user referring to previous results as a set?
4. Is the user asking for comparison/refinement?
5. Is the question answerable from existing memory without a tool call?
6. Is clarification required before any search?

#### Phase B: Convert Heuristics Into Policy

Move decision logic out of scattered helper checks into a clear policy layer:

- reuse `active_offer` for logistics/spec/review questions
- reuse `last_results` for comparison/refinement
- inspect the product page only when the answer is not already present in saved data
- use live web search only when local data and selected-page inspection are insufficient
- ask a clarification question when the target is ambiguous

#### Phase C: Add Higher-Level Operations

Extend the MCP/tool layer in `src/mcp/server.py` only where the current tools are insufficient.

Potential additions:

- `compare_offers`
- `refine_current_results`
- `summarize_offer_details`
- `extract_offer_logistics`
- `extract_offer_specs`

These should not replace the existing tools; they should compose them into more deliberate assistant actions.

#### Phase D: Tighten the Assistant Output Layer

The assistant response should be generated from:

1. the selected intent
2. the chosen plan
3. the grounded tool outputs

not from a generic "search results" template.

This will reduce cases where a valid product is selected but the answer does not match the question asked.

#### Phase E: Reflect State in the UI

Update the frontend to show what the assistant is doing without exposing raw internal noise:

- when the assistant is following a selected product
- when it is comparing prior results
- when it started a fresh search
- when it could not answer and needs clarification

### Suggested Implementation Order

1. Intent router
2. State-model expansion
3. Policy hardening for tool choice
4. Query-targeted answer shaping
5. Lightweight plan record and logging
6. Comparison/refinement support
7. Regression harness expansion
8. UI context surfacing
9. Broaden compare/refine language coverage and richer context restoration

### Success Criteria

- [x] `details about 2nd one` keeps the selected product active.
- [x] `what about delivery?` stays on that same selected product without a fresh search.
- [x] `compare 1 and 3` compares prior results instead of re-searching.
- [x] `show cheaper alternatives` refines the current result set instead of starting over.
- [x] `search for samsung watches` resets context and performs a new search.
- [x] Ambiguous follow-ups trigger a clarification question when needed.
- [x] The assistant can explain why it chose search, inspection, refinement, comparison, or no tool call.

### Ownership Notes

Primary backend files expected to change during this initiative:

- `src/agent/assistant.py`
- `src/agent/memory.py`
- `src/mcp/server.py`
- `tests/test_assistant_hardening.py`
- `tests/test_mcp_inspect.py`
- `react UI/src/pages/Home.tsx`

This work is now in the second implementation pass:

1. **Pass 1:** routing, plan object, state continuity, follow-up correctness - **done**
2. **Pass 2:** compare/refine behavior, UI context surfacing, broader evaluation coverage - **done**

## Current Fix Sprint (March 5, 2026)

### Scope

- [x] Add Daraz source end-to-end (scraper + categories config + ingestion source wiring + runner integration).
- [x] Unify freshness semantics across processing/reporting/ranking (`last_seen_at` as primary operational freshness marker).
- [x] Harden scraper configs by removing hardcoded Mongo targets and ensure every scraper writes `last_scraped`.

### Success Criteria

- [~] `jobs/run_scrapper.py --once` can run all enabled playwright scrapers including Daraz without manual script calls (runner wiring complete; live-site execution validation pending).
- [x] `jobs/data_quality_report.py` stale count aligns with `jobs/cleanup_stale_offers.py` stale query logic.
- [x] `src/scrapers/shophive/playwright_async.py` uses runtime settings/CLI args (no fixed DB constants) and persists `last_scraped`.

### Validation Notes

- [x] Daraz scraper smoke-run succeeded on a single category/page (`smart-watches`).
- [x] Shophive scraper smoke-run succeeded on a single category (`air-conditioners`) with new runtime-configurable DB wiring.

## Design Hardening Backlog (March 2026)

### Security and Trust

- [x] Add URL safety/SSRF protections for `inspect_product_page` (scheme checks, private network blocking, redirect limits, response size caps).
- [x] Add per-conversation ownership checks for history/delete/follow-up APIs.
- [x] Add stricter tool schema validation and reject unknown/malformed tool arguments earlier.

### Data Quality and Verification

- [x] Add live-offer verification pass (inspect top-N returned URLs and mark verified fields).
- [x] Add verification metadata in results (`verification_status`, `verified_fields`, `verified_at`).
- [x] Add source-confidence weighting to ranking when fields are unverified.

### Reliability and Observability

- [x] Add deterministic fallback reason codes in API response and UI badges.
- [x] Add assistant regression tests for: empty LLM output, reference-followup grounding, review extraction, auth/ownership checks.
- [x] Add metrics for tool failure rates by tool name and domain.

## Phase 0: Stabilize Data Ingestion (Week 1)

1. Keep existing scrapers as data producers.
2. Standardize output schema into the operational collections used by the system:
   - `offers_raw`
   - `offers_normalized`
   - `canonical_products`
3. Add `source`, `last_scraped`, `in_stock`, `raw_price`, `price_pkr`.
4. Schedule hourly/3-hourly scraping cycles.

Deliverable: consistent cross-site product records.

## Phase 1: Query + Retrieval Baseline (Week 1-2)

1. Create `/search` API endpoint for natural-language query.
2. Parse query into constraints: brand, model, storage, budget.
3. Retrieve candidates from Mongo using text + filters.
4. Rank by total price, stock, freshness.

Deliverable: non-ML baseline that works end-to-end.

## Phase 2: Transfer Learning Matcher (Week 2-4)

1. Collect labeled pairs (`same_product` vs `different_product`) under `data/labels/`.
2. Fine-tune `sentence-transformers` model on title/spec pairs.
3. Use matcher score to merge offers across stores into canonical products.
4. Store canonical mapping table for fast lookup.

Deliverable: higher precision product matching.

## Phase 3: Small Model (Clustering) (Week 3-4)

1. Build embeddings for offers.
2. Cluster embeddings (HDBSCAN or agglomerative fallback).
3. Use cluster ID as candidate canonical product group.
4. Pick the cluster representative using current value scoring (price/rating/freshness/source), not cheapest-only.

Deliverable: robust grouping without requiring huge labels.

## Phase 4: LLM Layer (Week 4+)

1. Use LLM for natural-language query understanding and explanation.
2. Keep deterministic retrieval/ranking as source of truth.
3. Return:
   - best offer
   - top alternatives
   - brief reason (price, confidence, freshness)

Deliverable: conversational agent with reliable pricing output.

## Phase 5: Optional Collaborative Filtering (After Click Data)

1. Log user interactions: clicks, saves, purchases.
2. Train CF model for personalization.
3. Blend CF score with price/match score.

Deliverable: personalized ranking once behavior data is sufficient.

## Phase 6: MCP-Style Tool-Calling Agent Layer (Implemented)

1. Introduce an internal MCP-style tool server wrapping internal capabilities:
   - search/rank offers
   - fetch offer details
   - log interactions
   - interaction/model diagnostics
2. Add LLM agent loop:
   - take natural-language request
   - decide tool calls
   - execute tools
   - produce grounded response
3. Add reliability controls:
   - strict JSON schema validation
   - max tool calls and timeouts
   - fallback path to deterministic `/search`
4. Add observability:
   - per-tool call logs
   - tool-call latency/error metrics

Deliverable: robust tool-using assistant that can execute multi-step retrieval workflows.

## Phase 7: Chat History and Memory (Implemented)

1. Add persistent conversation store in Mongo:
   - `conversation_id`
   - user message / assistant message
   - turn timestamps and metadata
2. Add context builder for follow-up queries:
   - recent turns window
   - compact summary memory for older turns
3. Add memory policy:
   - max tokens/turns
   - summarize older context when budget is exceeded
4. Add governance:
   - TTL retention policy
   - user/session delete endpoint for privacy

Deliverable: context-aware follow-up handling with controlled memory growth and retention.
