"""BACH Phase 1 completion: independent R4 audit, diagnosis and readiness tooling.

This package is deliberately outside the source sets hashed into TARGET_V1R..V1R4, so
adding it does not change any frozen target identity. It never edits historical
results, never rewrites model tool calls and never launches inference unless a person
runs a real-inference command with RUN_REAL=YES.
"""
