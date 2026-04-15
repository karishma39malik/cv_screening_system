-- Enable pgvector extension (MUST be first)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For fuzzy text search

-- ============================================================
-- ENUM TYPES
-- ============================================================
CREATE TYPE candidate_status AS ENUM (
    'new', 'parsing', 'parsed', 'screened',
    'shortlisted', 'interview_scheduled', 'interviewed',
    'offered', 'hired', 'rejected', 'on_hold', 'withdrawn'
);

CREATE TYPE screening_decision AS ENUM (
    'auto_shortlisted', 'auto_rejected', 'needs_review',
    'hr_approved', 'hr_rejected', 'hr_hold', 'forwarded'
);

CREATE TYPE file_format AS ENUM ('pdf', 'docx', 'txt', 'unknown');

CREATE TYPE anomaly_type AS ENUM (
    'duplicate_cv', 'employment_gap', 'inconsistent_dates',
    'missing_required_skills', 'low_parse_confidence',
    'suspicious_pattern', 'borderline_score'
);

-- ============================================================
-- TABLE: jobs
-- Stores all job descriptions
-- ============================================================
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,
    department      TEXT,
    location        TEXT,
    description_raw TEXT NOT NULL,           -- Original JD text
    requirements    JSONB,                   -- Parsed required skills/exp
    embedding       vector(768),             -- JD semantic vector
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    created_by      TEXT NOT NULL            -- HR user who posted
);

-- ============================================================
-- TABLE: candidates
-- Master candidate record — deduplicated across submissions
-- ============================================================
CREATE TABLE candidates (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Identity (used for deduplication)
    email           TEXT UNIQUE,
    full_name       TEXT,
    phone           TEXT,
    linkedin_url    TEXT,
    
    -- Status tracking
    current_status  candidate_status DEFAULT 'new',
    
    -- Timestamps
    first_seen_at   TIMESTAMPTZ DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Metadata
    source          TEXT,                   -- 'portal', 'linkedin', 'email', 'manual'
    is_returning    BOOLEAN DEFAULT FALSE,  -- Has applied before
    notes           TEXT                    -- HR free-text notes
);

-- ============================================================
-- TABLE: cv_versions
-- One candidate may submit multiple CVs over time
-- ============================================================
CREATE TABLE cv_versions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_id        UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    job_id              UUID REFERENCES jobs(id),
    
    -- File metadata
    original_filename   TEXT NOT NULL,
    stored_path         TEXT NOT NULL,       -- Local filesystem path
    file_format         file_format NOT NULL,
    file_size_bytes     INTEGER,
    file_hash           TEXT NOT NULL,       -- SHA256 for dedup
    
    -- Parsing outputs
    parsed_data         JSONB,               -- Full structured extraction
    parse_confidence    FLOAT,               -- 0.0 to 1.0
    parse_errors        JSONB,               -- Array of error objects
    
    -- Embeddings
    embedding           vector(768),         -- CV semantic vector
    embedding_model     TEXT,                -- Which model generated it
    
    -- Processing state
    ingestion_status    TEXT DEFAULT 'pending',  -- pending/processing/done/failed
    correlation_id      UUID DEFAULT uuid_generate_v4(),  -- For log tracing
    
    -- Timestamps
    uploaded_at         TIMESTAMPTZ DEFAULT NOW(),
    processed_at        TIMESTAMPTZ
);

-- ============================================================
-- TABLE: screenings
-- Each time a CV is evaluated against a JD
-- ============================================================
CREATE TABLE screenings (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cv_version_id       UUID NOT NULL REFERENCES cv_versions(id),
    job_id              UUID NOT NULL REFERENCES jobs(id),
    candidate_id        UUID NOT NULL REFERENCES candidates(id),
    
    -- Semantic scores (0.0 to 1.0, not percentages)
    semantic_similarity FLOAT,              -- Raw vector cosine similarity
    relevance_score     FLOAT,              -- LLM-assessed job relevance
    potential_score     FLOAT,              -- Growth/adaptability signal
    composite_score     FLOAT,              -- Weighted final score
    
    -- Rank within this job's pool
    rank_in_pool        INTEGER,
    total_in_pool       INTEGER,
    
    -- Explainability (CRITICAL for compliance)
    strengths           JSONB,              -- Array of strength strings
    gaps                JSONB,              -- Array of gap strings
    transferable_skills JSONB,              -- Inferred from adjacent domains
    value_add_insights  JSONB,              -- From Potential Agent
    llm_rationale       TEXT,              -- Full LLM explanation paragraph
    
    -- HR Decision
    decision            screening_decision DEFAULT 'needs_review',
    decision_by         TEXT,              -- HR username
    decision_at         TIMESTAMPTZ,
    decision_notes      TEXT,              -- HR free-text rationale
    
    -- Timestamps
    screened_at         TIMESTAMPTZ DEFAULT NOW(),
    
    -- Ensure one screening per CV per job
    UNIQUE (cv_version_id, job_id)
);

-- ============================================================
-- TABLE: anomalies
-- Flags raised by the Validation Agent
-- ============================================================
CREATE TABLE anomalies (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cv_version_id   UUID REFERENCES cv_versions(id),
    candidate_id    UUID REFERENCES candidates(id),
    job_id          UUID REFERENCES jobs(id),
    
    anomaly_type    anomaly_type NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    description     TEXT NOT NULL,          -- Human-readable explanation
    raw_evidence    JSONB,                  -- Supporting data
    
    -- Resolution
    is_resolved     BOOLEAN DEFAULT FALSE,
    resolved_by     TEXT,
    resolved_at     TIMESTAMPTZ,
    resolution_note TEXT,
    
    detected_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- TABLE: interviews
-- Records interview scheduling and feedback
-- ============================================================
CREATE TABLE interviews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    screening_id    UUID NOT NULL REFERENCES screenings(id),
    candidate_id    UUID NOT NULL REFERENCES candidates(id),
    job_id          UUID NOT NULL REFERENCES jobs(id),
    
    scheduled_at    TIMESTAMPTZ,
    interviewed_at  TIMESTAMPTZ,
    interviewer     TEXT,
    format          TEXT,                   -- 'phone', 'video', 'onsite', 'panel'
    
    -- Feedback (structured)
    technical_score     INTEGER CHECK (technical_score BETWEEN 1 AND 5),
    communication_score INTEGER CHECK (communication_score BETWEEN 1 AND 5),
    cultural_fit_score  INTEGER CHECK (cultural_fit_score BETWEEN 1 AND 5),
    overall_score       INTEGER CHECK (overall_score BETWEEN 1 AND 5),
    feedback_notes      TEXT,
    recommendation      TEXT CHECK (recommendation IN ('hire', 'reject', 'second_round', 'hold')),
    
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- TABLE: audit_logs
-- IMMUTABLE record of every system action (append-only)
-- ============================================================
CREATE TABLE audit_logs (
    id              BIGSERIAL PRIMARY KEY,   -- Sequential for ordering
    
    -- Context
    correlation_id  UUID,                    -- Links to cv_version correlation_id
    event_type      TEXT NOT NULL,           -- 'ingestion', 'parse', 'embed', 'match', 'decision'
    actor           TEXT NOT NULL,           -- 'system', 'agent:ingestion', 'hr:username'
    
    -- References (nullable — not all events have all entities)
    candidate_id    UUID,
    cv_version_id   UUID,
    job_id          UUID,
    screening_id    UUID,
    
    -- Event data
    event_data      JSONB NOT NULL,          -- Full structured event payload
    outcome         TEXT,                    -- 'success', 'failure', 'warning'
    error_message   TEXT,                    -- If outcome = failure
    duration_ms     INTEGER,                 -- Processing time
    
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Prevent audit log modification (compliance)
-- In production, consider a separate append-only role
REVOKE UPDATE, DELETE ON audit_logs FROM PUBLIC;

-- ============================================================
-- INDEXES for query performance
-- ============================================================

-- Vector similarity search indexes (IVFFlat for large scale)
CREATE INDEX idx_cv_embedding      ON cv_versions USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_job_embedding     ON jobs        USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- Standard B-tree indexes
CREATE INDEX idx_candidates_email  ON candidates  (email);
CREATE INDEX idx_cvv_candidate     ON cv_versions (candidate_id);
CREATE INDEX idx_cvv_job           ON cv_versions (job_id);
CREATE INDEX idx_cvv_hash          ON cv_versions (file_hash);
CREATE INDEX idx_screenings_job    ON screenings  (job_id, composite_score DESC);
CREATE INDEX idx_screenings_cand   ON screenings  (candidate_id);
CREATE INDEX idx_audit_correlation ON audit_logs  (correlation_id);
CREATE INDEX idx_audit_event_type  ON audit_logs  (event_type, created_at DESC);
CREATE INDEX idx_anomaly_cv        ON anomalies   (cv_version_id);

-- GIN indexes for JSONB search
CREATE INDEX idx_cv_parsed_data    ON cv_versions USING GIN (parsed_data);
CREATE INDEX idx_screening_strengths ON screenings USING GIN (strengths);

-- ============================================================
-- VIEWS for HR dashboard queries
-- ============================================================

CREATE VIEW v_candidate_summary AS
SELECT
    c.id,
    c.full_name,
    c.email,
    c.current_status,
    c.is_returning,
    c.source,
    COUNT(DISTINCT cv.id)     AS cv_count,
    COUNT(DISTINCT s.job_id)  AS jobs_applied,
    MAX(s.composite_score)    AS best_score,
    c.first_seen_at
FROM candidates c
LEFT JOIN cv_versions cv ON cv.candidate_id = c.id
LEFT JOIN screenings s   ON s.candidate_id = c.id
GROUP BY c.id;

CREATE VIEW v_job_pipeline AS
SELECT
    j.id,
    j.title,
    j.department,
    COUNT(s.id)                                              AS total_screened,
    COUNT(s.id) FILTER (WHERE s.decision = 'hr_approved')   AS shortlisted,
    COUNT(s.id) FILTER (WHERE s.decision = 'hr_rejected')   AS rejected,
    COUNT(s.id) FILTER (WHERE s.decision = 'needs_review')  AS pending_review,
    AVG(s.composite_score)                                   AS avg_score
FROM jobs j
LEFT JOIN screenings s ON s.job_id = j.id
GROUP BY j.id;
