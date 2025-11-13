# Repository Analysis Agent Strategy

This document outlines the step-by-step analysis pipeline executed by the `Repository` class in `src/analysis/repositories.py`. The strategy involves a sequence of data extraction, AI-powered analysis, and enrichment steps to produce a comprehensive metadata profile for a given software repository.

## Analysis Pipeline Flowchart

The following diagram illustrates the complete analysis flow, including optional enrichment steps and the data models used at each stage.

```mermaid
graph TD
    subgraph "Start"
        A[Input: Repository URL]
    end

    subgraph "Cache & Pre-computation"
        B{Cache Check};
        C[run_gimie_analysis];
    end

    subgraph "Core LLM Analysis"
        D[run_llm_analysis<br/>Agent: llm_request_repo_infos<br/>DataModel: SoftwareSourceCode];
        E[run_authors_enrichment<br/>(ORCID Scraping)<br/>DataModel: Person];
    end

    subgraph "Optional Enrichments"
        F{enrich_users?};
        G[run_user_enrichment<br/>Agent: enrich_users_from_dict<br/>DataModel: UserEnrichmentResult];
        H{enrich_orgs?};
        I[run_organization_enrichment<br/>Agent: enrich_organizations_from_dict<br/>DataModel: OrganizationEnrichmentResult];
    end

    subgraph "Final Assessments"
        J[run_academic_catalog_enrichment<br/>Agent: enrich_repository_academic_catalog<br/>DataModel: AcademicCatalogEnrichmentResult];
        K[run_epfl_final_assessment<br/>Agent: assess_epfl_relationship<br/>DataModel: EPFLAssessmentResult];
    end

    subgraph "Finalization"
        L[run_validation];
        M[save_in_cache];
        Y[End: Return Enriched Data];
        Z[End: Return Cached Data];
    end

    %% --- Define Flow ---
    A --> B;
    B -- Cache Miss / Force Refresh --> C;
    B -- Cache Hit --> Z;

    C --> D;
    D --> E;
    E --> F;

    F -- Yes --> G;
    G --> H;
    F -- No --> H;

    H -- Yes --> I;
    I --> J;
    H -- No --> J;

    J --> K;
    K --> L;
    L --> M;
    M --> Y;

    %% --- Style Definitions ---
    style A fill:#f9f,stroke:#333,stroke-width:2px
    style Z fill:#bfa,stroke:#333,stroke-width.md:2px
    style Y fill:#bfa,stroke:#333,stroke-width.md:2px
    classDef agentNode fill:#dff,stroke:#333,stroke-width.md:2px
    class D,G,I,J,K agentNode
```

## Pipeline Steps Explained

The `Repository.run_analysis` method orchestrates the following steps in sequence:

1.  **Cache Check**: Before any processing, the system checks if a complete, cached result for the given repository URL already exists. If a valid cache entry is found and `force_refresh` is `false`, the cached data is returned immediately, and the pipeline stops.

2.  **GIMIE Analysis (`run_gimie_analysis`)**:
    - **Purpose**: Extracts basic, structured metadata from the repository using the `gimie` tool.
    - **Output**: A JSON-LD graph which is used as context for the subsequent LLM analysis.

3.  **Core LLM Analysis (`run_llm_analysis`)**:
    - **Agent**: `llm_request_repo_infos`
    - **Purpose**: This is the main analysis step. The agent receives the repository's content (code, READMEs, etc.) and the GIMIE output. It analyzes this context to generate the initial `SoftwareSourceCode` object.
    - **Data Model**: `SoftwareSourceCode`

4.  **Author ORCID Enrichment (`run_authors_enrichment`)**:
    - **Purpose**: A non-agent step that iterates through the authors identified by the LLM. If an author has an ORCID iD, this step scrapes their public ORCID profile to add affiliation data.
    - **Data Model**: Modifies the `Person` objects within the `SoftwareSourceCode.author` list.

5.  **User Enrichment (`run_user_enrichment`)** - *Optional*:
    - **Triggered by**: `enrich_users=true` query parameter.
    - **Agent**: `enrich_users_from_dict`
    - **Purpose**: Performs a deep analysis of git authors and existing author data. It uses tools to search ORCID and the web to create detailed author profiles, including affiliation history and contribution summaries.
    - **Data Model**: The agent returns a `UserEnrichmentResult`, and the `EnrichedAuthor` objects within it are converted to `Person` objects, replacing the existing author list in `self.data`.

6.  **Organization Enrichment (`run_organization_enrichment`)** - *Optional*:
    - **Triggered by**: `enrich_orgs=true` query parameter.
    - **Agent**: `enrich_organizations_from_dict`
    - **Purpose**: Analyzes git author emails and existing organization mentions to identify and standardize institutional affiliations. It uses the ROR (Research Organization Registry) API to fetch canonical data for organizations.
    - **Data Model**: The agent returns an `OrganizationEnrichmentResult`. The `Organization` objects from this result replace the `relatedToOrganizations` list in `self.data`.

7.  **Academic Catalog Enrichment (`run_academic_catalog_enrichment`)**:
    - **Agent**: `enrich_repository_academic_catalog`
    - **Purpose**: Searches academic catalogs (currently EPFL Infoscience) for publications, researchers, and labs related to the repository, its authors, and its affiliated organizations.
    - **Data Model**: Returns an `AcademicCatalogEnrichmentResult`. The `AcademicCatalogRelation` objects are then assigned to the `academicCatalogRelations` fields on the main `SoftwareSourceCode` object as well as on the individual `Person` and `Organization` objects.

8.  **EPFL Final Assessment (`run_epfl_final_assessment`)**:
    - **Agent**: `assess_epfl_relationship`
    - **Purpose**: This is the final step in the analysis. This agent performs a holistic review of all data collected in the previous steps to make a definitive, evidence-based judgment on the repository's relationship to EPFL.
    - **Data Model**: Returns an `EPFLAssessmentResult`. The findings (`relatedToEPFL`, `relatedToEPFLConfidence`, `relatedToEPFLJustification`) overwrite any previous values in `self.data` to ensure consistency.

9.  **Validation & Caching (`run_validation`, `save_in_cache`)**:
    - **Purpose**: The final, enriched `SoftwareSourceCode` object is validated against the Pydantic model one last time. If valid, the complete result is saved to the SQLite cache for future requests.
    - **Output**: The final, enriched `SoftwareSourceCode` object is returned.
