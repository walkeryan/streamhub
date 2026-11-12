"""Request manager adapter (Overseerr / Jellyseerr / Seerr REST API)."""

from app.adapters.seerr.client import ManagerStatus, SeerrClient, SeerrError

__all__ = ["ManagerStatus", "SeerrClient", "SeerrError"]
