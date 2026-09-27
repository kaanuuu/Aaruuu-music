"""
Aaruu Music - Base Provider Interface
Defines the standard contract for search and track retrieval.
"""

from typing import List, Optional
from player.models import Track

class BaseProvider:
    """Standard base class for music providers."""

    def search(
        self, query: str, limit: int = 5, requester_id: int = 0, requester_name: str = ""
    ) -> List[Track]:
        """Search tracks."""
        raise NotImplementedError

    def get_track(
        self, track_id: str, requester_id: int = 0, requester_name: str = ""
    ) -> Optional[Track]:
        """Retrieve detail of a track."""
        raise NotImplementedError
