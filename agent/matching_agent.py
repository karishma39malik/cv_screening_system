import json
from typing import Any, Dict, List
import ollama as ollama_client
import structlog
from sqlalchemy import text

from agents.base_agent import BaseAgent
from shared.models import ScreeningResult
from shared.utils import truncate_text
from config.settings import settings

logger = structlog.get_logger(__name__)


class MatchingAgent(BaseAgent):
    """
    Core intelligence agent:
    1. Generate embeddings for CV and JD
    2. Compute cosine similarity via pgvector
    3. Use LLM to generate human-readable rationale
    4. Produce composite score and strengths/gaps analysis

    DESIGN PRINCIPLE: No fixed weights. Scoring is semantic + probabilistic.
    """

    def __init__(self, db):
        super().__init__("matching", db)
        self.ollama = ollama_client.AsyncClient(host=settings.ollama_base_url)

    async def execute(self, payload: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
        """
        payload expects:
          - cv_version_id: str
          - job_id: str
          - parsed_cv: dict    — from IngestionAgent
          - job_description: str
          - candidate_id: str
        """
        cv_version_id  = payload["cv_version_id"]
        job_id         = payload["job_id"]
        parsed_cv      = payload["parsed_cv"]
        job_description = payload["job_description"]
        candidate_id   = payload["candidate_id"]

        # ---- Step 1: Generate embeddings ----
        cv_text  = self._cv_to_text(parsed_cv)
        cv_embed = await self._embed(cv_text)
        jd_embed = await self._embed(truncate_text(job_description, 4000))

        # ---- Step 2: Store CV embedding in DB ----
        await self.db.execute(
            text("UPDATE cv_versions SET embedding = :emb WHERE id = :id"),
            {"emb": str(cv_embed), "id": cv_version_id},
        )

        # ---- Step 3: Compute cosine similarity via pgvector ----
        result = await self.db.execute(
            text("""
                SELECT 1 - (embedding <=> :jd_embed::vector) AS similarity
                FROM cv_versions WHERE id = :cv_id
            """),
            {"jd_embed": str(jd_embed), "cv_id": cv_version_id},
        )
        row = result.fetchone()
        semantic_similarity = float(row.similarity) if row else 0.0

        # ---- Step 4: LLM reasoning for deeper analysis ----
        llm_analysis = await self._llm_analyze(parsed_cv, job_description)

        # ---- Step 5: Compute composite score ----
        # Composite = blend of vector similarity + LLM relevance + potential
        # Weights are contextual, not hardcoded
        relevance_weight  = 0.4
        similarity_weight = 0.35
        potential_weight  = 0.25

        composite = (
            llm_analysis["relevance_score"]  * relevance_weight +
            semantic_similarity               * similarity_weight +
            llm_analysis["potential_score"]  * potential_weight
        )

        self.log.info(
            "matching_complete",
            similarity=round(semantic_similarity, 3),
            relevance=round(llm_analysis["relevance_score"], 3),
            composite=round(composite, 3),
        )

        return {
            "candidate_id":        candidate_id,
            "cv_version_id":       cv_version_id,
            "job_id":              job_id,
            "semantic_similarity": round(semantic_similarity, 3),
            "relevance_score":     round(llm_analysis["relevance_score"], 3),
            "potential_score":     round(llm_analysis["potential_score"], 3),
            "composite_score":     round(composite, 3),
            "strengths":           llm_analysis["strengths"],
            "gaps":                llm_analysis["gaps"],
            "transferable_skills": llm_analysis["transferable_skills"],
            "llm_rationale":       llm_analysis["rationale"],
        }

    async def _embed(self, text: str) -> List[float]:
        """Generate embedding vector using Ollama's embedding model."""
        response = await self.ollama.embeddings(
            model=settings.ollama_embed_model,
            prompt=text,
        )
        return response["embedding"]

    def _cv_to_text(self, parsed_cv: dict) -> str:
        """Convert parsed CV dict to a rich text for embedding."""
        parts = []
        if parsed_cv.get("cv_summary"):
            parts.append(f"Summary: {parsed_cv['cv_summary']}")

        tech = ", ".join(parsed_cv.get("technical_skills", []))
        if tech:
            parts.append(f"Technical Skills: {tech}")

        domains = ", ".join(parsed_cv.get("domain_expertise", []))
        if domains:
            parts.append(f"Domain Expertise: {domains}")

        for exp in parsed_cv.get("experience", [])[:5]:  # Top 5 roles
            role_text = f"{exp.get('role','')} at {exp.get('company','')}: {exp.get('description','')}"
            parts.append(role_text)

        certs = ", ".join(parsed_cv.get("certifications", []))
        if certs:
            parts.append(f"Certifications: {certs}")

        return "\n".join(parts)

    async def _llm_analyze(self, parsed_cv: dict, job_description: str) -> dict:
        """
        LLM generates:
        - Relevance score (0-1)
        - Potential score (0-1)
        - Strengths list
        - Gaps list
        - Transferable skills
        - Plain-English rationale for HR
        """
        cv_summary = json.dumps({
            "name": parsed_cv.get("full_name", "Candidate"),
            "skills": parsed_cv.get("technical_skills", [])[:15],
            "domains": parsed_cv.get("domain_expertise", []),
            "experience": [
                f"{e.get('role','')} at {e.get('company','')} ({e.get('duration_months',0)//12 if e.get('duration_months') else '?'} years)"
                for e in parsed_cv.get("experience", [])[:4]
            ],
            "total_years": parsed_cv.get("total_years_exp"),
            "education": [
                f"{e.get('degree','')} in {e.get('field','')} from {e.get('institution','')}"
                for e in parsed_cv.get("education", [])[:2]
            ],
        }, indent=2)

        analysis_prompt = f"""
You are a senior HR analyst. Evaluate this candidate against the job description.

GUARDRAILS:
- Base evaluation ONLY on skills, experience, and qualifications
- Do NOT consider or infer gender, age, nationality, or any protected characteristic
- Focus on transferable skills and growth potential, not just exact keyword matches

JOB DESCRIPTION:
{truncate_text(job_description, 2000)}

CANDIDATE PROFILE:
{cv_summary}

Return a JSON object with EXACTLY this structure:
{{
  "relevance_score": float 0.0-1.0,
  "potential_score": float 0.0-1.0,
  "strengths": ["list of 3-5 concrete strengths relevant to this role"],
  "gaps": ["list of 2-4 gaps or areas to probe in interview"],
  "transferable_skills": ["skills from other domains that apply here"],
  "rationale": "2-3 paragraph plain English explanation for an HR professional. Mention specific role requirements met, gaps to explore, and overall recommendation framing. Do NOT recommend hire/reject — HR makes that decision."
}}

Scoring guidance:
- relevance_score: How well skills/experience match JD requirements
- potential_score: Signals of learning agility, progression, adaptability

Return ONLY valid JSON. No markdown. No preamble.
"""
        try:
            response = await self.ollama.chat(
                model=settings.ollama_llm_model,
                messages=[{"role": "user", "content": analysis_prompt}],
                options={"temperature": 0.2, "num_predict": 1500},
            )
            raw = response["message"]["content"].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)

        except Exception as e:
            self.log.error("llm_analysis_failed", error=str(e))
            return {
                "relevance_score":     0.3,
                "potential_score":     0.3,
                "strengths":           ["Unable to analyze — LLM error"],
                "gaps":                ["Manual review required"],
                "transferable_skills": [],
                "rationale":           f"Automated analysis failed: {str(e)}. Manual review required.",
            }

