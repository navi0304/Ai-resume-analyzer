from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

_MODEL = None
if SentenceTransformer is not None:
    try:
        _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        _MODEL = None


def rank_resumes(resumes: list[str], job_description: str):
    """Return a score matrix where each row maps to one resume."""
    if not resumes:
        return []

    cleaned_resumes = [text.strip() if text and text.strip() else "resume" for text in resumes]
    jd_text = job_description.strip()
    if not jd_text:
        return [[0.0] for _ in cleaned_resumes]

    if _MODEL is not None:
        jd_embedding = _MODEL.encode([jd_text])
        resume_embeddings = _MODEL.encode(cleaned_resumes)
        return cosine_similarity(resume_embeddings, jd_embedding)

    documents = cleaned_resumes + [jd_text]
    matrix = TfidfVectorizer(stop_words="english").fit_transform(documents)
    resume_vectors = matrix[:-1]
    jd_vector = matrix[-1:]
    return cosine_similarity(resume_vectors, jd_vector)
