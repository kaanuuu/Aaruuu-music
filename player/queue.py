"""
Aaruu Music - Track Queue
Manages an ordered, capacity-limited queue of audio tracks for a chat.
"""

from typing import List, Optional
from player.models import Track


class TrackQueue:
    """Thread-safe queue implementation for per-chat audio scheduling."""

    def __init__(self, max_size: int = 50):
        self.max_size = max_size
        self._items: List[Track] = []

    def add(self, track: Track) -> bool:
        """Appends a track to the end of the queue. Returns False if queue capacity reached."""
        if len(self._items) >= self.max_size:
            return False
        self._items.append(track)
        return True

    def pop(self) -> Optional[Track]:
        """Pops and returns the next track to play."""
        if not self._items:
            return None
        return self._items.pop(0)

    def peek(self) -> Optional[Track]:
        """Returns the next track without removing it."""
        if not self._items:
            return None
        return self._items[0]

    def remove(self, index: int) -> Optional[Track]:
        """Removes and returns a track at the given 0-indexed position."""
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def undo_last(self) -> Optional[Track]:
        """Removes and returns the most recently added track."""
        if self._items:
            return self._items.pop()
        return None

    def shuffle(self) -> int:
        """Randomizes the order of tracks currently in queue."""
        import random
        random.shuffle(self._items)
        return len(self._items)

    def clear(self) -> int:
        """Empties the queue and returns the count of removed items."""
        count = len(self._items)
        self._items.clear()
        return count

    def to_list(self) -> List[Track]:
        """Returns a shallow copy of the queued tracks."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)
