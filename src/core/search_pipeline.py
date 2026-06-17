from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from src.core.freshness import operational_freshness_dt
from src.core.llm_query_parser import parse_query_with_llm
from src.core.marketplace import (
    attach_display_source_fields,
    get_marketplace_projection,
    product_family_key,
    seller_product_to_search_doc,
)
from src.core.model_registry import get_active_model
from src.core.normalize import normalize_text
from src.core.query_parser import ParsedQuery
from src.core.relevance import filter_relevant_results
from src.core.settings import Settings
from src.ml.clustering.cluster_offers import cluster_embeddings
from src.ml.cf.infer_cf import CFRecommender
from src.ml.matching.infer import ProductMatcher
from src.ml.ranking.rerank import rank_offer, sort_results


class SearchPipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.client = MongoClient(self.settings.mongo_uri)
        self.collection = self.client[self.settings.app_db_name][self.settings.normalized_collection]
        self.seller_collection = self.client[self.settings.app_db_name][self.settings.marketplace_seller_products_collection]
        self.reviews_collection = self.client[self.settings.app_db_name][self.settings.marketplace_reviews_collection]
        self.orders_collection = self.client[self.settings.app_db_name][self.settings.marketplace_orders_collection]
        self.predictions_collection = self.client[self.settings.app_db_name][self.settings.marketplace_predictions_collection]
        matcher_path = get_active_model(
            self.settings.model_registry_dir,
            model_type="matcher",
            default=self.settings.matcher_model_path,
        )
        self.matcher = ProductMatcher(matcher_path or self.settings.matcher_model_path)
        self.cf: CFRecommender | None = None
        cf_dir = Path(
            get_active_model(
                self.settings.model_registry_dir,
                model_type="cf",
                default="artifacts/cf_model",
            )
            or "artifacts/cf_model"
        )
        if cf_dir.exists():
            try:
                self.cf = CFRecommender(str(cf_dir))
            except Exception:
                self.cf = None

    def _base_filter(self, pq: ParsedQuery) -> dict:
        flt: dict = {"in_stock": True}
        if pq.brand:
            flt["brand"] = pq.brand
        if pq.max_price_pkr is not None:
            flt["price_pkr"] = {"$lte": pq.max_price_pkr}
        if pq.min_rating is not None:
            flt["rating"] = {"$gte": pq.min_rating}
        if pq.storage_gb is not None:
            flt["storage_gb"] = pq.storage_gb
        if pq.ram_gb is not None:
            flt["ram_gb"] = pq.ram_gb
        return flt

    def _seller_filter(self, pq: ParsedQuery) -> dict:
        flt: dict[str, Any] = {"status": "active", "in_stock": True}
        if pq.brand:
            flt["brand"] = pq.brand
        if pq.max_price_pkr is not None:
            flt["price_pkr"] = {"$lte": pq.max_price_pkr}
        if pq.storage_gb is not None or pq.ram_gb is not None:
            # Seller listings currently do not store hardware-size facets; keep them discoverable by text.
            pass
        return flt

    def _retrieve_candidates(self, pq: ParsedQuery, top_k: int) -> list[dict]:
        flt = self._base_filter(pq)
        seller_flt = self._seller_filter(pq)
        limit = min(max(top_k * 20, 100), self.settings.max_candidates)
        projection = {
            "_id": 0,
            "offer_id": 1,
            "title": 1,
            "title_normalized": 1,
            "link": 1,
            "source": 1,
            "price_pkr": 1,
            "shipping_pkr": 1,
            "image": 1,
            "images": 1,
            "specifications": 1,
            "brand": 1,
            "model": 1,
            "storage_gb": 1,
            "ram_gb": 1,
            "in_stock": 1,
            "rating": 1,
            "review_count": 1,
            "last_scraped": 1,
            "last_scraped_dt": 1,
            "last_seen_at": 1,
        }
        query = {"$and": [flt, {"$text": {"$search": pq.cleaned_query}}]}
        docs: list[dict] = list(
            self.collection.find(query, projection | {"score": {"$meta": "textScore"}})
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
        seller_docs: list[dict] = list(
            self.seller_collection.find(
                {"$and": [seller_flt, {"$text": {"$search": pq.cleaned_query}}]},
                get_marketplace_projection() | {"score": {"$meta": "textScore"}},
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
        docs.extend(seller_product_to_search_doc(row) for row in seller_docs)
        if docs:
            return docs[: limit * 2]
        # Text index fallback path for sparse datasets.
        tokens = [re.escape(t) for t in pq.cleaned_query.split() if t]
        safe_pattern = ".*".join(tokens[:8]) if tokens else re.escape(pq.cleaned_query[:64])
        regex = {"$regex": safe_pattern, "$options": "i"}
        fallback_query = dict(flt)
        fallback_query["title_normalized"] = regex
        fallback_docs = list(self.collection.find(fallback_query, projection).limit(limit))
        seller_fallback_query = dict(seller_flt)
        seller_fallback_query["title_normalized"] = regex
        fallback_seller_docs = list(
            self.seller_collection.find(seller_fallback_query, get_marketplace_projection()).limit(limit)
        )
        fallback_docs.extend(seller_product_to_search_doc(row) for row in fallback_seller_docs)
        return fallback_docs[: limit * 2]

    @staticmethod
    def _candidate_text(doc: dict) -> str:
        return normalize_text(f"{doc.get('title', '')} {doc.get('specifications', '')}")

    def search(self, query: str, top_k: int = 5, user_id: str | None = None) -> list[dict]:
        parsed = parse_query_with_llm(query, self.settings)
        rows = self._retrieve_candidates(parsed, top_k)
        effective_query = parsed.cleaned_query or normalize_text(query)
        rows = filter_relevant_results(effective_query, rows)
        if not rows:
            return []

        query_text = normalize_text(effective_query)
        candidate_texts = [self._candidate_text(r) for r in rows]
        match_scores = self.matcher.query_to_candidates(query_text, candidate_texts)
        # Bound clustering cost under high cardinality requests.
        cluster_rows = rows[: self.settings.max_cluster_candidates]
        cluster_texts = candidate_texts[: self.settings.max_cluster_candidates]
        embeddings = self.matcher.encode(cluster_texts)
        labels = cluster_embeddings(embeddings)

        now = datetime.now(timezone.utc)
        scored_rows: list[dict] = []
        family_prices: dict[str, list[float]] = {}
        for idx, row in enumerate(rows):
            price = row.get("price_pkr")
            shipping = row.get("shipping_pkr", 0.0) or 0.0
            total = None if price is None else float(price) + float(shipping)
            row["total_price_pkr"] = total
            family_key = product_family_key(row)
            row["product_family_key"] = family_key
            if total is not None:
                family_prices.setdefault(family_key, []).append(float(total))
            elif price is not None:
                family_prices.setdefault(family_key, []).append(float(price))
            row["match_score"] = float(match_scores[idx])
            if idx < len(labels):
                row["cluster_id"] = int(labels[idx])
            else:
                row["cluster_id"] = int(1000000 + idx)
            row["freshness_dt"] = operational_freshness_dt(row)
            row["rank_score"] = rank_offer(
                total_price_pkr=total,
                match_score=row["match_score"],
                in_stock=bool(row.get("in_stock", True)),
                rating=row.get("rating"),
                review_count=row.get("review_count"),
                source=row.get("source"),
                last_scraped_dt=row.get("freshness_dt"),
                now=now,
                prefer_value=parsed.prefer_value,
            )
            scored_rows.append(row)

        if user_id and self.cf is not None:
            offer_ids = [str(r.get("offer_id") or "") for r in scored_rows]
            cf_scores = self.cf.score_user_items(user_id, offer_ids)
            cf_scores = self.cf.normalize_scores(cf_scores)
            for row in scored_rows:
                oid = str(row.get("offer_id") or "")
                cf_score = float(cf_scores.get(oid, 0.0))
                row["cf_score"] = cf_score
                # Blend personalization without overpowering price/rating relevance.
                row["rank_score"] = 0.85 * row["rank_score"] + 0.15 * cf_score

        # Keep best value offer per cluster (already includes price + rating in rank).
        best_by_cluster: dict[int, dict] = {}
        for row in scored_rows:
            cid = row["cluster_id"]
            current = best_by_cluster.get(cid)
            if current is None:
                best_by_cluster[cid] = row
                continue
            if row["rank_score"] > current["rank_score"]:
                best_by_cluster[cid] = row

        deduped = list(best_by_cluster.values())
        ranked = sort_results(deduped)[:top_k]
        product_ids = [str(row.get("product_id") or f"scraped_{row.get('offer_id') or ''}") for row in ranked]
        review_rows = list(self.reviews_collection.find({"product_id": {"$in": product_ids}}, {"_id": 0, "product_id": 1, "rating": 1}))
        order_rows = list(self.orders_collection.find({"product_id": {"$in": product_ids}}, {"_id": 0, "product_id": 1, "quantity": 1, "total_pkr": 1, "status": 1}))
        prediction_rows = list(self.predictions_collection.find({"product_id": {"$in": product_ids}}, {"_id": 0}))
        reviews_by_product: dict[str, list[int]] = {}
        for row in review_rows:
            reviews_by_product.setdefault(str(row.get("product_id") or ""), []).append(int(row.get("rating") or 0))
        orders_by_product: dict[str, list[dict]] = {}
        for row in order_rows:
            orders_by_product.setdefault(str(row.get("product_id") or ""), []).append(row)
        predictions_by_product = {str(row.get("product_id") or ""): row for row in prediction_rows}
        for row in ranked:
            product_id = str(row.get("product_id") or f"scraped_{row.get('offer_id') or ''}")
            row["product_id"] = product_id
            rating_list = reviews_by_product.get(product_id, [])
            row["source_rating"] = row.get("rating")
            row["source_review_count"] = row.get("review_count")
            row["app_rating"] = round(sum(rating_list) / len(rating_list), 2) if rating_list else None
            row["app_review_count"] = len(rating_list)
            completed_orders = [item for item in orders_by_product.get(product_id, []) if str(item.get("status") or "") in {"paid", "fulfilled"}]
            row["units_sold"] = sum(int(item.get("quantity") or 0) for item in completed_orders)
            row["order_count"] = len(completed_orders)
            row["revenue_pkr"] = round(sum(float(item.get("total_pkr") or 0.0) for item in completed_orders), 2)
            prediction = predictions_by_product.get(product_id) or {}
            row["predicted_app_rating"] = prediction.get("predicted_app_rating")
            row["predicted_demand_score"] = prediction.get("predicted_demand_score")
            row["seasonal_relevance_score"] = prediction.get("seasonal_relevance_score")
            row["best_months"] = list(prediction.get("best_months") or [])
            row["best_month_labels"] = list(prediction.get("best_month_labels") or [])
            family_prices_for_row = family_prices.get(str(row.get("product_family_key") or "")) or []
            row["price_range_pkr_min"] = round(min(family_prices_for_row), 2) if family_prices_for_row else None
            row["price_range_pkr_max"] = round(max(family_prices_for_row), 2) if family_prices_for_row else None
            row["reason"] = (
                f"Matched semantically ({row['match_score']:.3f}), grouped by product cluster, "
                f"selected cheapest cluster offer and ranked by price/rating/freshness/source."
            )
            row.pop("freshness_dt", None)
        return attach_display_source_fields(ranked)
