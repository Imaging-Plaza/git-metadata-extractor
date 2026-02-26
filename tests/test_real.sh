curl -X 'GET' \
  'http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=jsonld&force_refresh=true&include_intermediates=true' \
  -H 'accept: application/json' | jq > test.json
  
#-C .