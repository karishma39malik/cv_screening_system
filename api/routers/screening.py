import os
import uuid
import asyncio
from typing import List
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import aiofiles
import structlog

from database.connection import get_db, AsyncSessionLocal
from shared.models import BulkUploadResponse
from shared.utils import sanitize_filename, compute_file_hash, get_file_extension
from config.settings import settings
from agents.ingestion_agent import IngestionAgent
from agents.matching_agent import MatchingAgent
from agents.potential_agent import PotentialAgent
from agents.validation_agent import ValidationAgent

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post("/upload", response_model=BulkUploadResponse)
async def upload_cvs(
    job_id:          str = Form(...),
    uploaded_by:     str = Form(...),
    cv_files:        List[UploadFile] = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db:              AsyncSession = Depends(get_db),
):
    """
    Bulk CV upload endpoint.
    - Accepts up to MAX_BULK_UPLOAD files per request
    - Validates format and size
    - Saves files to disk
    - Queues background processing (non-blocking)
    - Returns immediately with correlation IDs for tracking
    """
    if len(cv_files) > settings.max_bulk_upload:
        raise HTTPException(400, f"Too many files. Maximum is {settings.max_bulk_upload} per upload.")

    # Verify job exists
    job_result = await db.execute(text("SELECT id, description_raw FROM jobs WHERE id = :id"), {"id": job_id})
    job = job_result.mappings().fetchone()
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    queued          = []
    failed          = []
    correlation_ids = []

    for cv_file in cv_files:
        correlation_id = str(uuid.uuid4())

        # ---- Validate extension ----
        ext = get_file_extension(cv_file.filename)
        if ext not in settings.allowed_ext_list:
            failed.append({"filename": cv_file.filename, "error": f"Unsupported format: {ext}"})
            continue

        # ---- Validate file size ----
        content = await cv_file.read()
        size_mb = len(content) / (1024 * 1024)
        if size_mb > settings.max_file_size_mb:
            failed.append({"filename": cv_file.filename, "error": f"File too large: {size_mb:.1f}MB"})
            continue

        # ---- Save to disk ----
        safe_name = sanitize_filename(cv_file.filename)
        file_path = os.path.join(settings.upload_dir, "cvs", f"{correlation_id}_{safe_name}")
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)

        file_hash = compute_file_hash(content)

        # ---- Create cv_version record (status: pending) ----
        cv_version_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO cv_versions
                    (id, job_id, original_filename, stored_path, file_format,
                     file_size_bytes, file_hash, ingestion_status, correlation_id)
                VALUES
                    (:id, :job_id, :fname, :path, :fmt, :size, :hash, 'pending', :cid)
            """),
            {
                "id":     cv_version_id,
                "job_id": job_id,
                "fname":  cv_file.filename,
                "path":   file_path,
                "fmt":    ext,
                "size":   len(content),
                "hash":   file_hash,
                "cid":    correlation_id,
            },
        )

        queued.append(cv_version_id)
        correlation_ids.append(correlation_id)

        # ---- Queue background processing ----
        background_tasks.add_task(
            process_cv_pipeline,
            cv_version_id=cv_version_id,
            job_id=job_id,
            file_path=file_path,
            filename=cv_file.filename,
            file_hash=file_hash,
            correlation_id=correlation_id,
        )

    logger.info("bulk_upload_complete",
        total=len(cv_files), queued=len(queued), failed=len(failed))

    return BulkUploadResponse(
        total_received=len(cv_files),
        queued=len(queued),
        failed=len(failed),
        correlation_ids=correlation_ids,
        errors=failed,
    )


async def process_cv_pipeline(
    cv_version_id: str,
    job_id: str,
    file_path: str,
    filename: str,
    file_hash: str,
    correlation_id: str,
) -> None:
    """
    Full agentic pipeline for a single CV.
    Runs in background — failures here NEVER affect other CVs.

    Pipeline:
    1. IngestionAgent  → parse CV → structured JSON
    2. Find/create Candidate record (dedup by email)
    3. MatchingAgent   → semantic scoring + LLM rationale
    4. PotentialAgent  → growth & value-add insights
    5. ValidationAgent → anomaly detection
    6. Store ScreeningResult in DB
    """
    bound_log = logger.bind(correlation_id=correlation_id, filename=filename)
    bound_log.info("pipeline_start")

    # Each background task gets its own DB session
    async with AsyncSessionLocal() as db:
        try:
            # ---- UPDATE STATUS: processing ----
            await db.execute(
                text("UPDATE cv_versions SET ingestion_status = 'processing' WHERE id = :id"),
                {"id": cv_version_id},
            )
            await db.commit()

            # ---- 1. INGESTION & PARSING ----
            ingestion = IngestionAgent(db)
            ingestion_result = await ingestion.run(
                payload={"file_path": file_path, "filename": filename},
                correlation_id=correlation_id,
            )
            parsed_cv = ingestion_result["parsed_cv"]

            # ---- 2. FIND OR CREATE CANDIDATE ----
            candidate_id = await _upsert_candidate(db, parsed_cv, correlation_id)

            # Link cv_version to candidate
            await db.execute(
                text("""
                    UPDATE cv_versions
                    SET candidate_id = :cid,
                        parsed_data = :data::jsonb,
                        parse_confidence = :conf,
                        embedding_model = :emodel,
                        ingestion_status = 'done',
                        processed_at = NOW()
                    WHERE id = :id
                """),
                {
                    "cid":    candidate_id,
                    "data":   str(parsed_cv),
                    "conf":   parsed_cv.get("parse_confidence", 0.5),
                    "emodel": settings.ollama_embed_model,
                    "id":     cv_version_id,
                },
            )
            await db.commit()

            # ---- 3. FETCH JD ----
            jd_result = await db.execute(
                text("SELECT description_raw FROM jobs WHERE id = :id"), {"id": job_id}
            )
            jd_row = jd_result.fetchone()
            job_description = jd_row.description_raw if jd_row else ""

            # ---- 4. SEMANTIC MATCHING ----
            matcher = MatchingAgent(db)
            match_result = await matcher.run(
                payload={
                    "cv_version_id":  cv_version_id,
                    "job_id":         job_id,
                    "candidate_id":   candidate_id,
                    "parsed_cv":      parsed_cv,
                    "job_description": job_description,
                },
                correlation_id=correlation_id,
            )

            # ---- 5. POTENTIAL ANALYSIS ----
            potential = PotentialAgent(db)
            potential_result = await potential.run(
                payload={"parsed_cv": parsed_cv},
                correlation_id=correlation_id,
            )
            value_add_insights = potential_result.get("value_add_insights", [])
            potential_label    = potential_result.get("growth_potential_label", "")

            # ---- 6. VALIDATION & ANOMALY DETECTION ----
            validator = ValidationAgent(db)
            validation_result = await validator.run(
                payload={
                    "file_hash":        file_hash,
                    "cv_version_id":    cv_version_id,
                    "candidate_id":     candidate_id,
                    "job_id":           job_id,
                    "parsed_cv":        parsed_cv,
                    "composite_score":  match_result["composite_score"],
                    "parse_confidence": parsed_cv.get("parse_confidence", 0.5),
                },
                correlation_id=correlation_id,
            )

            # ---- 7. STORE SCREENING RESULT ----
            screening_id = str(uuid.uuid4())
            # Combine rationale with potential insights
            full_rationale = match_result["llm_rationale"]
            if potential_label:
                full_rationale += f"\n\nPotential Assessment: {potential_label}. "
                full_rationale += potential_result.get("potential_score_rationale", "")

            await db.execute(
                text("""
                    INSERT INTO screenings
                        (id, cv_version_id, job_id, candidate_id,
                         semantic_similarity, relevance_score, potential_score, composite_score,
                         strengths, gaps, transferable_skills, value_add_insights, llm_rationale,
                         decision)
                    VALUES
                        (:id, :cv_id, :job_id, :cand_id,
                         :sim, :rel, :pot, :comp,
                         :str::jsonb, :gaps::jsonb, :trans::jsonb, :vai::jsonb, :rat,
                         :decision)
                """),
                {
                    "id":       screening_id,
                    "cv_id":    cv_version_id,
                    "job_id":   job_id,
                    "cand_id":  candidate_id,
                    "sim":      match_result["semantic_similarity"],
                    "rel":      match_result["relevance_score"],
                    "pot":      match_result.get("potential_score", 0.5),
                    "comp":     match_result["composite_score"],
                    "str":      str(match_result["strengths"]),
                    "gaps":     str(match_result["gaps"]),
                    "trans":    str(match_result["transferable_skills"]),
                    "vai":      str(value_add_insights),
                    "rat":      full_rationale,
                    "decision": "needs_review" if validation_result["requires_review"] else "needs_review",
                },
            )

            # ---- UPDATE CANDIDATE STATUS ----
            await db.execute(
                text("UPDATE candidates SET current_status = 'screened', last_updated_at = NOW() WHERE id = :id"),
                {"id": candidate_id},
            )

            await db.commit()
            bound_log.info("pipeline_complete", screening_id=screening_id,
                composite_score=match_result["composite_score"])

        except Exception as e:
            bound_log.error("pipeline_failed", error=str(e))
            # Mark cv_version as failed — do NOT re-raise (must not stop other CVs)
            try:
                await db.rollback()
                await db.execute(
                    text("UPDATE cv_versions SET ingestion_status = 'failed' WHERE id = :id"),
                    {"id": cv_version_id},
                )
                await db.commit()
            except Exception as inner_e:
                bound_log.error("pipeline_failure_cleanup_failed", error=str(inner_e))


async def _upsert_candidate(db, parsed_cv: dict, correlation_id: str) -> str:
    """
    Find existing candidate by email or create new one.
    This is how we deduplicate candidates across multiple job applications.
    """
    email = parsed_cv.get("email")

    if email:
        result = await db.execute(
            text("SELECT id, is_returning FROM candidates WHERE email = :email"),
            {"email": email.lower().strip()},
        )
        existing = result.fetchone()

        if existing:
            # Mark as returning candidate
            await db.execute(
                text("UPDATE candidates SET is_returning = TRUE, last_updated_at = NOW() WHERE id = :id"),
                {"id": str(existing.id)},
            )
            await db.commit()
            return str(existing.id)

    # Create new candidate
    candidate_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO candidates (id, full_name, email, phone, linkedin_url, current_status)
            VALUES (:id, :name, :email, :phone, :linkedin, 'new')
        """),
        {
            "id":       candidate_id,
            "name":     parsed_cv.get("full_name"),
            "email":    email.lower().strip() if email else None,
            "phone":    parsed_cv.get("phone"),
            "linkedin": parsed_cv.get("linkedin_url"),
        },
    )
    await db.commit()
    return candidate_id


@router.get("/results/{job_id}")
async def get_screening_results(
    job_id:    str,
    min_score: float = 0.0,
    limit:     int   = 50,
    db:        AsyncSession = Depends(get_db),
):
    """
    Get ranked screening results for a job.
    Results are ordered by composite_score descending.
    """
    result = await db.execute(
        text("""
            SELECT
                s.id AS screening_id,
                s.composite_score,
                s.semantic_similarity,
                s.relevance_score,
                s.potential_score,
                s.strengths,
                s.gaps,
                s.transferable_skills,
                s.value_add_insights,
                s.llm_rationale,
                s.decision,
                s.screened_at,
                c.id AS candidate_id,
                c.full_name,
                c.email,
                c.current_status,
                c.is_returning,
                cv.original_filename,
                cv.stored_path,
                cv.parse_confidence,
                cv.ingestion_status,
                (SELECT COUNT(*) FROM anomalies a WHERE a.cv_version_id = cv.id) AS anomaly_count
            FROM screenings s
            JOIN candidates c    ON c.id = s.candidate_id
            JOIN cv_versions cv  ON cv.id = s.cv_version_id
            WHERE s.job_id = :job_id
              AND s.composite_score >= :min_score
            ORDER BY s.composite_score DESC
            LIMIT :lim
        """),
        {"job_id": job_id, "min_score": min_score, "lim": limit},
    )
    rows = result.mappings().fetchall()

    # Update rankings in DB
    for i, row in enumerate(rows, start=1):
        await db.execute(
            text("UPDATE screenings SET rank_in_pool = :rank, total_in_pool = :total WHERE id = :id"),
            {"rank": i, "total": len(rows), "id": row["screening_id"]},
        )

    return [dict(r) for r in rows]


@router.patch("/{screening_id}/decision")
async def update_decision(
    screening_id: str,
    decision:     str = Form(...),   # hr_approved / hr_rejected / hr_hold / forwarded
    decision_by:  str = Form(...),
    notes:        str = Form(None),
    db:           AsyncSession = Depends(get_db),
):
    """
    HR makes a final decision on a candidate.
    All decisions are audit-logged.
    """
    valid_decisions = ["hr_approved", "hr_rejected", "hr_hold", "forwarded"]
    if decision not in valid_decisions:
        raise HTTPException(400, f"Invalid decision. Must be one of: {valid_decisions}")

    await db.execute(
        text("""
            UPDATE screenings
            SET decision = :dec, decision_by = :by, decision_at = NOW(), decision_notes = :notes
            WHERE id = :id
        """),
        {"dec": decision, "by": decision_by, "notes": notes, "id": screening_id},
    )

    # Log this HR decision to audit trail
    await db.execute(
        text("""
            INSERT INTO audit_logs (event_type, actor, event_data, outcome)
            VALUES ('hr_decision', :actor, :data::jsonb, 'success')
        """),
        {
            "actor": f"hr:{decision_by}",
            "data":  str({"screening_id": screening_id, "decision": decision, "notes": notes}),
        },
    )

    logger.info("hr_decision_recorded",
        screening_id=screening_id, decision=decision, by=decision_by)

    return {"message": "Decision recorded", "screening_id": screening_id, "decision": decision}
