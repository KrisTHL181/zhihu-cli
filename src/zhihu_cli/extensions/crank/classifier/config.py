"""Paths, model choices, thresholds, and training hyperparameters for the crank classifier."""

from __future__ import annotations

from pathlib import Path

BASE_MODEL_NAME: str = "hfl/chinese-macbert-base"

# Paths
CRANK_DIR: Path = Path.home() / ".zhihu-cli" / "crank"
CLASSIFIER_DIR: Path = CRANK_DIR / "classifier"
MODEL_DIR: Path = CLASSIFIER_DIR / "model"
METRICS_PATH: Path = CLASSIFIER_DIR / "metrics.json"
METADATA_PATH: Path = CLASSIFIER_DIR / "metadata.json"
TRAINING_DATA_DIR: Path = CLASSIFIER_DIR / "training_data"
POSITIVE_DIR: Path = TRAINING_DATA_DIR / "positive"
NEGATIVE_DIR: Path = TRAINING_DATA_DIR / "negative"

# Text processing
MAX_SEQ_LENGTH: int = 512
TITLE_WEIGHT: float = 2.0

# Classification thresholds
CRANK_THRESHOLD: float = 0.5
UNCERTAIN_LOW: float = 0.3
UNCERTAIN_HIGH: float = 0.7
DISCOVERY_THRESHOLD: float = 0.7

# Training hyperparameters
TRAINING_CONFIG: dict = {
    "batch_size": 8,
    "learning_rate": 2e-5,
    "num_epochs": 4,
    "warmup_ratio": 0.1,
    "weight_decay": 0.01,
    "gradient_accumulation_steps": 2,
    "eval_steps": 50,
    "save_steps": 200,
    "logging_steps": 10,
    "seed": 42,
}

NEGATIVE_COLLECTION_CONFIG: dict = {
    "target_count": 1000,
    "science_topics": [
        "物理学",
        "天文学",
        "数学",
        "化学",
        "生物学",
        "量子力学",
        "相对论",
        "进化论",
        "科学科普",
    ],
    "science_authors": [
        "中科院物理所",
        "中国科普博览",
    ],
    "max_per_query": 100,
    "delay_between_queries": 1.5,
}

LABEL_MAP: dict[int, str] = {0: "normal", 1: "crank"}

# Crank-related search keywords for discovery
CRANK_KEYWORDS: list[str] = [
    "推翻相对论",
    "推翻爱因斯坦",
    "新物理学",
    "统一场论",
    "物理学革命",
    "颠覆现代物理",
    "新量子理论",
    "反相对论",
    "时间空间新理论",
    "宇宙新模型",
    "推翻进化论",
    "永动机",
    "反重力",
    "推翻能量守恒",
    "新引力理论",
    "以太 理论",
    "超光速",
    "灵魂 量子",
    "新的宇宙模型",
    "证明哥德巴赫猜想",
]
