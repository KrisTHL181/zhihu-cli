"""Model loading and inference for the crank classifier.

Uses module-level singleton: model loaded once on first use, cached for subsequent
calls within the same process. All torch/transformers imports are lazy — the module
is importable even without the [classifier] optional dependencies installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zhihu_cli.extensions.crank.classifier.config import (
    BASE_MODEL_NAME,
    CRANK_THRESHOLD,
    MAX_SEQ_LENGTH,
    METADATA_PATH,
    MODEL_DIR,
    TITLE_WEIGHT,
)
from zhihu_cli.extensions.crank.classifier.text_cleaner import (
    prepare_for_bert,
)

# Module-level singleton
_model: Any = None
_tokenizer: Any = None
_device: str = "cpu"


def _ensure_deps():
    """Lazy-check that torch and transformers are installed."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as e:
        raise ImportError("Classifier dependencies not installed. Run: pip install -e '.[classifier]'") from e


def load_model(model_dir: Path | None = None, force_reload: bool = False) -> None:
    """Load the trained model (or fall back to base model for zero-shot).

    Cached at module level — subsequent calls are no-ops unless *force_reload*.
    """
    global _model, _tokenizer, _device

    if _model is not None and not force_reload:
        return

    _ensure_deps()
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_dir = model_dir or MODEL_DIR

    has_weights = (model_dir / "pytorch_model.bin").exists() or (model_dir / "model.safetensors").exists()
    if model_dir.exists() and has_weights:
        print(f"  [classifier] Loading trained model from {model_dir}")
        _model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
        _tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    else:
        print(f"  [classifier] No trained model at {model_dir}, loading base model for zero-shot")
        _model = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL_NAME, num_labels=2)
        _tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_NAME)

    _model.eval()
    _device = "cuda" if torch.cuda.is_available() else "cpu"
    _model.to(_device)


def predict(
    title: str,
    body: str,
    *,
    threshold: float = CRANK_THRESHOLD,
    title_weight: float = TITLE_WEIGHT,
) -> dict[str, Any]:
    """Run inference on a single article.

    Returns dict with keys: label, probability, confidence, probabilities.
    """
    if _model is None:
        load_model()

    import torch

    input_text = prepare_for_bert(title, body, title_weight=title_weight)

    inputs = _tokenizer(
        input_text,
        return_tensors="pt",
        truncation=True,
        max_length=MAX_SEQ_LENGTH,
        padding=True,
    )
    inputs = {k: v.to(_device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = _model(**inputs)
        probs = torch.softmax(outputs.logits, dim=-1)

    prob_crank = float(probs[0, 1].item())
    prob_normal = float(probs[0, 0].item())

    label = "crank" if prob_crank >= threshold else "normal"
    confidence = max(prob_crank, prob_normal)

    return {
        "label": label,
        "probability": prob_crank,
        "confidence": confidence,
        "probabilities": [prob_normal, prob_crank],
    }


def predict_from_article_dict(
    article: dict[str, Any],
    *,
    threshold: float = CRANK_THRESHOLD,
    title_weight: float = TITLE_WEIGHT,
) -> dict[str, Any]:
    """Run inference from a Zhihu API article dict.

    Handles both excerpt-only (from search API) and full-body (from scrape_article).
    """
    title = article.get("title", "")
    body = article.get("_body", article.get("excerpt", ""))
    return predict(title, body, threshold=threshold, title_weight=title_weight)


def is_crank(
    title: str,
    body: str,
    *,
    threshold: float = CRANK_THRESHOLD,
) -> bool:
    """Quick boolean check."""
    result = predict(title, body, threshold=threshold)
    return result["label"] == "crank"


def model_is_loaded() -> bool:
    """Check if the model has been loaded."""
    return _model is not None


def unload_model() -> None:
    """Free model memory."""
    global _model, _tokenizer
    _model = None
    _tokenizer = None
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def get_model_stats() -> dict[str, Any]:
    """Return model metadata."""
    if METADATA_PATH.exists():
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    return {
        "status": "untrained",
        "model_name": BASE_MODEL_NAME,
        "message": (
            "No trained model found. Run 'zhihu crank classify collect-negatives' then 'zhihu crank classify train'."
        ),
    }
