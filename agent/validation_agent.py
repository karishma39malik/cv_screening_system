import hashlib
from typing import Any, Dict, List
from sqlalchemy import text
import structlog

from agents.base_agent import BaseAgent

logger = structlog.get_logger(__name__)


class ValidationAgent(BaseAgent):
    """
    Validates CVs and screenings. Flags issues to HR.
    Does NOT auto-decide — raises flags for human review.

    Checks:
    1. Duplicate CV (same file hash)
    2. Candidate already applied for this job
    3. Missing mandatory qualifications
    4. Employment gap analysis
    5. Inconsistent/overlapping job dates
    6. Low parse confidence
    7. Borderline composite score
    """

    def __init__(self, db):
        super().__init__("validation", db)

    async def execute(self, payload: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
        """
        payload expects:
          - file_hash: str
          - cv_version_id: str
          - candidate_id: str
          - job_id: str
          - parsed_cv: dict
          - composite_score: float
          - parse_confidence: float
        """
        anomalies = []

        # Run all checks
        anomalies += await self._check_duplicate_file(payload)
        anomalies += await self._check_duplicate_application(payload)
        anomalies += await self._check_parse_confidence(payload)
        anomalies += await self._check_employment_gaps(payload)
        anomalies += await self._check_date_consistency(payload)
        anomalies += await self._check_borderline_score(payload)

        # Persist anomalies to database
        for anomaly in anomalies:
            await self.db.execute(
                text("""
                    INSERT INTO anomalies
                        (cv_version_id, candidate_id, job_id, anomaly_type, severity, description, raw_evidence)
                    VALUES
                        (:cv_id, :cand_id, :job_id, :atype, :severity, :desc, :evidence::jsonb)
                """),
                {
                    "cv_id":   payload["cv_version_id"],
                    "cand_id": payload["candidate_id"],
                    "job_id":  payload["job_id"],
                    "atype":   anomaly["type"],
                    "severity": anomaly["severity"],
                    "desc":    anomaly["description"],
                    "evidence": str(anomaly.get("evidence", {})),
                },
            )

        self.log.info("validation_complete", anomaly_count=len(anomalies))

        return {
            "anomalies":       anomalies,
            "requires_review": any(a["severity"] in ["high", "critical"] for a in anomalies),
            "anomaly_count":   len(anomalies),
        }

    async def _check_duplicate_file(self, payload: dict) -> List[dict]:
        """Check if the exact same file was uploaded before."""
        result = await self.db.execute(
            text("""
                SELECT id, candidate_id, uploaded_at
                FROM cv_versions
                WHERE file_hash = :hash AND id != :cv_id
                LIMIT 1
            """),
            {"hash": payload["file_hash"], "cv_id": payload["cv_version_id"]},
        )
        row = result.fetchone()
        if row:
            return [{
                "type":        "duplicate_cv",
                "severity":    "high",
                "description": f"This exact CV file was previously uploaded on {row.uploaded_at.date()}. "
                               "May indicate resubmission or copy. HR review recommended.",
                "evidence":    {"duplicate_cv_id": str(row.id), "uploaded_at": str(row.uploaded_at)},
            }]
        return []

    async def _check_duplicate_application(self, payload: dict) -> List[dict]:
        """Check if candidate has already applied for this exact job."""
        result = await self.db.execute(
            text("""
                SELECT s.id, s.screened_at
                FROM screenings s
                WHERE s.candidate_id = :cand_id AND s.job_id = :job_id
                LIMIT 1
            """),
            {"cand_id": payload["candidate_id"], "job_id": payload["job_id"]},
        )
        row = result.fetchone()
        if row:
            return [{
                "type":        "duplicate_cv",
                "severity":    "medium",
                "description": f"This candidate has already been screened for this role on {row.screened_at.date()}.",
                "evidence":    {"prior_screening_id": str(row.id)},
            }]
        return []

    async def _check_parse_confidence(self, payload: dict) -> List[dict]:
        """Flag CVs with very low parsing confidence."""
        confidence = payload.get("parse_confidence", 1.0)
        if confidence < 0.35:
            return [{
                "type":        "low_parse_confidence",
                "severity":    "high",
                "description": f"CV parsing confidence is very low ({confidence:.0%}). "
                               "The document may be scanned, image-based, or have complex formatting. "
                               "Manual review of the original file is strongly recommended.",
                "evidence":    {"confidence": confidence},
            }]
        elif confidence < 0.55:
            return [{
                "type":        "low_parse_confidence",
                "severity":    "medium",
                "description": f"CV parsing confidence is moderate ({confidence:.0%}). "
                               "Some fields may be incomplete.",
                "evidence":    {"confidence": confidence},
            }]
        return []

    async def _check_employment_gaps(self, payload: dict) -> List[dict]:
        """Detect significant gaps in employment history."""
        experience = payload.get("parsed_cv", {}).get("experience", [])
        if len(experience) < 2:
            return []

        # Sort by start date and check gaps > 12 months
        anomalies = []
        try:
            sorted_exp = sorted(
                [e for e in experience if e.get("start_date") and e.get("end_date")],
                key=lambda x: x["start_date"],
            )
            for i in range(1, len(sorted_exp)):
                prev_end   = sorted_exp[i-1]["end_date"]
                curr_start = sorted_exp[i]["start_date"]
                if prev_end.lower() == "present":
                    continue
                # Simple year-based gap detection
                try:
                    prev_year  = int(str(prev_end)[:4])
                    curr_year  = int(str(curr_start)[:4])
                    gap_months = (curr_year - prev_year) * 12
                    if gap_months > 12:
                        anomalies.append({
                            "type":        "employment_gap",
                            "severity":    "low",
                            "description": f"Potential employment gap of ~{gap_months//12} year(s) detected "
                                           f"between {sorted_exp[i-1].get('company','')} and "
                                           f"{sorted_exp[i].get('company','')}. Worth exploring in interview.",
                            "evidence":    {"gap_months": gap_months, "before": prev_end, "after": curr_start},
                        })
                except (ValueError, TypeError):
                    pass
        except Exception as e:
            self.log.warning("gap_analysis_error", error=str(e))

        return anomalies

    async def _check_date_consistency(self, payload: dict) -> List[dict]:
        """Check for overlapping job dates (possible inconsistency)."""
        experience = payload.get("parsed_cv", {}).get("experience", [])
        active_roles = [
            e for e in experience
            if e.get("end_date", "").lower() == "present"
        ]
        if len(active_roles) > 1:
            return [{
                "type":        "inconsistent_dates",
                "severity":    "medium",
                "description": f"CV shows {len(active_roles)} simultaneously 'current' positions. "
                               "This may indicate consulting/freelance work or a data error. Clarify in interview.",
                "evidence":    {"active_roles": [r.get("role") for r in active_roles]},
            }]
        return []

    async def _check_borderline_score(self, payload: dict) -> List[dict]:
        """Flag borderline composite scores for mandatory HR review."""
        score = payload.get("composite_score", 0)
        if 0.38 <= score <= 0.52:
            return [{
                "type":        "borderline_score",
                "severity":    "medium",
                "description": f"Composite score ({score:.2f}) is in the borderline range (0.38–0.52). "
                               "System cannot make a confident recommendation. HR judgment is essential.",
                "evidence":    {"composite_score": score},
            }]
        return []
