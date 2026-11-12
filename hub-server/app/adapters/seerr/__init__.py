"""Request manager adapter (Overseerr / Jellyseerr / Seerr REST API)."""

from app.adapters.seerr.client import ManagerStatus, SeerrClient, SeerrConnection, SeerrError

__all__ = ["ManagerStatus", "SeerrClient", "SeerrConnection", "SeerrError"]
