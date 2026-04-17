import json
from typing import Any, Dict, List

import ollama as ollama_client
import structlog
from sqlalchemy import text

from agents.base_agent import BaseAgent
from shared.utils import truncate_text
from config.settings import settings

logger = structlog.get_logger(__name__)


class MatchingAgent(BaseAgent):

    def __init__(self, db):
        super().__init__("matching", db)
        self.ollama = ollama_client.AsyncClient(
            host=settings.ollama_base_url
        )

    # -----------------------------
    # IMPORTANT: pgvector FIX
    # -----------------------------
    def _to_pgvector(self, vec: List[float]) -> str:
        # correct pgvector string format
        return "[" + ",".join(str(float(x)) for x in vec) + "]"

    async def execute(self, payload: Dict[str, Any], correlation_id: str):

        cv_version_id   = payload["cv_version_id"]
        job_id          = payload["job_id"]
        parsed_cv       = payload["parsed_cv"]
        job_description = payload["job_description"]
        candidate_id    = payload["candidate_id"]

        # -----------------------------
        # Step 1: Embeddings
        # -----------------------------
        cv_text = self._cv_to_text(parsed_cv)

        cv_embed = await self._embed(cv_text)
        jd_embed = await self._embed(truncate_text(job_description, 4000))

        cv_vec = self._to_pgvector(cv_embed)
        jd_vec = self._to_pgvector(jd_embed)

        # -----------------------------
        # IMPORTANT FIX: NO CAST(:x::vector)
        # Use direct string cast in SQL
        # -----------------------------

        try:
            # -----------------------------
            # Step 2: Store CV embedding
            # -----------------------------
            await self.db.execute(
                text("""
                    UPDATE cv_versions
                    SET embedding = :emb::vector
                    WHERE id = :id
                """),
                {"emb": cv_vec, "id": cv_version_id}
            )

            # -----------------------------
            # Step 3: similarity search
            # -----------------------------
            result = await self.db.execute(
                text("""
                    SELECT 1 - (embedding <=> :jd::vector) AS similarity
                    FROM cv_versions
                    WHERE id = :id
                """),
                {"jd": jd_vec, "id": cv_version_id}
            )

            row = result.fetchone()
            semantic_similarity = float(row.similarity) if row else 0.0

        except Exception:
            await self.db.rollback()
            raise

        # -----------------------------
        # Step 4: LLM analysis
        # -----------------------------
        llm_analysis = await self._llm_analyze(parsed_cv, job_description)

        # -----------------------------
        # Step 5: Composite score
        # -----------------------------
        composite = (
            llm_analysis["relevance_score"] * 0.4 +
            semantic_similarity * 0.35 +
            llm_analysis["potential_score"] * 0.25
        )

        self.log.info(
            "matching_complete",
            similarity=round(semantic_similarity, 3),
            composite=round(composite, 3),
        )

        return {
            "candidate_id": candidate_id,
            "cv_version_id": cv_version_id,
            "job_id": job_id,
            "semantic_similarity": round(semantic_similarity, 3),
            "relevance_score": llm_analysis["relevance_score"],
            "potential_score": llm_analysis["potential_score"],
            "composite_score": round(composite, 3),
            "strengths": llm_analysis["strengths"],
            "gaps": llm_analysis["gaps"],
            "transferable_skills": llm_analysis["transferable_skills"],
            "llm_rationale": llm_analysis["rationale"],
        }

    # -----------------------------
    # Embeddings
    # -----------------------------
    async def _embed(self, text: str) -> List[float]:
        response = await self.ollama.embeddings(
            model=settings.ollama_embed_model,
            prompt=text,
        )
        return response["embedding"]

    # -----------------------------
    # CV formatting
    # -----------------------------
    def _cv_to_text(self, parsed_cv: dict) -> str:
        parts = []

        if parsed_cv.get("cv_summary"):
            parts.append(f"Summary: {parsed_cv['cv_summary']}")

        tech = ", ".join(parsed_cv.get("technical_skills", []))
        if tech:
            parts.append(f"Technical Skills: {tech}")

        domains = ", ".join(parsed_cv.get("domain_expertise", []))
        if domains:
            parts.append(f"Domain Expertise: {domains}")

        for exp in parsed_cv.get("experience", [])[:5]:
            parts.append(
                f"{exp.get('role','')} at {exp.get('company','')}: {exp.get('description','')}"
            )

        return "\n".join(parts)

    # -----------------------------
    # LLM
    # -----------------------------
    async def _llm_analyze(self, parsed_cv: dict, job_description: str) -> dict:

        cv_summary = json.dumps(parsed_cv, indent=2)

        prompt = f"""
Return ONLY valid JSON:

{{
  "relevance_score": 0.0-1.0,
  "potential_score": 0.0-1.0,
  "strengths": [],
  "gaps": [],
  "transferable_skills": [],
  "rationale": ""
}}

JOB:
{truncate_text(job_description, 2000)}

CV:
{cv_summary}
"""

        try:
            response = await self.ollama.chat(
                model=settings.ollama_llm_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2},
            )

            raw = response["message"]["content"].strip()

            # IMPORTANT FIX: prevent empty response crash
            if not raw:
                raise ValueError("Empty LLM response")

            raw = raw.replace("```json", "").replace("```", "").strip()

            return json.loads(raw)

        except Exception as e:
            self.log.error("llm_analysis_failed", error=str(e))

            return {
                "relevance_score": 0.3,
                "potential_score": 0.3,
                "strengths": ["LLM failed"],
                "gaps": ["Manual review required"],
                "transferable_skills": [],
                "rationale": f"LLM error: {str(e)}",
            }
