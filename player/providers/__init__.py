"""
Aaruu Music - Providers Initialization
Exposes initialized YouTube and JioSaavn provider singletons.
"""

from player.providers.youtube import youtube_provider
from player.providers.jiosaavn import jiosaavn_provider

__all__ = ["youtube_provider", "jiosaavn_provider"]
