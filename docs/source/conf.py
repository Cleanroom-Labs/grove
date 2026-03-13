"""
Sphinx configuration for Grove documentation.

Imports shared theme configuration from the common submodule,
then adds Grove-specific settings.
"""

import sys
import os

sys.path.insert(0, os.path.abspath('../common'))
from theme_config import *  # noqa: F401, F403

project = 'Grove'
copyright = '2026, Cleanroom Labs'
author = 'Cleanroom Labs'
version = get_docs_version()
release = get_docs_version()

# Grove doesn't use requirements traceability
extensions = [ext for ext in extensions if ext != 'sphinx_needs']

# Paths: docs/source/ -> sibling docs/common/
html_static_path = ['../common/sphinx/_static', '_static']
templates_path = ['../common/sphinx/_templates']
html_favicon = '../common/sphinx/_static/favicon.ico'

myst_enable_extensions = [
    "tasklist",
    "html_image",
]

# README starts at H2 (banner image is the visual title) — suppress heading-level warning
suppress_warnings = ["myst.header"]

html_css_files = [*html_css_files, 'grove-docs.css']

html_title = 'Grove Docs'
html_context = {
    'display_github': True,
    'github_user': 'Cleanroom-Labs',
    'github_repo': 'grove',
    'github_version': 'main',
    'conf_py_path': '/docs/source/',
}
setup_project_icon(project_name='Grove', html_context_dict=html_context)
setup_standalone_docs(project_name='Grove', html_context_dict=html_context)
setup_version_context(html_context)
