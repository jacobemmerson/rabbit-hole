Hit a Wikipedia REST API endpoint and pretty-print the response. Useful for verifying data shape, inspecting fields, and testing error handling during client development.

**Usage:** `/wiki-fetch <endpoint> <title>`

Supported endpoints: `summary`, `related`, `categories`

Examples:
- `/wiki-fetch summary Quantum_mechanics`
- `/wiki-fetch related Roman_Empire`
- `/wiki-fetch categories Photosynthesis`

**Arguments:** $ARGUMENTS

## Steps

1. Parse the arguments to extract the endpoint type and article title.
2. Construct the correct Wikipedia REST API URL:
   - summary: `https://en.wikipedia.org/api/rest_v1/page/summary/{title}`
   - related: `https://en.wikipedia.org/api/rest_v1/page/related/{title}`
   - categories: `https://en.wikipedia.org/w/api.php?action=query&titles={title}&prop=categories&format=json`
3. Fetch the URL using curl or the project's Wikipedia client if it exists.
4. Pretty-print the response JSON, highlighting the fields the project actually uses:
   - summary: `title`, `extract`, `categories`
   - related: `pages[].title`, `pages[].extract`
   - categories: `query.pages[*].categories[].title`
5. Note any missing fields, unexpected nulls, or error responses that the client will need to handle gracefully.
