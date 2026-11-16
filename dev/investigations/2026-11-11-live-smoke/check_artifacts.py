"""Check smoke artifacts for syntax errors and accidental credential values."""
import ast
import json
from pathlib import Path

from dotenv import dotenv_values

root = Path('dev/investigations/2026-11-11-live-smoke')
secrets = [item for name, value in dotenv_values('/run/gme.env').items()
           if value and any(term in name.upper() for term in ['TOKEN', 'PASSWORD', 'API_KEY', 'SECRET'])
           for item in value.split(',') if len(item) >= 8]
violations = []
files = list(root.glob('*'))
for path in files:
    if not path.is_file():
        continue
    text = path.read_text(encoding='utf-16' if path.read_bytes().startswith(b'\xff\xfe') else 'utf-8')
    if any(secret in text for secret in secrets):
        violations.append(path.name)
    if path.suffix == '.py':
        ast.parse(text)
    elif path.suffix == '.json':
        json.loads(text)
print(json.dumps({'files_checked': len(files), 'credential_leak_files': violations,
                  'python_syntax': 'valid', 'json_syntax': 'valid'}))
raise SystemExit(bool(violations))
