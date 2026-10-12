"""Video subpackage: time mapping, audio assembly and final rendering."""

from .audiomix import assemble_audio
from .render import RenderResult, render_final_video

__all__ = ["assemble_audio", "RenderResult", "render_final_video"]
