"""Temporary source API over existing read-only index files."""
import logging
import os

from dotenv import load_dotenv

load_dotenv('/run/gme.env', override=False)
os.environ['INDEX_QDRANT_URL'] = 'http://gme-preview-qdrant-1:6333'
os.environ['INDEX_DATA_DIR'] = '/work/gme/data/index'
os.environ['V2_PROVIDER_CACHE_PATH'] = '/tmp/sources-smoke.db'
logging.disable(logging.CRITICAL)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('open_pulse_sources.service.app:app', host='0.0.0.0', port=8080,
                access_log=False, log_level='critical')
