from __future__ import annotations

import torch
from torch import nn


class MarketplaceMultiTaskNet(nn.Module):
    def __init__(
        self,
        *,
        text_dim: int,
        numeric_dim: int,
        hidden_dim: int = 128,
        seller_vocab: int = 4096,
        brand_vocab: int = 2048,
        category_vocab: int = 512,
        subcategory_vocab: int = 1024,
        embed_dim: int = 16,
    ) -> None:
        super().__init__()
        self.seller_embedding = nn.Embedding(seller_vocab, embed_dim)
        self.brand_embedding = nn.Embedding(brand_vocab, embed_dim)
        self.category_embedding = nn.Embedding(category_vocab, embed_dim)
        self.subcategory_embedding = nn.Embedding(subcategory_vocab, embed_dim)

        self.text_branch = nn.Sequential(
            nn.Linear(text_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.1),
        )
        self.numeric_branch = nn.Sequential(
            nn.Linear(numeric_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(0.1),
        )
        trunk_input = hidden_dim + (hidden_dim // 2) + embed_dim * 4
        self.trunk = nn.Sequential(
            nn.Linear(trunk_input, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.15),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.rating_head = nn.Linear(hidden_dim, 1)
        self.demand_head = nn.Linear(hidden_dim, 1)
        self.month_head = nn.Linear(hidden_dim, 12)

    def forward(self, batch: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        text_features = self.text_branch(batch["text"])
        numeric_features = self.numeric_branch(batch["numeric"])
        embedded = torch.cat(
            [
                self.seller_embedding(batch["seller_id"]),
                self.brand_embedding(batch["brand_id"]),
                self.category_embedding(batch["category_id"]),
                self.subcategory_embedding(batch["subcategory_id"]),
            ],
            dim=1,
        )
        trunk = self.trunk(torch.cat([text_features, numeric_features, embedded], dim=1))
        rating = torch.sigmoid(self.rating_head(trunk)).squeeze(1) * 4.0 + 1.0
        demand = torch.relu(self.demand_head(trunk)).squeeze(1)
        month_logits = self.month_head(trunk)
        return {
            "rating": rating,
            "demand": demand,
            "month_logits": month_logits,
        }
