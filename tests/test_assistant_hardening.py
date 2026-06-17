import sys
import types

import pytest

from src.agent.assistant import AssistantAgent, _normalize_live_offers
from src.core.settings import Settings


def test_normalize_live_offers_prefers_verified_confident_results():
    rows = [
        {
            "title": "Budget Watch",
            "link": "https://some-store.com/p1",
            "source": "some-store",
            "price_pkr": 9000,
            "rating": 4.3,
            "review_count": 5,
            "verification_status": "unverified",
        },
        {
            "title": "Trusted Verified Watch",
            "link": "https://daraz.pk/p2",
            "source": "daraz",
            "price_pkr": 9500,
            "rating": 4.5,
            "review_count": 120,
            "verification_status": "verified",
            "verified_fields": ["price_pkr", "rating"],
        },
    ]
    out = _normalize_live_offers(rows)
    assert out[0]["title"] == "Trusted Verified Watch"
    assert out[0]["source_confidence"] >= out[1]["source_confidence"]


def test_resolve_reference_offer_supports_ordinal_followup():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_results": [
            {"title": "First Choice", "link": "https://a.test/p1"},
            {"title": "Second Choice", "link": "https://b.test/p2"},
        ]
    }
    out = agent._resolve_reference_offer(query="check reviews of second one", state=state)
    assert out is not None
    assert out["title"] == "Second Choice"


def test_resolve_reference_offer_supports_product_number_followup():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_reference_offer": {"title": "First Choice", "link": "https://a.test/p1"},
        "last_results": [
            {"title": "First Choice", "link": "https://a.test/p1"},
            {"title": "Second Choice", "link": "https://b.test/p2"},
            {"title": "Third Choice", "link": "https://c.test/p3"},
        ],
    }
    out = agent._resolve_reference_offer(query="give me more details about product no 2", state=state)
    assert out is not None
    assert out["title"] == "Second Choice"


def test_resolve_reference_offer_supports_model_token_followup():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_results": [
            {"title": "Generic Gaming Earbuds", "link": "https://a.test/generic"},
            {"title": "M25 Gaming Wireless Earbuds", "link": "https://b.test/m25"},
            {"title": "Ronin R-7015 Gaming Earbuds", "link": "https://c.test/r7015"},
        ]
    }
    out = agent._resolve_reference_offer(query="more details about m25", state=state)
    assert out is not None
    assert out["title"] == "M25 Gaming Wireless Earbuds"


def test_resolve_reference_offer_supports_compacted_model_token_followup():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_results": [
            {"title": "M25 Gaming Wireless Earbuds", "link": "https://b.test/m25"},
            {"title": "Ronin R-7015 Gaming Earbuds", "link": "https://c.test/r7015"},
        ]
    }
    out = agent._resolve_reference_offer(query="details about r7015", state=state)
    assert out is not None
    assert out["title"] == "Ronin R-7015 Gaming Earbuds"


def test_named_followup_wins_over_generic_pronoun_reference():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_reference_offer": {
            "title": "Wave Pro Gaming Earbuds",
            "link": "https://zero.test/wave",
        },
        "last_results": [
            {"title": "Wave Pro Gaming Earbuds", "link": "https://zero.test/wave"},
            {"title": "Ronin R-7015 Gaming Earbuds", "link": "https://ronin.test/r7015"},
        ],
    }
    out = agent._resolve_reference_offer(
        query="give me more details about the ronin earbuds that you searched",
        state=state,
    )
    assert out is not None
    assert out["title"] == "Ronin R-7015 Gaming Earbuds"


def test_resolve_reference_offer_uses_last_selected_product_for_implicit_followup():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_reference_offer": {
            "title": "Ronin R-540 EarBuds",
            "link": "https://ronin.test/r540",
            "source": "ronin",
        },
        "last_results": [
            {"title": "M25 Gaming Wireless Earbuds", "link": "https://b.test/m25"},
            {"title": "Ronin R-540 EarBuds", "link": "https://ronin.test/r540", "source": "ronin"},
        ],
    }
    out = agent._resolve_reference_offer(query="any details about delivery?", state=state)
    assert out is not None
    assert out["title"] == "Ronin R-540 EarBuds"


def test_seed_reference_product_state_uses_store_product_context():
    class _FakeCollection:
        def __init__(self, row):
            self.row = row

        def find_one(self, flt=None, projection=None):
            if self.row and self.row.get("product_id") == flt.get("product_id") and self.row.get("status") == flt.get("status"):
                return dict(self.row)
            return None

    agent = AssistantAgent.__new__(AssistantAgent)
    agent.settings = Settings()
    seller_doc = {
        "product_id": "seller_test_1",
        "offer_id": "seller_test_1",
        "seller_id": "seller_a",
        "seller_name": "Seller A",
        "store_name": "A Store",
        "title": "Sony WH-1000XM5",
        "description": "Wireless headphones",
        "category": "audio",
        "subcategory": "headphones",
        "brand": "Sony",
        "model": "WH-1000XM5",
        "price_pkr": 90000.0,
        "shipping_pkr": 0.0,
        "in_stock": True,
        "stock_qty": 3,
        "images": ["https://example.test/p1.jpg"],
        "image": "https://example.test/p1.jpg",
        "specifications": "ANC",
        "tags": [],
        "external_url": None,
        "internal_path": "/store/products/seller_test_1",
        "source": "A Store",
        "listing_type": "seller",
        "status": "active",
    }
    agent.db = {agent.settings.marketplace_seller_products_collection: _FakeCollection(seller_doc)}
    seeded = agent._seed_reference_product_state(
        query="tell me about this product",
        state={},
        reference_product_id="seller_test_1",
    )
    assert seeded["active_offer"]["title"] == "Sony WH-1000XM5"
    assert seeded["active_offer"]["link"] == "/store/products/seller_test_1"
    assert seeded["last_reference_offer"]["offer_id"] == "seller_test_1"
    assert seeded["last_results_query"] == "Sony WH-1000XM5"


def test_resolve_reference_offer_does_not_hijack_new_search():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_results": [
            {"title": "Samsung Galaxy Watch 6 Classic", "link": "https://a.test/watch6"},
        ]
    }
    out = agent._resolve_reference_offer(query="search for samsung watches under 50000", state=state)
    assert out is None


def test_resolve_reference_offer_does_not_guess_first_row_for_ambiguous_this_one():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_results": [
            {"title": "First Choice", "link": "https://a.test/p1"},
            {"title": "Second Choice", "link": "https://b.test/p2"},
        ]
    }
    out = agent._resolve_reference_offer(query="details about this one", state=state)
    assert out is None


def test_build_turn_plan_requests_clarification_for_ambiguous_followup():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_results": [
            {"title": "First Choice", "link": "https://a.test/p1"},
            {"title": "Second Choice", "link": "https://b.test/p2"},
        ]
    }
    plan = agent._build_turn_plan(query="any details about delivery?", state=state, top_k=5, min_rating=None)
    assert plan["intent"] == "clarification_needed"
    assert "Which product" in plan["clarification_question"]


def test_build_turn_plan_routes_compare_using_active_offer_and_explicit_reference():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "active_offer": {"title": "First Choice", "link": "https://a.test/p1", "source": "store-a"},
        "last_results": [
            {"title": "First Choice", "link": "https://a.test/p1", "source": "store-a"},
            {"title": "Second Choice", "link": "https://b.test/p2", "source": "store-b"},
        ],
    }
    plan = agent._build_turn_plan(query="compare this with product 2", state=state, top_k=5, min_rating=None)
    assert plan["intent"] == "compare_products"
    assert len(plan["comparison_offers"]) == 2


def test_build_state_patch_does_not_auto_select_first_offer_after_multi_result_search():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {}
    plan = {"intent": "new_search", "response_focus": "general", "requires_local_search": True}
    response = {
        "conversation_id": "conv_x",
        "mode": "tool_calling",
        "answer": "Top matching offers:",
        "results": [
            {"title": "First Choice", "link": "https://a.test/p1", "source": "store-a", "price_pkr": 1000},
            {"title": "Second Choice", "link": "https://b.test/p2", "source": "store-b", "price_pkr": 1200},
        ],
    }
    patch = agent._build_state_patch(query="search for earbuds", state=state, plan=plan, response=response)
    assert patch["active_offer"] is None
    assert patch["last_reference_offer"] is None
    assert len(patch["last_results"]) == 2


def test_build_state_patch_tracks_last_tool_outputs():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {}
    plan = {"intent": "new_search", "response_focus": "general", "requires_local_search": True}
    response = {
        "conversation_id": "conv_x",
        "mode": "tool_calling",
        "answer": "Top matching offers:",
        "results": [{"title": "First Choice", "link": "https://a.test/p1", "source": "store-a"}],
        "tool_calls": [
            {"tool_name": "search_offers", "output": {"ok": True}},
            {"tool_name": "inspect_product_page", "output": {"ok": False, "error": "failed"}},
        ],
    }
    patch = agent._build_state_patch(query="search for earbuds", state=state, plan=plan, response=response)
    assert patch["last_tool_outputs"] == [
        {"tool_name": "search_offers", "ok": True, "error": None},
        {"tool_name": "inspect_product_page", "ok": False, "error": "failed"},
    ]


def test_attach_response_context_summarizes_selected_product_followup():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {"last_results_query": "search for gaming earbuds"}
    plan = {
        "intent": "selected_product_logistics",
        "response_focus": "delivery",
        "target_offer": {"title": "M25 Gaming Wireless Earbuds", "link": "https://b.test/m25", "source": "priceoye"},
        "comparison_offers": [],
        "requires_local_search": False,
        "requires_page_inspection": True,
        "requires_live_web_search": False,
        "target_result_indexes": [1],
    }
    response = {
        "conversation_id": "conv_x",
        "mode": "product_followup",
        "answer": "Delivery info...",
        "results": [{"title": "M25 Gaming Wireless Earbuds", "link": "https://b.test/m25", "source": "priceoye"}],
    }
    out = agent._attach_response_context(query="any details about delivery?", state=state, plan=plan, response=response)
    assert out["intent"] == "selected_product_logistics"
    assert out["assistant_context"]["mode_label"] == "Selected Product"
    assert "M25 Gaming Wireless Earbuds" in out["assistant_context"]["summary"]
    assert "because your question is about delivery" in out["assistant_context"]["decision_reason"].lower()
    assert out["assistant_context"]["results_query"] == "search for gaming earbuds"


def test_handle_general_question_explains_last_action_reason():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "active_offer": {"title": "M25 Gaming Wireless Earbuds", "link": "https://b.test/m25", "source": "priceoye"},
        "last_results_query": "search for gaming earbuds",
        "last_plan": {
            "intent": "selected_product_logistics",
            "response_focus": "delivery",
            "target_offer": {"title": "M25 Gaming Wireless Earbuds", "link": "https://b.test/m25", "source": "priceoye"},
            "comparison_offers": [],
            "target_result_indexes": [1],
            "requires_local_search": False,
            "requires_page_inspection": True,
            "requires_live_web_search": False,
        },
    }
    response = agent._handle_general_question(
        query="why did you inspect the page",
        state=state,
        conversation_id="conv_x",
    )
    assert response["mode"] == "general_question"
    assert "because your question is about delivery" in response["answer"].lower()


def test_handle_refine_results_filters_cheaper_than_selected_offer():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "active_offer": {"title": "Selected Earbuds", "link": "https://a.test/selected", "price_pkr": 5000},
        "last_search_results": [
            {"title": "Selected Earbuds", "link": "https://a.test/selected", "source": "a", "price_pkr": 5000},
            {"title": "Budget Earbuds", "link": "https://a.test/budget", "source": "a", "price_pkr": 3500},
            {"title": "Premium Earbuds", "link": "https://a.test/premium", "source": "a", "price_pkr": 7000},
        ],
    }
    response = agent._handle_refine_results(query="show cheaper alternatives", state=state, conversation_id="conv_x")
    assert response["mode"] == "refine_previous_results"
    assert len(response["results"]) == 1
    assert response["results"][0]["title"] == "Budget Earbuds"
    assert "cheaper than the selected product" in response["answer"]


def test_handle_compare_followup_compares_two_selected_products():
    class _FakeAgent(AssistantAgent):
        pass

    agent = _FakeAgent.__new__(_FakeAgent)

    def _fake_product_followup(*, query, reference_offer, conversation_id, user_id, include_tool_trace):
        result = dict(reference_offer)
        result.setdefault("price_pkr", 0)
        result.setdefault("rating", None)
        result.setdefault("review_count", None)
        return {
            "conversation_id": conversation_id,
            "mode": "product_followup",
            "answer": "details",
            "results": [result],
            "tool_calls": [{"tool_name": "fake"}],
        }

    agent._handle_product_followup = _fake_product_followup
    response = agent._handle_compare_followup(
        query="compare product 1 and 2",
        comparison_offers=[
            {"title": "First Choice", "link": "https://a.test/p1", "source": "store-a", "price_pkr": 1000, "rating": 4.2},
            {"title": "Second Choice", "link": "https://b.test/p2", "source": "store-b", "price_pkr": 1200, "rating": 4.0},
        ],
        conversation_id="conv_x",
        user_id="u1",
        include_tool_trace=True,
    )
    assert response["mode"] == "compare_products"
    assert "Comparison between First Choice and Second Choice:" in response["answer"]
    assert "Price gap:" in response["answer"]
    assert "Verdict:" in response["answer"]
    assert len(response["results"]) == 2


def test_handle_refine_results_supports_most_reviewed_sort():
    agent = AssistantAgent.__new__(AssistantAgent)
    state = {
        "last_search_results": [
            {"title": "Choice A", "link": "https://a.test/a", "source": "a", "price_pkr": 4000, "rating": 4.3, "review_count": 15},
            {"title": "Choice B", "link": "https://a.test/b", "source": "a", "price_pkr": 4500, "rating": 4.1, "review_count": 120},
            {"title": "Choice C", "link": "https://a.test/c", "source": "a", "price_pkr": 4800, "rating": 4.8, "review_count": 70},
        ],
    }
    response = agent._handle_refine_results(query="show the most reviewed options", state=state, conversation_id="conv_x")
    assert response["mode"] == "refine_previous_results"
    assert response["results"][0]["title"] == "Choice B"
    assert "sorted by reviews" in response["answer"]


def test_product_followup_uses_selected_offer_tools():
    class _FakeTools:
        def __init__(self):
            self.calls = []

        def call_tool(self, name, arguments, context=None):
            self.calls.append((name, arguments, context))
            if name == "get_offer_details":
                return {
                    "ok": True,
                    "tool_name": name,
                    "result": {
                        "found": True,
                        "offer": {
                            "offer_id": "offer-2",
                            "title": "Galaxy Watch 6",
                            "link": "https://store.test/watch6",
                            "source": "store",
                            "price_pkr": 50000,
                            "rating": 4.4,
                            "review_count": 45,
                            "specifications": "Bluetooth, AMOLED display",
                        },
                    },
                }
            if name == "inspect_product_page":
                return {
                    "ok": True,
                    "tool_name": name,
                    "result": {
                        "final_url": "https://store.test/watch6",
                        "price_pkr": 49000,
                        "rating": 4.5,
                        "review_count": 52,
                        "availability": "in_stock",
                        "has_review_signals": True,
                    },
                }
            raise AssertionError(name)

    agent = AssistantAgent.__new__(AssistantAgent)
    agent.tools = _FakeTools()
    response = agent._handle_product_followup(
        query="give me more details about product no 2",
        reference_offer={"offer_id": "offer-2", "title": "Galaxy Watch 6", "link": "https://store.test/watch6", "source": "store"},
        conversation_id="conv_x",
        user_id="u1",
        include_tool_trace=True,
    )

    assert response["mode"] == "product_followup"
    assert response["results"][0]["price_pkr"] == 49000
    assert [call[0] for call in agent.tools.calls] == ["get_offer_details", "inspect_product_page"]


def test_product_followup_answers_delivery_question_from_inspected_signals():
    class _FakeTools:
        def call_tool(self, name, arguments, context=None):
            if name == "get_offer_details":
                return {
                    "ok": True,
                    "tool_name": name,
                    "result": {
                        "found": True,
                        "offer": {
                            "title": "Bluetti EB150 Portable Power Station",
                            "link": "https://store.test/eb150",
                            "source": "store",
                            "price_pkr": 159999,
                        },
                    },
                }
            if name == "inspect_product_page":
                return {
                    "ok": True,
                    "tool_name": name,
                    "result": {
                        "final_url": "https://store.test/eb150",
                        "shipping_price_pkr": 250,
                        "shipping_summary": "Delivery charges Rs. 250 nationwide",
                        "delivery_info": "Delivery in 3-5 working days",
                    },
                }
            raise AssertionError(name)

    agent = AssistantAgent.__new__(AssistantAgent)
    agent.tools = _FakeTools()
    response = agent._handle_product_followup(
        query="is there any info on the delivery charges for this product",
        reference_offer={"title": "Bluetti EB150 Portable Power Station", "link": "https://store.test/eb150", "source": "store"},
        conversation_id="conv_x",
        user_id="u1",
        include_tool_trace=False,
    )

    assert response["mode"] == "product_followup"
    assert "Delivery/shipping charge: PKR 250." in response["answer"]
    assert "3-5 working days" in response["answer"]


def test_product_followup_ignores_suspicious_page_price_outlier():
    class _FakeTools:
        def call_tool(self, name, arguments, context=None):
            if name == "get_offer_details":
                return {
                    "ok": True,
                    "tool_name": name,
                    "result": {
                        "found": True,
                        "offer": {
                            "title": "Ronin R-7015 Gaming Earbuds",
                            "link": "https://ronin.test/r7015",
                            "source": "ronin",
                            "price_pkr": 4995,
                        },
                    },
                }
            if name == "inspect_product_page":
                return {
                    "ok": True,
                    "tool_name": name,
                    "result": {
                        "final_url": "https://ronin.test/r7015",
                        "price_pkr": 100,
                        "has_review_signals": False,
                    },
                }
            raise AssertionError(name)

    agent = AssistantAgent.__new__(AssistantAgent)
    agent.tools = _FakeTools()
    response = agent._handle_product_followup(
        query="details about ronin",
        reference_offer={"title": "Ronin R-7015 Gaming Earbuds", "link": "https://ronin.test/r7015", "source": "ronin"},
        conversation_id="conv_x",
        user_id="u1",
        include_tool_trace=False,
    )
    assert response["results"][0]["price_pkr"] == 4995


def test_enforce_conversation_owner_blocks_mismatch():
    agent = AssistantAgent.__new__(AssistantAgent)
    agent.settings = types.SimpleNamespace(conversation_require_user_id=False)
    agent.memory = types.SimpleNamespace(get_session=lambda _: {"conversation_id": "conv_x", "user_id": "owner1"})

    with pytest.raises(PermissionError):
        agent._enforce_conversation_owner(conversation_id="conv_x", user_id="owner2", allow_missing_user=False)


def test_chat_raises_on_empty_model_response(monkeypatch):
    class _FakeResponse:
        def __init__(self):
            self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=""))]

    class _FakeCompletions:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeGroq:
        def __init__(self, api_key):
            self.chat = _FakeChat()

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=_FakeGroq))

    agent = AssistantAgent.__new__(AssistantAgent)
    agent.settings = types.SimpleNamespace(groq_api_key="test-key", assistant_model="fake-model")

    with pytest.raises(RuntimeError):
        agent._chat([{"role": "user", "content": "hello"}])


def test_build_grounded_answer_includes_price_range_and_predicted_marker():
    agent = AssistantAgent.__new__(AssistantAgent)
    answer = agent._build_grounded_answer(
        query="search for cooling fan",
        results=[
            {
                "title": "Cooling Fan X1",
                "source": "daraz",
                "link": "https://example.test/fan-x1",
                "total_price_pkr": 5200,
                "display_source_rating": 4.3,
                "display_source_rating_kind": "predicted",
                "price_range_pkr_min": 4800,
                "price_range_pkr_max": 6500,
            }
        ],
    )
    assert "range PKR 4,800 - 6,500" in answer
    assert "rating 4.3 (pred.)" in answer
