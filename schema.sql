-- ============================================================
-- RepoAlpha — Enterprise Schema by Shanujan
-- Run in Supabase > SQL Editor
-- Adds: pipeline_runs, alert_log, watchlist, score_history,
--        repo_embeddings (pgvector), and match_repos() RPC function
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector (free on Supabase)

-- ────────────────────────────────────────────────────────────
-- EXISTING TABLES (from v1 schema — idempotent)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS repositories (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  full_name           TEXT NOT NULL UNIQUE,
  name                TEXT,
  owner               TEXT,
  description         TEXT,
  language            TEXT,
  stars_count         INTEGER DEFAULT 0,
  forks_count         INTEGER DEFAULT 0,
  url                 TEXT,
  trending_rank       INTEGER DEFAULT 0,
  corporate_score     INTEGER DEFAULT 0,
  ai_hype_score       INTEGER DEFAULT 0,
  commercial_summary  TEXT,
  tech_vibe           TEXT,
  market_category     TEXT,
  license_type        TEXT,
  license_label       TEXT,
  license_color       TEXT DEFAULT 'yellow',
  rating              TEXT DEFAULT 'SELL',
  hiring_dossier      JSONB,
  scraped_at          TIMESTAMPTZ,
  analyzed_at         TIMESTAMPTZ,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stargazers (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  repo_id         UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  github_login    TEXT NOT NULL,
  avatar_url      TEXT,
  profile_url     TEXT,
  starred_at      TIMESTAMPTZ,
  company         TEXT,
  bio             TEXT,
  email           TEXT,
  location        TEXT,
  company_score   INTEGER DEFAULT 0,
  enriched        BOOLEAN DEFAULT FALSE,
  enriched_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (repo_id, github_login)
);

CREATE TABLE IF NOT EXISTS corporate_signals (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  repo_id         UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  stargazer_id    UUID NOT NULL REFERENCES stargazers(id) ON DELETE CASCADE,
  github_login    TEXT,
  company         TEXT NOT NULL,
  signal_score    INTEGER NOT NULL,
  raw_bio         TEXT,
  detected_at     TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (repo_id, stargazer_id)
);

-- ────────────────────────────────────────────────────────────
-- NEW: pipeline_runs — Agent execution audit log
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  phase                 TEXT NOT NULL,          -- harvest | enrich | analyze | alert
  status                TEXT NOT NULL,          -- started | success | failed | partial
  repos_processed       INTEGER DEFAULT 0,
  stargazers_processed  INTEGER DEFAULT 0,
  signals_detected      INTEGER DEFAULT 0,
  alerts_fired          INTEGER DEFAULT 0,
  error_message         TEXT,
  duration_seconds      FLOAT,
  started_at            TIMESTAMPTZ DEFAULT NOW(),
  finished_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_phase   ON pipeline_runs(phase);


-- ────────────────────────────────────────────────────────────
-- NEW: alert_log — De-duplicate fired alerts
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_log (
  id                       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  repo_full_name           TEXT NOT NULL,
  repo_id                  UUID REFERENCES repositories(id) ON DELETE CASCADE,
  corporate_score_at_alert INTEGER,
  rating                   TEXT,
  channels_notified        INTEGER DEFAULT 0,
  alerted_at               TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_log_repo ON alert_log(repo_full_name);
CREATE INDEX IF NOT EXISTS idx_alert_log_ts   ON alert_log(alerted_at DESC);


-- ────────────────────────────────────────────────────────────
-- NEW: watchlist — User-pinned repos for tracking
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist (
  id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id          TEXT NOT NULL,         -- Supabase auth uid or session id
  repo_full_name   TEXT NOT NULL,
  alert_threshold  INTEGER DEFAULT 30,
  note             TEXT,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (user_id, repo_full_name)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id);


-- ────────────────────────────────────────────────────────────
-- NEW: score_history — Time-series corporate scores for charts
-- Snapshots the corporate_score every pipeline run.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS score_history (
  id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  repo_id          UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  full_name        TEXT NOT NULL,
  corporate_score  INTEGER NOT NULL,
  ai_hype_score    INTEGER DEFAULT 0,
  stars_count      INTEGER DEFAULT 0,
  recorded_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_score_history_repo ON score_history(repo_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_score_history_ts   ON score_history(recorded_at DESC);


-- ────────────────────────────────────────────────────────────
-- NEW: repo_embeddings — pgvector semantic search
-- 384-dim vectors from sentence-transformers all-MiniLM-L6-v2
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS repo_embeddings (
  id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  repo_id   UUID NOT NULL UNIQUE REFERENCES repositories(id) ON DELETE CASCADE,
  embedding vector(384) NOT NULL,
  model     TEXT DEFAULT 'all-MiniLM-L6-v2',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- IVFFlat index for approximate nearest-neighbor search
-- lists=100 is a good starting point for <100k vectors
CREATE INDEX IF NOT EXISTS idx_repo_embeddings_ivfflat
  ON repo_embeddings USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);


-- ────────────────────────────────────────────────────────────
-- RPC FUNCTION: match_repos — Vector similarity search
-- Called by utils/vector.py find_similar_repos()
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION match_repos(
  query_embedding vector(384),
  match_threshold float DEFAULT 0.7,
  match_count     int   DEFAULT 5
)
RETURNS TABLE (
  full_name        text,
  description      text,
  corporate_score  int,
  ai_hype_score    int,
  url              text,
  similarity       float
)
LANGUAGE sql STABLE
AS $$
  SELECT
    r.full_name,
    r.description,
    r.corporate_score,
    r.ai_hype_score,
    r.url,
    1 - (re.embedding <=> query_embedding) AS similarity
  FROM repo_embeddings re
  JOIN repositories r ON r.id = re.repo_id
  WHERE 1 - (re.embedding <=> query_embedding) > match_threshold
  ORDER BY re.embedding <=> query_embedding
  LIMIT match_count;
$$;


-- ────────────────────────────────────────────────────────────
-- TRIGGER: auto-snapshot score history on repo update
-- Fires when corporate_score changes — zero maintenance time-series
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION snapshot_score()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.corporate_score IS DISTINCT FROM OLD.corporate_score THEN
    INSERT INTO score_history (repo_id, full_name, corporate_score, ai_hype_score, stars_count)
    VALUES (NEW.id, NEW.full_name, NEW.corporate_score, NEW.ai_hype_score, NEW.stars_count);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_snapshot_score ON repositories;
CREATE TRIGGER trg_snapshot_score
  AFTER UPDATE OF corporate_score ON repositories
  FOR EACH ROW EXECUTE FUNCTION snapshot_score();


-- ────────────────────────────────────────────────────────────
-- VIEWS
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW repo_intelligence AS
SELECT
  r.*,
  COUNT(DISTINCT s.id)   AS total_stargazers_scraped,
  COUNT(DISTINCT cs.id)  AS total_corp_signals,
  COUNT(DISTINCT cs.company) FILTER (WHERE cs.company IS NOT NULL) AS unique_companies,
  ARRAY_AGG(DISTINCT cs.company ORDER BY cs.company)
    FILTER (WHERE cs.company IS NOT NULL) AS adopter_companies
FROM repositories r
LEFT JOIN stargazers       s  ON s.repo_id  = r.id
LEFT JOIN corporate_signals cs ON cs.repo_id = r.id
GROUP BY r.id;


-- ────────────────────────────────────────────────────────────
-- ROW LEVEL SECURITY
-- ────────────────────────────────────────────────────────────
ALTER TABLE repositories      ENABLE ROW LEVEL SECURITY;
ALTER TABLE stargazers         ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_signals  ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_runs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_log          ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist          ENABLE ROW LEVEL SECURITY;
ALTER TABLE score_history      ENABLE ROW LEVEL SECURITY;
ALTER TABLE repo_embeddings    ENABLE ROW LEVEL SECURITY;

-- Public read for all intelligence tables
CREATE POLICY "Public read" ON repositories      FOR SELECT USING (true);
CREATE POLICY "Public read" ON stargazers         FOR SELECT USING (true);
CREATE POLICY "Public read" ON corporate_signals  FOR SELECT USING (true);
CREATE POLICY "Public read" ON pipeline_runs      FOR SELECT USING (true);
CREATE POLICY "Public read" ON alert_log          FOR SELECT USING (true);
CREATE POLICY "Public read" ON score_history      FOR SELECT USING (true);
CREATE POLICY "Public read" ON repo_embeddings    FOR SELECT USING (true);

-- Watchlist: users can only see their own entries
CREATE POLICY "Own watchlist" ON watchlist
  FOR ALL USING (user_id = current_setting('request.jwt.claims', true)::json->>'sub');
