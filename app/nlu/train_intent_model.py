"""Trains the intent classification head on top of frozen rubert-tiny2 embeddings.

Usage: python -m app.nlu.train_intent_model
Writes artifacts/intent_head.joblib + artifacts/labels.joblib, and prints held-out
accuracy so regressions are visible before shipping a new model.
"""

import csv

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from app.nlu.generate_dataset import DATASET_PATH, write_dataset
from app.nlu.intent_classifier import (
    ARTIFACTS_DIR,
    EMBEDDING_MODEL_NAME,
    LABELS_PATH,
    MODEL_PATH,
)


def load_dataset() -> tuple[list[str], list[str]]:
    if not DATASET_PATH.exists():
        write_dataset()
    texts, labels = [], []
    with DATASET_PATH.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            if len(row) != 2:
                continue
            text, label = row
            texts.append(text)
            labels.append(label)
    return texts, labels


def main() -> None:
    from sentence_transformers import SentenceTransformer

    texts, labels = load_dataset()
    print(f"Loaded {len(texts)} labelled examples across {len(set(labels))} intents.")

    print(f"Encoding with {EMBEDDING_MODEL_NAME} ...")
    encoder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    embeddings = encoder.encode(texts, show_progress_bar=False)

    x_train, x_test, y_train, y_test = train_test_split(
        embeddings, labels, test_size=0.2, random_state=42, stratify=labels
    )

    head = LogisticRegression(max_iter=2000, C=5.0, class_weight="balanced")
    head.fit(x_train, y_train)

    y_pred = head.predict(x_test)
    print(classification_report(y_test, y_pred, zero_division=0))

    # Refit on the full dataset for the shipped model (held-out split was only for eval).
    head.fit(embeddings, labels)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(head, MODEL_PATH)
    joblib.dump(np.array(head.classes_), LABELS_PATH)
    print(f"Saved model to {MODEL_PATH}")


if __name__ == "__main__":
    main()
