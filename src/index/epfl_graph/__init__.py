"""EPFL Graph disciplines RAG index.

Pulls the full ontology tree (~2226 categories) from graphai.epfl.ch into
DuckDB, embeds each node via RCP, and pushes vectors into a single Qdrant
collection so callers can query "which EPFL Graph discipline is closest
to this text?".
"""
