"""EPFL Graph (graphai-client) wrapper module.

Thin wrapper around the upstream `graphai_client` library. Auth is handled
transparently using the EPFL_GRAPH_USERNAME and EPFL_GRAPH_PASSWORD env vars,
so callers do not need to manage `login_info` themselves.

Public API mirrors `graphai_client.client_api.{translation,text,embedding,
image,voice,video}` and the integrated `graphai_client.client.process_video`.
"""

from src.module.epfl_graph.auth import get_login_info, reset_login_info
from src.module.epfl_graph.client import process_video
from src.module.epfl_graph.image import extract_text_from_slide
from src.module.epfl_graph.ontology import (
    category_chain,
    category_graphsearch_url,
    category_id_to_label,
    category_info,
    category_nearest_openalex_topics,
    category_wikipedia,
    concept_nearest_categories,
    ontology_tree,
)
from src.module.epfl_graph.openalex_related import (
    people_for_topics,
    publications_for_topics,
    units_for_topics,
)
from src.module.epfl_graph.text import (
    detect_language,
    embed_text,
    extract_concepts_from_keywords,
    extract_concepts_from_text,
    extract_keywords_from_text,
    translate_text,
)
from src.module.epfl_graph.video import (
    download_file,
    extract_audio,
    extract_slides,
    fingerprint_video,
    get_video_token,
)
from src.module.epfl_graph.voice import detect_audio_language, transcribe_audio

__all__ = [
    "category_chain",
    "category_graphsearch_url",
    "category_id_to_label",
    "category_info",
    "category_nearest_openalex_topics",
    "category_wikipedia",
    "concept_nearest_categories",
    "detect_audio_language",
    "detect_language",
    "download_file",
    "embed_text",
    "extract_audio",
    "extract_concepts_from_keywords",
    "extract_concepts_from_text",
    "extract_keywords_from_text",
    "extract_slides",
    "extract_text_from_slide",
    "fingerprint_video",
    "get_login_info",
    "get_video_token",
    "ontology_tree",
    "people_for_topics",
    "process_video",
    "publications_for_topics",
    "reset_login_info",
    "transcribe_audio",
    "translate_text",
    "units_for_topics",
]
