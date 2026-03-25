---
name: swagger-openapi-explorer
description: explore swagger and openapi specs from a swagger ui url, raw spec url, or local spec file. use when a user wants to find an endpoint such as login, inspect request or response json, understand auth requirements, or optionally test a public api endpoint without loading the whole spec into context.
---

# Swagger OpenAPI Explorer

Use this skill to explore an API conversationally from a Swagger UI page or raw OpenAPI spec.

This skill is optimized for the common workflow: initialize the spec, search for the relevant endpoint, inspect its request and response shapes, and execute a live call only if the user explicitly asks.

## Quick workflow

1. Initialize the spec.
2. Search endpoints by user intent.
3. Inspect the best endpoint and show example request and response JSON.
4. Execute only when the user clearly wants a live API call.

Default behavior: favor discovery and explanation over execution.

## Initialize the spec

Run:

```bash
python scripts/openapi_helper.py init "<swagger-ui-or-spec-url>"
```

The script accepts:
- Swagger UI HTML pages
- raw `.json`, `.yaml`, or `.yml` specs
- local file paths
- `file://` URLs

After init, the script stores the normalized spec in a cache directory and marks it active for later `search`, `inspect`, and `execute` commands.

What to do after init:
- report the API title, version, tags, server URLs, and any warnings
- if warnings say the spec was discovered from HTML, mention that briefly
- if multiple endpoints may fit the user request, continue with `search`

## Search for the endpoint

Run:

```bash
python scripts/openapi_helper.py search "login"
```

Optional filters:

```bash
python scripts/openapi_helper.py search "login" --method POST --tag Authentication
```

Search results are already trimmed. Present the top matches with method, path, summary, and tags. Prefer the endpoint whose summary, operation id, path, and tags best match the user’s intent.

## Inspect an endpoint

Run:

```bash
python scripts/openapi_helper.py inspect POST "/api/authentication/login"
```

Inspection is the main value of this skill. Use it to answer questions like:
- “find the login endpoint”
- “show me the response json”
- “what auth does this need?”
- “what fields are required?”

When presenting inspection results:
- show the method and path
- summarize auth requirements
- list required path, query, and header parameters
- show the mock request body if relevant
- show the mock or example response JSON
- mention the response status code used for the example

If the script returned a schema-derived example rather than a real example, say so plainly.

## Execute a live call only when requested

Only run a live request if the user explicitly asks to call or test the API.

Run:

```bash
python scripts/openapi_helper.py execute POST "/api/authentication/login" \
  --body-json '{"username":"demo","password":"demo"}'
```

Optional arguments:
- `--path-json` for path parameters
- `--query-json` for query parameters
- `--headers-json` for custom headers
- `--auth-token` for bearer auth
- `--server-url` to override the default server

## Safety rules

Follow these rules every time:

- inspect before execute unless the user already gave a fully specified call and explicitly asked to run it
- never execute write operations such as `POST`, `PUT`, `PATCH`, or `DELETE` unless the user explicitly asks
- if auth is required, explain what the endpoint expects before asking for a token
- never echo secrets back in full; redact tokens and sensitive headers in your summary
- refuse or avoid calls to local, loopback, or private-network hosts
- if execution fails, show the structured error and suggest the next best inspection step instead of guessing

## Output style

Use this structure when it fits:

```markdown
**Best match:** `POST /api/authentication/login`

Why it matches:
- authentication tag
- summary mentions login

Auth:
- bearer token not required for this endpoint

Request body example:
```json
{...}
```

Response example for `200`:
```json
{...}
```
```

Keep the narrative short. The JSON and endpoint identity matter more than long prose.

## Limitations

This skill is best-effort, not a full OpenAPI validator.

Known limitations:
- it handles common Swagger UI patterns but may miss heavily customized pages
- it resolves internal refs, not arbitrary remote ref chains
- complex `oneOf` or `allOf` schemas are simplified to practical examples
- multipart and unusual auth schemes may need manual inspection

If the spec is too unusual for the script, explain the limitation clearly and fall back to describing what was successfully extracted.

## Resources

- Use `scripts/openapi_helper.py` for all fetch, search, inspect, and execute steps.
- Use `references/usage-notes.md` when you need examples, edge cases, or a reminder of the script’s capabilities.
