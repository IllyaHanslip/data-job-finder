"""Every job source, in the order they are searched."""

from job_finder.sources.adzuna import AdzunaSource
from job_finder.sources.base import JobSource, SearchRequest, SourceResult
from job_finder.sources.himalayas import HimalayasSource
from job_finder.sources.jobicy import JobicySource
from job_finder.sources.reed import ReedSource
from job_finder.sources.remotive import RemotiveSource

ALL_SOURCES: list[type[JobSource]] = [
    AdzunaSource,
    ReedSource,
    HimalayasSource,
    RemotiveSource,
    JobicySource,
]

__all__ = [
    "ALL_SOURCES",
    "AdzunaSource",
    "HimalayasSource",
    "JobSource",
    "JobicySource",
    "ReedSource",
    "RemotiveSource",
    "SearchRequest",
    "SourceResult",
]
