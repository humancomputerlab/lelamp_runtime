"""Constrained motion/light scene DSL."""

from .compiler import compile_scene
from .executor import execute_compiled_scene, execute_scene
from .schema import BODY_PRIMITIVES, LIGHT_PRIMITIVES, validate_scene

__all__ = [
    "BODY_PRIMITIVES",
    "LIGHT_PRIMITIVES",
    "compile_scene",
    "execute_compiled_scene",
    "execute_scene",
    "validate_scene",
]
