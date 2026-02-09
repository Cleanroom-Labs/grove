"""
Git Submodule Visualizer

A tkinter-based GUI for visualizing git repositories and their submodules
as an interactive node-link diagram.
"""

__all__ = ["SubmoduleVisualizerApp"]


def __getattr__(name):
    if name == "SubmoduleVisualizerApp":
        from .app import SubmoduleVisualizerApp
        return SubmoduleVisualizerApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
