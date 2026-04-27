"""Pluggable source adapters for wallet candidate discovery."""

from .base import Candidate, SourceAdapter
from .dune_adapter import DuneAdapter

__all__ = ["Candidate", "SourceAdapter", "DuneAdapter"]
