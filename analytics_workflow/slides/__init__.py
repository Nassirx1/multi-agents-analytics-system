"""Standalone slide deck generation package for the analytics workflow."""

from .deck_spec import ContentBlock, DeckSpec, SlideSpec, VisualSpec
from .story_builder import build_deck_spec
from .pptx_renderer import render_deck

__all__ = [
    "ContentBlock",
    "DeckSpec",
    "SlideSpec",
    "VisualSpec",
    "build_deck_spec",
    "render_deck",
]
