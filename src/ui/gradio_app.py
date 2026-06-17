from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
from typing import Any

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr
from pymongo import MongoClient

from src.agent.assistant import AssistantAgent
from src.core.logging_utils import setup_logging
from src.core.settings import Settings
from src.core.search_pipeline import SearchPipeline

logger = setup_logging("ui.gradio")

TABLE_HEADERS = [
    "#",
    "Title",
    "Source",
    "Price PKR",
    "Rating",
    "Reviews",
    "Match",
    "Link",
]

APP_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;700;800&family=DM+Mono:wght@400;500&display=swap');

:root {
  --ink: #102a27;
  --ink-soft: #33514d;
  --surface: #f8fbfa;
  --surface-2: #edf5f3;
  --card: #ffffff;
  --accent: #0f766e;
  --accent-deep: #115e59;
  --line: #d7e5e1;
  --warm: #d97706;
  --ok: #047857;
}

.gradio-container {
  font-family: 'Manrope', sans-serif !important;
  color: var(--ink);
  background:
    radial-gradient(circle at 0% 0%, #dff4ef 0%, rgba(223,244,239,0) 45%),
    radial-gradient(circle at 100% 100%, #ffe8d2 0%, rgba(255,232,210,0) 35%),
    linear-gradient(180deg, #f4faf8 0%, #eef6f4 100%);
}

.app-shell {
  max-width: 1460px;
  margin: 0 auto;
}

.hero {
  border: 1px solid #194942;
  border-radius: 20px;
  padding: 22px 24px;
  background: linear-gradient(135deg, #0b4f48 0%, #0f766e 48%, #d97706 160%);
  color: #f4fffc;
  box-shadow: 0 20px 40px rgba(17, 94, 89, 0.18);
  margin-bottom: 14px;
}

.hero h1 {
  margin: 0 0 6px 0;
  font-size: 32px;
  line-height: 1.1;
  letter-spacing: -0.02em;
  font-weight: 800;
}

.hero p {
  margin: 0;
  color: #ddf4ef;
}

.panel {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 12px 24px rgba(16, 42, 39, 0.06);
}

.panel .block-title {
  margin-bottom: 8px;
}

.status-chip {
  border-radius: 12px;
  border: 1px solid #c8dad6;
  background: #f6fbfa;
  padding: 10px 12px;
  font-size: 13px;
  color: var(--ink-soft);
}

.offer-card {
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 12px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fcfb 100%);
  margin-bottom: 10px;
}

.offer-card .row-1 {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.offer-card .title {
  font-weight: 700;
  color: var(--ink);
}

.offer-card .source {
  color: var(--ink-soft);
  font-weight: 600;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: .04em;
}

.offer-card .meta {
  display: flex;
  gap: 12px;
  font-size: 13px;
  color: var(--ink-soft);
}

.offer-card .price {
  color: var(--ok);
  font-weight: 800;
}

.offer-card .reason {
  margin-top: 7px;
  font-size: 12px;
  color: #425f5a;
}

.trace-code textarea, .trace-code pre, .trace-code code {
  font-family: 'DM Mono', monospace !important;
  font-size: 12px !important;
}
"""


@lru_cache(maxsize=1)
def get_runtime() -> dict[str, Any]:
    settings = Settings()
    client = MongoClient(settings.mongo_uri)
    db = client[settings.app_db_name]
    pipeline = SearchPipeline(settings)
    assistant = AssistantAgent(settings=settings, pipeline=pipeline, db=db)
    return {
        "settings": settings,
        "client": client,
        "db": db,
        "pipeline": pipeline,
        "assistant": assistant,
    }


def _fmt_price(value: Any) -> str:
    try:
        return f"PKR {float(value):,.0f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def render_results_cards(results: list[dict[str, Any]]) -> str:
    if not results:
        return (
            "<div class='offer-card'>"
            "<div class='title'>No ranked offers available for this query yet.</div>"
            "<div class='reason'>Try broader keywords or increase budget constraints.</div>"
            "</div>"
        )
    cards: list[str] = []
    for idx, row in enumerate(results[:8], start=1):
        title = escape(str(row.get("title", "Untitled")))
        source = escape(str(row.get("source", "unknown")))
        link = escape(str(row.get("link", "#")))
        price = _fmt_price(row.get("total_price_pkr") or row.get("price_pkr"))
        rating = _fmt_float(row.get("rating"), 1)
        reviews = row.get("review_count")
        match = _fmt_float(row.get("match_score"), 3)
        reason = escape(str(row.get("reason", "")))
        cards.append(
            f"""
<div class="offer-card">
  <div class="row-1">
    <div class="title">{idx}. {title}</div>
    <div class="source">{source}</div>
  </div>
  <div class="meta">
    <span class="price">{price}</span>
    <span>Rating: {rating}</span>
    <span>Reviews: {reviews if reviews is not None else "N/A"}</span>
    <span>Match: {match}</span>
    <span><a href="{link}" target="_blank">Open</a></span>
  </div>
  <div class="reason">{reason}</div>
</div>
"""
        )
    return "\n".join(cards)


def results_to_table(results: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, row in enumerate(results, start=1):
        rows.append(
            [
                idx,
                row.get("title", ""),
                row.get("source", ""),
                _fmt_price(row.get("total_price_pkr") or row.get("price_pkr")),
                _fmt_float(row.get("rating"), 1),
                row.get("review_count", ""),
                _fmt_float(row.get("match_score"), 3),
                row.get("link", ""),
            ]
        )
    return rows


def make_status(
    mode: str,
    conversation_id: str,
    results_count: int,
    tool_calls: int,
    fallback_reason: str | None = None,
) -> str:
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    fallback_html = ""
    if fallback_reason:
        fallback_html = f" &nbsp;|&nbsp; <b>Fallback:</b> {escape(fallback_reason)}"
    return (
        f"<div class='status-chip'><b>Mode:</b> {escape(mode)} &nbsp;|&nbsp; "
        f"<b>Conversation:</b> {escape(conversation_id)} &nbsp;|&nbsp; "
        f"<b>Results:</b> {results_count} &nbsp;|&nbsp; "
        f"<b>Tool calls:</b> {tool_calls} &nbsp;|&nbsp; "
        f"<b>Updated:</b> {ts}{fallback_html}</div>"
    )


def run_assistant(
    message: str,
    chat_history: list[dict[str, str]] | None,
    conversation_state: str,
    conversation_id_input: str,
    user_id: str,
    top_k: float,
    min_rating: float | None,
    include_tool_trace: bool,
):
    chat_history = list(chat_history or [])
    query = (message or "").strip()
    if not query:
        return (
            "",
            chat_history,
            conversation_state,
            conversation_id_input,
            gr.update(),
            gr.update(),
            "",
            "<div class='status-chip'><b>Action required:</b> Enter a query first.</div>",
        )

    conversation_id = (conversation_id_input or "").strip() or (conversation_state or "").strip() or None
    uid = (user_id or "").strip() or None
    try:
        runtime = get_runtime()
        assistant: AssistantAgent = runtime["assistant"]
        result = assistant.run(
            query=query,
            conversation_id=conversation_id,
            user_id=uid,
            top_k=int(top_k),
            min_rating=min_rating,
            include_tool_trace=include_tool_trace,
        )
        answer = str(result.get("answer", "")).strip() or "No response generated."
        new_conversation_id = str(result.get("conversation_id", "")).strip()
        mode = str(result.get("mode", "unknown"))
        results = list(result.get("results", []) or [])
        tool_calls = list(result.get("tool_calls", []) or [])
        fallback_reason = str(result.get("fallback_reason", "")).strip() or None
        chat_history.extend(
            [
                {"role": "user", "content": query},
                {"role": "assistant", "content": answer},
            ]
        )
        trace = json.dumps(tool_calls, indent=2, ensure_ascii=True) if include_tool_trace else ""
        return (
            "",
            chat_history,
            new_conversation_id,
            new_conversation_id,
            render_results_cards(results),
            results_to_table(results),
            trace,
            make_status(
                mode,
                new_conversation_id or "N/A",
                len(results),
                len(tool_calls),
                fallback_reason=fallback_reason,
            ),
        )
    except Exception as exc:
        logger.exception("assistant_ui_run_failed")
        error_text = f"Assistant request failed: {exc}"
        chat_history.extend(
            [
                {"role": "user", "content": query},
                {"role": "assistant", "content": error_text},
            ]
        )
        return (
            "",
            chat_history,
            conversation_state,
            conversation_id_input,
            gr.update(),
            gr.update(),
            "",
            f"<div class='status-chip'><b>Error:</b> {escape(str(exc))}</div>",
        )


def load_history(conversation_id_input: str, user_id: str):
    conversation_id = (conversation_id_input or "").strip()
    if not conversation_id:
        return [], "", "", "<div class='status-chip'><b>Action required:</b> Enter conversation ID to load.</div>"
    try:
        runtime = get_runtime()
        assistant: AssistantAgent = runtime["assistant"]
        uid = (user_id or "").strip() or None
        history = assistant.get_history(conversation_id, limit=300, user_id=uid)
        turns = history.get("turns", [])
        chat_messages = [
            {"role": row["role"], "content": row.get("content", "")}
            for row in turns
            if row.get("role") in {"user", "assistant"}
        ]
        status = (
            f"<div class='status-chip'><b>Loaded:</b> {len(chat_messages)} messages "
            f"from conversation <b>{escape(conversation_id)}</b>.</div>"
        )
        return chat_messages, conversation_id, conversation_id, status
    except Exception as exc:
        logger.exception("assistant_ui_load_history_failed")
        return [], "", "", f"<div class='status-chip'><b>Error:</b> {escape(str(exc))}</div>"


def delete_conversation(conversation_id_input: str, user_id: str):
    conversation_id = (conversation_id_input or "").strip()
    if not conversation_id:
        return (
            [],
            "",
            "",
            "",
            [],
            "",
            "<div class='status-chip'><b>Action required:</b> Enter conversation ID to delete.</div>",
        )
    try:
        runtime = get_runtime()
        assistant: AssistantAgent = runtime["assistant"]
        uid = (user_id or "").strip() or None
        summary = assistant.delete_conversation(conversation_id, user_id=uid)
        status = (
            f"<div class='status-chip'><b>Deleted:</b> conversation "
            f"<b>{escape(conversation_id)}</b> | turns={summary.get('turns_deleted', 0)} "
            f"| tool_logs={summary.get('tool_logs_deleted', 0)}</div>"
        )
        return [], "", "", "", [], "", status
    except Exception as exc:
        logger.exception("assistant_ui_delete_history_failed")
        return [], "", "", "", [], "", f"<div class='status-chip'><b>Error:</b> {escape(str(exc))}</div>"


def start_new_conversation():
    return (
        [],
        "",
        "",
        "",
        [],
        "",
        "<div class='status-chip'><b>Ready:</b> New conversation started.</div>",
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Product Search Agent UI", css=APP_CSS, theme=gr.themes.Base()) as demo:
        gr.Markdown(
            """
<div class="app-shell">
  <div class="hero">
    <h1>Product Search Agent</h1>
    <p>LLM + tool-calling + ranking pipeline test console with persistent conversation memory.</p>
  </div>
</div>
            """
        )

        conversation_state = gr.State(value="")

        with gr.Row(elem_classes=["app-shell"]):
            with gr.Column(scale=7, elem_classes=["panel"]):
                status = gr.Markdown(
                    "<div class='status-chip'><b>Ready:</b> Ask a product query to begin.</div>",
                    elem_classes=["block-title"],
                )
                chatbot = gr.Chatbot(
                    value=[],
                    type="messages",
                    height=620,
                    avatar_images=(None, None),
                    bubble_full_width=False,
                    render_markdown=True,
                    label="Conversation",
                )
                with gr.Row():
                    message = gr.Textbox(
                        label="Ask a query",
                        placeholder="Example: Find best Samsung phone under 150000 with rating above 4.0",
                        lines=2,
                        scale=6,
                    )
                    send_btn = gr.Button("Send", variant="primary", scale=1)
                with gr.Accordion("Tool Trace (for debugging)", open=False):
                    trace = gr.Code(label="Tool calls", language="json", value="", elem_classes=["trace-code"])

            with gr.Column(scale=5, elem_classes=["panel"]):
                gr.Markdown("### Session Control")
                conversation_id = gr.Textbox(
                    label="Conversation ID",
                    placeholder="Auto-generated after first message or paste existing ID",
                )
                user_id = gr.Textbox(
                    label="User ID (optional)",
                    placeholder="Used for personalization and interaction attribution",
                )
                with gr.Row():
                    top_k = gr.Slider(label="Top K", minimum=1, maximum=20, step=1, value=5)
                    min_rating = gr.Number(label="Min Rating (optional)", value=None, precision=1)
                include_tool_trace = gr.Checkbox(label="Include tool trace in response", value=True)

                with gr.Row():
                    new_btn = gr.Button("New", variant="secondary")
                    load_btn = gr.Button("Load", variant="secondary")
                    delete_btn = gr.Button("Delete", variant="stop")

                gr.Markdown("### Ranked Offers")
                results_cards = gr.Markdown(value=render_results_cards([]))
                results_table = gr.Dataframe(
                    headers=TABLE_HEADERS,
                    datatype=["number", "str", "str", "str", "str", "str", "str", "str"],
                    value=[],
                    interactive=False,
                    row_count=(0, "dynamic"),
                    col_count=(len(TABLE_HEADERS), "fixed"),
                    wrap=True,
                    max_height=300,
                )

        send_outputs = [
            message,
            chatbot,
            conversation_state,
            conversation_id,
            results_cards,
            results_table,
            trace,
            status,
        ]
        send_inputs = [
            message,
            chatbot,
            conversation_state,
            conversation_id,
            user_id,
            top_k,
            min_rating,
            include_tool_trace,
        ]
        send_btn.click(run_assistant, inputs=send_inputs, outputs=send_outputs)
        message.submit(run_assistant, inputs=send_inputs, outputs=send_outputs)

        load_btn.click(
            load_history,
            inputs=[conversation_id, user_id],
            outputs=[chatbot, conversation_state, conversation_id, status],
        )
        delete_btn.click(
            delete_conversation,
            inputs=[conversation_id, user_id],
            outputs=[chatbot, conversation_state, conversation_id, results_cards, results_table, trace, status],
        )
        new_btn.click(
            start_new_conversation,
            inputs=[],
            outputs=[chatbot, conversation_state, conversation_id, results_cards, results_table, trace, status],
        )

    return demo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Gradio UI for Product Search Agent.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = build_app()
    app.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
