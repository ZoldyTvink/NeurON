"""Intent classification: pretrained Russian sentence embeddings + a small trained head.

We don't fine-tune a full LLM: a frozen pretrained encoder (cointegrated/rubert-tiny2,
an openly licensed, Sber-affiliated Russian distilled BERT) turns text into embeddings,
and a lightweight LogisticRegression head is trained on our own synthetic dataset
(app/nlu/data/intents_dataset.csv). This keeps the model small, fast on CPU, and cheap
to retrain, while still being a real trained neural pipeline (not a paid LLM API).

If the embedding model can't be downloaded (offline demo), we fall back to a
keyword/TF-IDF classifier so the service keeps working, at reduced accuracy.
"""

from pathlib import Path

import joblib

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "intent_head.joblib"
LABELS_PATH = ARTIFACTS_DIR / "labels.joblib"
EMBEDDING_MODEL_NAME = "cointegrated/rubert-tiny2"
CONFIDENCE_THRESHOLD = 0.2

_encoder = None  # lazily loaded sentence-transformers model
_head = None
_labels = None
_fallback = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        from sentence_transformers import SentenceTransformer

        _encoder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _encoder


def _load_trained_head() -> bool:
    global _head, _labels
    if _head is not None:
        return True
    if MODEL_PATH.exists() and LABELS_PATH.exists():
        _head = joblib.load(MODEL_PATH)
        _labels = joblib.load(LABELS_PATH)
        return True
    return False


def _get_fallback_classifier():
    """TF-IDF + logistic regression trained on the fly, used only if embeddings are unavailable."""
    global _fallback
    if _fallback is None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline

        from app.nlu.train_intent_model import load_dataset

        texts, labels = load_dataset()
        pipeline = make_pipeline(
            TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)),
            LogisticRegression(max_iter=1000),
        )
        pipeline.fit(texts, labels)
        _fallback = pipeline
    return _fallback


def classify(text: str) -> tuple[str, float]:
    """Returns (intent_label, confidence). Falls back to 'fallback' below threshold."""
    text = text.strip()
    if not text:
        return "fallback", 0.0

    try:
        if _load_trained_head():
            encoder = _get_encoder()
            embedding = encoder.encode([text])
            probs = _head.predict_proba(embedding)[0]
            best_idx = probs.argmax()
            confidence = float(probs[best_idx])
            label = _labels[best_idx]
        else:
            raise RuntimeError("no trained head artifacts found")
    except Exception:
        pipeline = _get_fallback_classifier()
        probs = pipeline.predict_proba([text])[0]
        classes = pipeline.classes_
        best_idx = probs.argmax()
        confidence = float(probs[best_idx])
        label = classes[best_idx]

    if confidence < CONFIDENCE_THRESHOLD:
        return "fallback", confidence
    return label, confidence
