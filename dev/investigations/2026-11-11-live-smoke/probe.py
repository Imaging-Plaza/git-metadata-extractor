"""Bounded live availability probes; never prints credentials or response bodies."""
import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv('/run/gme.env', override=False)


async def main():
    keys = ['GME_GITHUB_TOKEN', 'RCP_TOKEN', 'OPENAI_API_KEY', 'INFOSCIENCE_TOKEN',
            'SOURCES_API_URL', 'SOURCES_API_TOKEN', 'API_TOKEN']
    print(json.dumps({'credential_presence': {k: bool(os.getenv(k)) for k in keys}}), flush=True)
    async with httpx.AsyncClient(timeout=25) as client:
        calls = [
            ('github', 'https://api.github.com/repos/sdsc-ordes/gimie',
             {'Authorization': 'Bearer ' + os.getenv('GME_GITHUB_TOKEN', '').split(',')[0]}),
            ('qdrant', 'http://gme-preview-qdrant-1:6333/collections', {}),
            ('selenium', 'http://gme-preview-selenium-1:4444/status', {}),
            ('gimie', 'http://gme-preview-gimie-1:15400/openapi.json', {}),
            ('rcp_models', 'https://inference-rcp.epfl.ch/v1/models',
             {'Authorization': 'Bearer ' + os.getenv('RCP_TOKEN', '').split(',')[0]}),
        ]
        async def probe(name, url, headers):
            try:
                response = await client.get(url, headers=headers)
                result = {'name': name, 'http_status': response.status_code}
                if response.is_success:
                    body = response.json()
                    if name == 'qdrant':
                        result['collections'] = [v['name'] for v in body['result']['collections']]
                    elif name == 'rcp_models':
                        result['models'] = [v['id'] for v in body['data']]
                    elif name == 'github':
                        result['repository'] = body.get('full_name')
                return result
            except Exception as exc:
                return {'name': name, 'error_type': type(exc).__name__}
        results = await asyncio.gather(*(probe(*args) for args in calls))
        print(json.dumps(results, indent=2), flush=True)
        Path('dev/investigations/2026-11-11-live-smoke/probe-results.json').write_text(json.dumps(results, indent=2))
    for name in ['app.py', 'api.py']:
        path = Path('/work/sources/open_pulse_sources/service') / name
        if path.exists():
            print('SOURCE FILE', name)
            print(path.read_text()[:11000] if name == 'app.py' else '\n'.join(
                line for line in path.read_text().splitlines()
                if any(token in line for token in ['def ', '@router', 'API_TOKEN', 'FastAPI'])))


asyncio.run(main())
