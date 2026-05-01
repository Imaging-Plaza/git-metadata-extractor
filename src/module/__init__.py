"""Top-level namespace for analytical modules that complement the v2 pipeline.

Each subpackage is a self-contained workstream. Modules are deliberately not
wired into the main `/v2/extract` pipeline by default — they exist as
standalone capabilities that can be invoked from CLI scripts or future
pipeline stages once their data shape is stable.
"""
