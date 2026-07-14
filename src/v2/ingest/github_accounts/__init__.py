"""GitHub account (user / organization) metadata parsers + their models.

Moved out of the retired v1 package (``src/v1/parsers`` +
``src/v1/data_models``) because the v2 ``RealGitHubProvider`` uses them for
user/org enrichment (GraphQL + REST, with optional Selenium-backed ORCID
lookup). See ``github_provider._resolve_user_lookup`` /
``_resolve_organization_lookup``.
"""
