"""Training pipeline for the crank vs normal BERT classifier.

Wraps HuggingFace Trainer with custom metrics, early stopping, and author-stratified
validation. All imports are lazy — importable without [classifier] deps installed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _ensure_deps():
    try:
        import sklearn  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as e:
        raise ImportError("Classifier dependencies not installed. Run: pip install -e '.[classifier]'") from e


def compute_metrics(eval_pred: Any) -> dict[str, float]:
    """Compute classification metrics for HuggingFace Trainer."""
    import numpy as np
    import torch
    from sklearn.metrics import accuracy_score, precision_recall_fscore_support, roc_auc_score

    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)
    probs = torch.softmax(torch.from_numpy(logits), dim=-1).numpy()

    precision, recall, f1, _ = precision_recall_fscore_support(
        labels,
        predictions,
        average="binary",
        zero_division=0,
    )
    acc = accuracy_score(labels, predictions)
    try:
        auc = roc_auc_score(labels, probs[:, 1])
    except ValueError:
        auc = 0.0

    return {
        "accuracy": float(acc),
        "f1": float(f1),
        "precision": float(precision),
        "recall": float(recall),
        "roc_auc": float(auc),
    }


class _SimpleDataset:
    """Minimal torch Dataset that tokenizes on-the-fly to avoid storing tokenized tensors."""

    def __init__(self, texts: list[str], labels: list[int], tokenizer: Any) -> None:
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer

    def __len__(self) -> int:
        return len(self.texts)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        from zhihu_cli.extensions.crank.classifier.config import MAX_SEQ_LENGTH

        enc = self.tokenizer(
            self.texts[idx],
            truncation=True,
            max_length=MAX_SEQ_LENGTH,
            padding="max_length",
            return_tensors="pt",
        )
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "labels": self.labels[idx],
        }


def train_classifier(
    hof_root: Path,
    *,
    output_dir: Path | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Full training pipeline.

    Steps: load data → author-stratified split → load model → train → save → metrics.
    """
    _ensure_deps()
    import torch
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        EarlyStoppingCallback,
        Trainer,
        TrainingArguments,
    )

    from zhihu_cli.extensions.crank.classifier.config import (
        BASE_MODEL_NAME,
        METADATA_PATH,
        METRICS_PATH,
        MODEL_DIR,
        TRAINING_CONFIG,
    )
    from zhihu_cli.extensions.crank.classifier.dataset import load_and_split_dataset

    output_dir = output_dir or MODEL_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    config = {**TRAINING_CONFIG, **overrides}
    seed = config["seed"]

    # 1. Load and split data
    print("Loading and splitting dataset...")
    data = load_and_split_dataset(hof_root, seed=seed)

    # 2. Load model and tokenizer
    print(f"Loading base model: {BASE_MODEL_NAME}")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL_NAME, num_labels=2)

    # 3. Build datasets
    train_dataset = _SimpleDataset(data["train_texts"], data["train_labels"], tokenizer)
    val_dataset = _SimpleDataset(data["val_texts"], data["val_labels"], tokenizer)

    # 4. Training arguments
    training_args = TrainingArguments(
        output_dir=str(output_dir / "checkpoints"),
        overwrite_output_dir=True,
        num_train_epochs=config["num_epochs"],
        per_device_train_batch_size=config["batch_size"],
        per_device_eval_batch_size=config["batch_size"] * 2,
        gradient_accumulation_steps=config["gradient_accumulation_steps"],
        warmup_ratio=config["warmup_ratio"],
        weight_decay=config["weight_decay"],
        learning_rate=config["learning_rate"],
        logging_dir=str(output_dir / "logs"),
        logging_steps=config["logging_steps"],
        eval_steps=config["eval_steps"],
        save_steps=config["save_steps"],
        evaluation_strategy="steps",
        save_strategy="steps",
        load_best_model_at_end=True,
        metric_for_best_model="f1",
        greater_is_better=True,
        report_to="none",
        dataloader_pin_memory=False,
        seed=seed,
        fp16=torch.cuda.is_available(),
    )

    # 5. Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        tokenizer=tokenizer,
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=3)],
    )

    # 6. Train
    print(f"\nStarting training ({config['num_epochs']} epochs, batch_size={config['batch_size']})...")
    trainer.train()

    # 7. Save
    print(f"\nSaving model to {output_dir}...")
    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    # 8. Evaluate
    val_metrics = trainer.evaluate()
    print("\nValidation metrics:")
    for key in ["eval_accuracy", "eval_f1", "eval_precision", "eval_recall", "eval_roc_auc"]:
        if key in val_metrics:
            print(f"  {key}: {val_metrics[key]:.4f}")

    # 9. Save metadata
    metadata = {
        "model_name": BASE_MODEL_NAME,
        "train_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "num_epochs": config["num_epochs"],
        "batch_size": config["batch_size"],
        "learning_rate": config["learning_rate"],
        "val_metrics": {k: float(v) for k, v in val_metrics.items() if isinstance(v, (int, float))},
        "label_map": {0: "normal", 1: "crank"},
    }
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {k: float(v) for k, v in val_metrics.items() if isinstance(v, (int, float))},
            f,
            ensure_ascii=False,
            indent=2,
        )
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"Metadata saved to {METADATA_PATH}")
    return metadata


def check_training_data_ready() -> bool:
    """Check if we have both positive and negative samples for training."""
    from zhihu_cli.extensions.crank.classifier.data_loader import (
        count_negative_samples,
        count_positive_samples,
    )

    pos = count_positive_samples()
    neg = count_negative_samples()
    print(f"Positive samples: {pos}")
    print(f"Negative samples: {neg}")

    if pos < 50:
        print("ERROR: Need at least 50 positive samples.")
        return False
    if neg < 50:
        print("ERROR: Need at least 50 negative samples.")
        return False
    return True
