"""Docker Hub RAG index module.

Indexes Docker Hub *repositories* (images), one row per `namespace/name`
(official images live under the `library/` namespace). Mirrors the
github_repos index shape: DuckDB metadata store + RCP embeddings into a
`dockerhub` Qdrant collection, reusing the shared openalex RCP / Qdrant
clients and chunker. Data comes from the public Docker Hub v2 API
(`https://hub.docker.com/v2/`), which serves public repositories
anonymously.
"""
