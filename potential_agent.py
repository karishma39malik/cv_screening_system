import json
from typing import Any, Dict
import ollama as ollama_client
import structlog

from agents.base_agent import BaseAgent
from config.settings import settings

logger = structlog.get_logger(__name__)


class PotentialAgent(BaseAgent):
    """
    Augments the MatchingAgent with deeper human-centric insights:
    - Career trajectory (ascending/stagnant/pivoting)
    - Learning velocity (certs, tech adoption timeline)
    - Leadership signals (promotions, team lead roles)
    - Role adaptability (cross-functional experience)

    This agent augments recruiter judgment — it does NOT override it.
    """

    def __init__(self, db):
        super().__init__("potential", db)
        self.ollama = ollama_client.AsyncClient(host=settings.ollama_base_url)

    async def execute(self, payload: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
        """
        payload expects:
          - parsed_cv: dict
        """
        parsed_cv = payload["parsed_cv"]
        insights  = await self._analyze_potential(parsed_cv)

        self.log.info(
            "potential_analysis_complete",
            trajectory=insights.get("career_trajectory"),
            leadership_signals=len(insights.get("leadership_signals", [])),
        )

        return insights

    async def _analyze_potential(self, parsed_cv: dict) -> dict:
        prompt = f"""
You are a talent development expert. Analyze this candidate's career profile for potential indicators.

GUARDRAILS:
- Focus ONLY on professional indicators: career progression, skill acquisition, leadership
- Do NOT infer or comment on personal attributes unrelated to job performance

CANDIDATE PROFILE:
{json.dumps({
    "experience": parsed_cv.get("experience", []),
    "certifications": parsed_cv.get("certifications", []),
    "technical_skills": parsed_cv.get("technical_skills", []),
    "domain_expertise": parsed_cv.get("domain_expertise", []),
    "total_years_exp": parsed_cv.get("total_years_exp"),
}, indent=2)}

Analyze and return this JSON:
{{
  "career_trajectory": "ascending | lateral | pivoting | stagnant | insufficient_data",
  "learning_velocity": "high | medium | low | insufficient_data",
  "leadership_signals": ["list of specific evidence of leadership or ownership"],
  "adaptability_indicators": ["cross-functional, industry, or tech pivots observed"],
  "growth_potential_label": "one of: Strong long-term fit | High upskilling ability | Good future leadership profile | Specialist with deep expertise | Generalist with broad exposure | Insufficient data",
  "value_add_insights": ["2-4 specific, actionable insights for the hiring manager"],
  "potential_score_rationale": "1 paragraph explaining the potential assessment"
}}

Return ONLY valid JSON. No markdown.
"""
        try:
            response = await self.ollama.chat(
                model=settings.ollama_llm_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 800},
            )
            raw = response["message"]["content"].strip().replace("```json","").replace("```","")
            return json.loads(raw)
        except Exception as e:
            self.log.error("potential_analysis_failed", error=str(e))
            return {
                "career_trajectory":      "insufficient_data",
                "learning_velocity":      "insufficient_data",
                "leadership_signals":     [],
                "adaptability_indicators": [],
                "growth_potential_label": "Insufficient data",
                "value_add_insights":     ["Manual review required"],
                "potential_score_rationale": f"Analysis failed: {str(e)}",
            }
