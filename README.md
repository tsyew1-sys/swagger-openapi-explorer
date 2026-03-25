# Swagger OpenAPI Explorer Skill

A ChatGPT Skill for exploring Swagger/OpenAPI APIs conversationally.

Give the skill a Swagger UI page or OpenAPI spec URL, and it helps:

- locate relevant endpoints by intent
- inspect request and response schemas
- generate simplified example JSON
- optionally execute API calls in a controlled way

## What this skill is for

This skill is designed for flows like:

> Here’s a Swagger URL. Find the login endpoint, show me the response JSON.

Execution is optional. The main value is helping ChatGPT understand and navigate an API without loading the full raw spec into context.

## Core workflow

1. Initialize from a Swagger/OpenAPI URL
2. Search endpoints semantically
3. Inspect a chosen endpoint
4. View simplified request/response JSON
5. Optionally execute the endpoint

## Included tools

### `initialize_swagger`
Loads and normalizes a Swagger/OpenAPI document.

Typical responsibilities:
- fetch the underlying spec
- parse OpenAPI/Swagger structure
- resolve `$ref`
- collect endpoints, methods, and metadata
- cache the parsed result for follow-up actions

### `search_endpoints`
Searches the parsed API for relevant operations.

Useful for prompts like:
- “find login endpoint”
- “find create user”
- “find auth token route”

### `get_endpoint_details`
Returns model-friendly details for one endpoint.

Typical output includes:
- method and path
- summary/description
- parameters
- request body shape
- response schema
- simplified example JSON

### `execute_api_call`
Optionally performs a real API request.

This should be treated as a follow-up action, not the primary workflow.

## Intended user experience

Primary use case:

> Here’s a Swagger URL. Find the login endpoint and show me the response JSON.

Optional follow-up:

> Now call it.

## Repo structure

```text
.
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── ...
├── references/
│   └── ...
└── assets/