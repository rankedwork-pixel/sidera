"""Company bootstrap pipeline -- ingest docs, extract knowledge, configure agents.

Public API
----------
.. autofunction:: run_bootstrap

Usage::

    from src.bootstrap import run_bootstrap

    plan = await run_bootstrap(
        folder_id="1A2B3C...",
        user_id="U12345",
    )
    # plan.status == "draft" -- present to human for review
    # After approval:
    plan.status = "approved"
    result = await execute_plan(plan, user_id="U12345")
"""

from __future__ import annotations

import structlog

from src.bootstrap.classifier import classify_documents
from src.bootstrap.crawler import crawl_folder
from src.bootstrap.executor import execute_plan
from src.bootstrap.extractor import extract_knowledge
from src.bootstrap.generator import generate_plan
from src.bootstrap.models import BootstrapPlan

logger = structlog.get_logger(__name__)

__all__ = ["run_bootstrap", "execute_plan"]


async def run_bootstrap(
    folder_id: str,
    *,
    user_id: str = "bootstrap",
    max_docs: int = 100,
) -> BootstrapPlan:
    """Run the full bootstrap pipeline: crawl -> classify -> extract -> generate.

    This produces a ``BootstrapPlan`` in "draft" status.  The plan must
    be reviewed and approved by a human before calling ``execute_plan()``.

    Parameters
    ----------
    folder_id:
        Google Drive folder ID to crawl.
    user_id:
        The user initiating the bootstrap.
    max_docs:
        Maximum documents to crawl (cost safety).

    Returns
    -------
    BootstrapPlan
        A draft plan ready for human review.
    """
    logger.info(
        "bootstrap.start",
        folder_id=folder_id,
        user_id=user_id,
        max_docs=max_docs,
    )

    total_cost = 0.0

    # Step 1: Crawl Google Drive folder
    raw_docs = await crawl_folder(folder_id, max_docs=max_docs)
    if not raw_docs:
        logger.warning("bootstrap.no_documents", folder_id=folder_id)
        return BootstrapPlan(
            source_folder_id=folder_id,
            errors=["No readable documents found in the specified folder."],
        )

    # Step 2: Classify documents
    classified = await classify_documents(raw_docs)
    relevant = [d for d in classified if d.is_relevant]

    if not relevant:
        logger.warning("bootstrap.no_relevant_docs", folder_id=folder_id)
        return BootstrapPlan(
            source_folder_id=folder_id,
            documents_crawled=len(raw_docs),
            documents_classified=len(classified),
            errors=["No relevant documents found after classification."],
        )

    # Step 3: Extract knowledge
    knowledge, extract_cost = await extract_knowledge(relevant)
    total_cost += extract_cost

    # Step 4: Generate plan
    plan = generate_plan(
        knowledge,
        source_folder_id=folder_id,
        documents_crawled=len(raw_docs),
        documents_classified=len(classified),
        documents_extracted=len(relevant),
        estimated_cost=total_cost,
    )

    logger.info(
        "bootstrap.complete",
        plan_id=plan.id,
        **plan.summary(),
    )

    return plan
