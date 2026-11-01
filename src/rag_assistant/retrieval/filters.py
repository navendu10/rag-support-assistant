"""Region filter (Session 6): customers search only their own region's documents and ALL.

The region comes **only from the user's profile** (``data/users.json``), never from the
question. A UK customer who writes "I'm in India" still gets UK + ALL. The functions
take a ``User`` and nothing else, so there is no path from the message to the filter.

The filter is applied inside the Weaviate query, so filtering happens before ranking and
the search still returns ``fetch_k`` candidates from the allowed regions.

This is personalisation, not security: it decides which policy applies to the person,
but a customer may legitimately ask about another region, and internal documents are
still searchable. Access control is Session 7.
"""

from __future__ import annotations

from weaviate.classes.query import Filter
from weaviate.collections.classes.filters import _Filters

from rag_assistant.schemas import User

ALL_REGIONS = "ALL"


def region_values(user: User) -> tuple[str, ...] | None:
    """Regions a user's search may return: ``(their region, "ALL")``, or ``None`` for agents.

    Agents have region ALL (``User`` enforces it) and serve every region, so they get
    no filter at all.
    """
    if user.region == ALL_REGIONS:
        return None
    return (user.region, ALL_REGIONS)


def build_region_filter(values: tuple[str, ...] | None) -> _Filters | None:
    """A Weaviate filter matching any of ``values`` on ``region``, or ``None`` for no filter.

    ``region`` is FIELD-tokenised (``ingestion/store.py``), so ``equal`` is an exact match.
    """
    if values is None:
        return None
    return Filter.any_of([Filter.by_property("region").equal(value) for value in values])


def region_filter(user: User) -> _Filters | None:
    """The region filter for ``user``'s profile (``None`` for agents)."""
    return build_region_filter(region_values(user))
