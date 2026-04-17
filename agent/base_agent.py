import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import structlog
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """
    Every agent inherits from this class.
    Provides:
      - Structured logging with correlation IDs
      - Performance timing
      - Standardized error handling
      - Audit log writing
    """

    def __init__(self, name: str, db: AsyncSession):
        self.name = name
        self.db = db
        self.log = logger.bind(agent=name)

    async def run(
        self,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:

        correlation_id = correlation_id or str(uuid.uuid4())
        bound_log = self.log.bind(correlation_id=correlation_id)

        bound_log.info("agent_started", payload_keys=list(payload.keys()))
        start_time = time.perf_counter()

        try:
            result = await self.execute(payload, correlation_id)

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            bound_log.info(
                "agent_completed",
                duration_ms=duration_ms,
                outcome="success",
            )

            await self._write_audit(
                correlation_id=correlation_id,
                event_type=f"agent:{self.name}",
                event_data={
                    "input_keys": list(payload.keys()),
                    "outcome": "success"
                },
                outcome="success",
                duration_ms=duration_ms,
            )

            return result

        except Exception as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)

            bound_log.error(
                "agent_failed",
                error=str(e),
                duration_ms=duration_ms
            )

            await self._write_audit(
                correlation_id=correlation_id,
                event_type=f"agent:{self.name}",
                event_data={
                    "input_keys": list(payload.keys()),
                    "error": str(e)
                },
                outcome="failure",
                duration_ms=duration_ms,
                error_message=str(e),
            )

            raise

    @abstractmethod
    async def execute(
        self,
        payload: Dict[str, Any],
        correlation_id: str
    ) -> Dict[str, Any]:
        pass

    async def _write_audit(
        self,
        correlation_id: str,
        event_type: str,
        event_data: Dict[str, Any],
        outcome: str,
        duration_ms: int,
        error_message: Optional[str] = None,
        **kwargs,
    ) -> None:
        try:
            await self.db.execute(
                text("""
                    INSERT INTO audit_logs
                    (correlation_id, event_type, actor, event_data, outcome, duration_ms, error_message)
                    VALUES
                    (:cid, :etype, :actor, CAST(:edata AS jsonb), :outcome, :dur, :err)
                """),
                {
                    "cid": correlation_id,
                    "etype": event_type,
                    "actor": self.name,
                    "edata": json.dumps(event_data),
                    "outcome": outcome,
                    "dur": duration_ms,
                    "err": error_message,
                }
            )
            await self.db.commit()

        except Exception as log_err:
            await self.db.rollback()
            self.log.error("audit_write_failed", error=str(log_err))
