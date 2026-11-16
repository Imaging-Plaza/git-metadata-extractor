"""Bounded live smoke calls against the current worktree; no graph/index writes."""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

load_dotenv('/run/gme.env', override=False)
os.environ.update({
    'V2_USE_MOCK_PROVIDERS': 'false',
    'SOURCES_API_URL': 'http://gme-live-sources-20261111:8080',
    'SOURCES_API_TOKEN': os.environ['API_TOKEN'],
    'SOURCES_API_TIMEOUT_SECONDS': '90',
    'GIMIE_API_URL': 'http://gme-preview-gimie-1:15400',
    'INDEX_QDRANT_URL': 'http://gme-preview-qdrant-1:6333',
    'SELENIUM_REMOTE_URL': 'http://gme-preview-selenium-1:4444',
    'V2_PIPELINE_CACHE_ENABLED': 'false',
    'V2_PROVIDER_CACHE_ENABLED': 'false',
    'V2_PROVIDER_CACHE_PATH': '/tmp/live-providers.db',
    'V2_QUERY_LOG_DIR': '/tmp/live-query-logs',
    'V2_LINK_VERACITY_ENABLED': 'false',
    'V2_APPLY_CRITIC_PRUNING': 'false',
    'V2_SUBSTRATE_ENABLED': 'false',
    'V2_CANONICAL_OUTPUT_ENABLED': 'false',
    'V2_CONCEPT_TAGGING_ENABLED': 'false',
    'V2_EXPAND_OWNED_REPOS': 'false',
    'V2_ALLOW_REQUEST_MODEL_OVERRIDE': 'true',
    'V2_LLM_REQUEST_LIMIT': '4',
    'V2_LLM_TOOL_CALLS_LIMIT': '6',
    'V2_LLM_TIMEOUT_SECONDS': '60',
})
logging.disable(logging.CRITICAL)
sys.path.insert(0, str(Path.cwd()))
from git_metadata_extractor.agents.llm.model_config import ENV_VAR_MAPPINGS, load_model_config

for kind, env_name in ENV_VAR_MAPPINGS.items():
    configurations = load_model_config(kind)
    for config in configurations:
        config.update(timeout=60, max_retries=1, max_tokens=6000)
    os.environ[env_name] = json.dumps(configurations)

ROOT = Path('dev/investigations/2026-11-11-live-smoke')


def save(name, result):
    ROOT.joinpath(name + '.json').write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, default=str), flush=True)


async def sources():
    from git_metadata_extractor.providers.sources_service import SourcesClient, SourceSearchRequest
    client = SourcesClient(os.environ['SOURCES_API_URL'], token=os.environ['SOURCES_API_TOKEN'], timeout=90)
    results = []
    try:
        started = perf_counter()
        manifest = await client.manifest()
        results.append({'call': 'manifest', 'status': manifest.status, 'stores': len(manifest.sources),
                        'elapsed_seconds': round(perf_counter()-started, 2)})
        for index, query in [('ror', 'Ecole Polytechnique Federale de Lausanne'),
                             ('epfl_graph', 'software engineering metadata knowledge graphs')]:
            started = perf_counter()
            result = await client.search(index, SourceSearchRequest(query=query, top_k=3, candidate_k=5))
            results.append({'call': index, 'status': result.status, 'hits': len(result.hits),
                            'ids': [hit.id for hit in result.hits], 'error': result.error,
                            'elapsed_seconds': round(perf_counter()-started, 2)})
            print(json.dumps(results[-1]), flush=True)
    finally:
        await client.aclose()
    save('sources-results', results)


async def profile(runtime):
    from git_metadata_extractor.experiments.execution import run_profile
    from git_metadata_extractor.experiments.models import PipelineProfile
    # Keep model spend bounded: real GitHub context + root generator followed by
    # the normal post-agent sequence. This deliberately excludes entity fanout.
    full = runtime == 'rule_based_full'
    actual_runtime = 'rule_based' if full else runtime
    spec = PipelineProfile(name='live-' + runtime, runtime=actual_runtime,
                           stages={} if full else {'repository': ['context_gather', 'repo_agent']},
                           max_concurrent_agents=2, retry_max_retries=0)
    started = perf_counter()
    try:
        execution = await asyncio.wait_for(run_profile(spec, 'https://github.com/sdsc-ordes/gimie',
                            cache_dir=Path('/tmp/live-profile') / runtime, provider_mode='live'), timeout=300)
        response = execution.response.model_dump(mode='json', by_alias=True)
        ROOT.joinpath(runtime + '-response.json').write_text(json.dumps(response, indent=2))
        def internal_paths(value, path=''):
            if isinstance(value, dict):
                return [path+'/'+str(k) for k in value if str(k).startswith('_')] + [
                    p for k, v in value.items() for p in internal_paths(v, path+'/'+str(k))]
            if isinstance(value, list):
                return [p for i, v in enumerate(value) for p in internal_paths(v, path+'/'+str(i))]
            return []
        save(runtime + '-summary', {'status': 'completed', 'runtime': runtime,
             'scope': 'full pipeline' if full else 'live context + repository agent + normal post-agent stages; no entity fanout',
             'elapsed_seconds': round(perf_counter()-started, 2), 'cache_hit': execution.pipeline_cache_hit,
             'stats': response['stats'], 'warnings': response['warnings'],
             'internal_field_leaks': internal_paths(response['output']),
             'response_fields': list(response)})
    except Exception as exc:
        save(runtime + '-summary', {'status': 'failed', 'runtime': runtime,
            'elapsed_seconds': round(perf_counter()-started, 2), 'error_type': type(exc).__name__})


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode in {'integration', 'integration_org'}:
        import pytest
        paths = ['tests/v2/test_llm_repository_agent.py', 'tests/v2/test_llm_person_agent.py',
                 'tests/v2/test_llm_organization_agent.py']
        if mode == 'integration_org':
            os.environ['V2_LLM_REQUEST_LIMIT'] = '8'
            os.environ['V2_LLM_TOOL_CALLS_LIMIT'] = '12'
            paths = ['tests/v2/test_llm_organization_agent.py']
        sys.exit(pytest.main([*paths,
                             '-q', '-m', 'llm_integration', '--no-cov', '-p', 'no:cacheprovider', '--tb=short']))
    asyncio.run(sources() if mode == 'sources' else profile(mode))
