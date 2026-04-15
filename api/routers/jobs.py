import os
import uuid
from typing import List
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import aiofiles
import structlog

from database.connection import get_db
from shared.models import JobCreateRequest, JobResponse
from shared.utils import sanitize_filename, truncate_text
from config.settings import settings
from agents.matching_agent import MatchingAgent

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post("/", response_model=JobResponse, status_code=201)
async def create_job(
    title:      str = Form(...),
    department: str = Form(None),
    location:   str = Form(None),
    created_by: str = Form(...),
    jd_file:    UploadFile = File(...),
    db:         AsyncSession = Depends(get_db),
):
    """
    Upload a Job Description.
    Accepts a text file or PDF containing the JD.
    Generates and stores the JD embedding for future candidate matching.
    """
    # Read JD content
    content = await jd_file.read()
    jd_text = content.decode("utf-8", errors="replace")

    if len(jd_text.strip()) < 100:
        raise HTTPException(400, "Job description is too short. Please upload a complete JD.")

    # Generate JD embedding
    agent  = MatchingAgent(db)
    jd_emb = await agent._embed(truncate_text(jd_text, 4000))

    # Store in DB
    job_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO jobs (id, title, department, location, description_raw, embedding, created_by)
            VALUES (:id, :title, :dept, :loc, :desc, :emb::vector, :by)
        """),
        {
            "id":    job_id,
            "title": title,
            "dept":  department,
            "loc":   location,
            "desc":  jd_text,
            "emb":   str(jd_emb),
            "by":    created_by,
        },
    )

    # Save JD file to disk
    filename = sanitize_filename(jd_file.filename)
    jd_path  = os.path.join(settings.upload_dir, "jds", f"{job_id}_{filename}")
    async with aiofiles.open(jd_path, "wb") as f:
        await f.write(content)

    logger.info("job_created", job_id=job_id, title=title)

    result = await db.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id})
    return dict(result.mappings().fetchone())


@router.get("/", response_model=List[dict])
async def list_jobs(active_only: bool = True, db: AsyncSession = Depends(get_db)):
    """List all job postings."""
    query = "SELECT id, title, department, location, is_active, created_at FROM jobs"
    if active_only:
        query += " WHERE is_active = TRUE"
    query += " ORDER BY created_at DESC"
    result = await db.execute(text(query))
    return [dict(r) for r in result.mappings()]


@router.get("/{job_id}/pipeline")
async def get_job_pipeline(job_id: str, db: AsyncSession = Depends(get_db)):
    """Get full hiring pipeline stats for a job."""
    result = await db.execute(
        text("SELECT * FROM v_job_pipeline WHERE id = :id"),
        {"id": job_id},
    )
    row = result.mappings().fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    return dict(row)
