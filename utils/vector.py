"""
utils/vector.py — Semantic Repo Clustering with pgvector
RepoAlpha

Supabase's free tier ships with the pgvector extension, giving us
a zero-cost vector database. We use it to:

  1. Embed repo descriptions + tech vibes → 768-dim vectors
  2. Find semantically similar repos (clustering for M&A)
  3. "Repos like this one" recommendations in the dashboard

Embedding model: sentence-transformers/all-MiniLM-L6-v2
  → runs locally, no API cost, 384-dim output
  → fast: ~50ms per sentence on CPU
"""

import logging
from typing import Optional

log = logging.getLogger("vector")

try:
    from sentence_transformers import SentenceTransformer
    _MODEL: Optional[SentenceTransformer] = None
    VECTOR_AVAILABLE = True
except ImportError:
    VECTOR_AVAILABLE = False
    log.warning(
        "sentence-transformers not installed. "
        "Run: pip install sentence-transformers "
        "to enable semantic search. Falling back to keyword search."
    )


def get_model() -> "SentenceTransformer":
    """Lazy-load the embedding model (downloaded once, ~90MB)."""
    global _MODEL
    if _MODEL is None:
        log.info("Loading embedding model: all-MiniLM-L6-v2...")
        _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        log.info("Embedding model ready.")
    return _MODEL


def embed_repo(repo: dict) -> Optional[list[float]]:
    """
    Creates a semantic embedding for a repository.
    Combines multiple text fields for richer signal.
    Returns a list of 384 floats, or None if model unavailable.
    """
    if not VECTOR_AVAILABLE:
        return None

    # Combine the most semantically rich fields
    text = " ".join(filter(None, [
        repo.get("name", ""),
        repo.get("description", ""),
        repo.get("tech_vibe", ""),
        repo.get("market_category", ""),
        repo.get("commercial_summary", ""),
        repo.get("language", ""),
    ]))

    if not text.strip():
        return None

    model = get_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def upsert_repo_embedding(supabase_client, repo_id: str, repo: dict) -> bool:
    """
    Generates and stores a repo's embedding in the `repo_embeddings` table.
    Supabase's pgvector extension handles storage and ANN search natively.
    """
    embedding = embed_repo(repo)
    if embedding is None:
        return False

    try:
        supabase_client.table("repo_embeddings").upsert({
            "repo_id": repo_id,
            "embedding": embedding,  # pgvector accepts a Python list
            "model": "all-MiniLM-L6-v2",
        }, on_conflict="repo_id").execute()
        return True
    except Exception as e:
        log.error(f"Embedding upsert failed for {repo_id}: {e}")
        return False


def find_similar_repos(supabase_client, repo: dict, top_k: int = 5) -> list[dict]:
    """
    Finds the top-k semantically similar repos using pgvector cosine similarity.
    Falls back to market_category filter if vectors unavailable.
    """
    if not VECTOR_AVAILABLE:
        # Keyword fallback: same market category, sorted by score
        result = (
            supabase_client.table("repositories")
            .select("full_name, description, corporate_score, ai_hype_score, url")
            .eq("market_category", repo.get("market_category", "Other"))
            .neq("full_name", repo.get("full_name", ""))
            .order("corporate_score", desc=True)
            .limit(top_k)
            .execute()
        )
        return result.data

    embedding = embed_repo(repo)
    if not embedding:
        return []

    # Supabase RPC call to pgvector's <=> cosine distance operator
    # This runs the ANN search on the database side — zero client compute.
    try:
        result = supabase_client.rpc(
            "match_repos",
            {
                "query_embedding": embedding,
                "match_threshold": 0.7,
                "match_count": top_k + 1,  # +1 to exclude self
            },
        ).execute()

        # Filter out the queried repo itself
        return [
            r for r in result.data
            if r.get("full_name") != repo.get("full_name")
        ][:top_k]

    except Exception as e:
        log.error(f"pgvector search failed: {e}")
        return []
