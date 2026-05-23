"""Crank text classifier — BERT-based crank vs normal science content classification."""

from __future__ import annotations

from zhihu_cli.extensions.crank.classifier.discovery import discover_cranks as discover_cranks
from zhihu_cli.extensions.crank.classifier.discovery import discover_report as discover_report
from zhihu_cli.extensions.crank.classifier.model import get_model_stats as get_model_stats
from zhihu_cli.extensions.crank.classifier.model import is_crank as is_crank
from zhihu_cli.extensions.crank.classifier.model import load_model as load_model
from zhihu_cli.extensions.crank.classifier.model import predict as predict
from zhihu_cli.extensions.crank.classifier.model import predict_from_article_dict as predict_from_article_dict
from zhihu_cli.extensions.crank.classifier.model import unload_model as unload_model
from zhihu_cli.extensions.crank.classifier.train import check_training_data_ready as check_training_data_ready
from zhihu_cli.extensions.crank.classifier.train import train_classifier as train_classifier
