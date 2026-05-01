"""Multi-source indexing root.

Each subpackage (e.g. `infoscience`, `openalex`) owns one external source:
discovery, fetch, chunking, embedding, and a vector store. Design docs live
under `.internal/<source>/`.
"""
