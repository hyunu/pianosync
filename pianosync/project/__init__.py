"""Project subpackage: JSON persistence and validation."""

from .store import Project, ProjectErrorDetail, load_project, save_project

__all__ = ["Project", "ProjectErrorDetail", "load_project", "save_project"]