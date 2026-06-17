from __future__ import annotations

import json
from pathlib import Path

from sentence_transformers import InputExample, SentenceTransformer, losses
from sentence_transformers.evaluation import BinaryClassificationEvaluator
from torch.utils.data import DataLoader


def load_jsonl_pairs(path: str | Path) -> list[dict]:
    pairs: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            label = row.get("label")
            if label is None:
                continue
            pairs.append(
                {
                    "text_a": str(row["text_a"]),
                    "text_b": str(row["text_b"]),
                    "label": float(label),
                }
            )
    return pairs


def train_matcher(
    train_samples: list[dict],
    output_dir: str,
    base_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    epochs: int = 2,
    batch_size: int = 32,
    eval_samples: list[dict] | None = None,
) -> None:
    model = SentenceTransformer(base_model)
    examples = [
        InputExample(texts=[s["text_a"], s["text_b"]], label=float(s["label"]))
        for s in train_samples
    ]
    loader = DataLoader(examples, shuffle=True, batch_size=batch_size)
    loss = losses.CosineSimilarityLoss(model)
    if eval_samples is None:
        split = max(int(len(examples) * 0.8), 1)
        eval_examples = examples[split:]
    else:
        eval_examples = [
            InputExample(texts=[s["text_a"], s["text_b"]], label=float(s["label"]))
            for s in eval_samples
        ]
    evaluator = None
    if eval_examples:
        evaluator = BinaryClassificationEvaluator(
            [ex.texts[0] for ex in eval_examples],
            [ex.texts[1] for ex in eval_examples],
            [int(ex.label >= 0.5) for ex in eval_examples],
            name="match_eval",
        )
    warmup = max(int(len(loader) * 0.1), 10)
    model.fit(
        train_objectives=[(loader, loss)],
        epochs=epochs,
        warmup_steps=warmup,
        evaluator=evaluator,
        output_path=output_dir,
        save_best_model=True,
        show_progress_bar=True,
    )
    model.save(output_dir)
