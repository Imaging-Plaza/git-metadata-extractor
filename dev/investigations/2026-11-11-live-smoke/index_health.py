"""Read collection counts to interpret empty semantic searches."""
import asyncio
import json
from pathlib import Path

import httpx


async def main():
    results = []
    async with httpx.AsyncClient(timeout=15) as client:
        for name in ['ror_europe', 'epfl_graph_disciplines']:
            response = await client.get('http://gme-preview-qdrant-1:6333/collections/' + name)
            item = {'collection': name, 'http_status': response.status_code}
            if response.is_success:
                result = response.json()['result']
                item.update(points=result['points_count'], status=result['status'])
            results.append(item)
    Path('dev/investigations/2026-11-11-live-smoke/index-health.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results))


asyncio.run(main())
