# Usage Notes

## Main commands

Initialize a spec:

```bash
python scripts/openapi_helper.py init "https://petstore3.swagger.io/api/v3/openapi.json"
```

Search for endpoints:

```bash
python scripts/openapi_helper.py search "login"
python scripts/openapi_helper.py search "create user" --method POST
```

Inspect an endpoint:

```bash
python scripts/openapi_helper.py inspect GET "/pet/{petId}"
python scripts/openapi_helper.py inspect POST "/user/login"
```

Execute a live call:

```bash
python scripts/openapi_helper.py execute GET "/pet/{petId}" --path-json '{"petId":123}'
python scripts/openapi_helper.py execute POST "/user/login" --query-json '{"username":"demo","password":"secret"}'
```

## Practical guidance

### Prefer inspect over execute

In most conversations the user wants one of these:
- the right endpoint
- the example response json
- the auth requirements
- the request shape

That means `inspect` is the main workhorse. Use `execute` only for explicit live testing.

### How search ranking works

Search matches against:
- method
- path
- summary
- description
- operation id
- tags
- parameter names

This is enough for common requests like `login`, `billing`, `season pass`, or `create user`.

### How example json is produced

The script prefers, in order:
1. documented `example`
2. documented `examples`
3. schema-derived mock JSON

Schema-derived output is intentionally lightweight. It is for understanding shape, not for guaranteeing exact production payloads.

## Common failure cases

### HTML page but no spec found

If `init` fails on a Swagger UI page, the page may be heavily customized or may require JS bootstrapping that does not expose a direct spec URL.

Next step:
- ask the user for the raw `.json` or `.yaml` spec URL if they have it
- if they do not, summarize the failure clearly

### No server URL available

Some specs omit usable server information. In that case, use `inspect` normally and only use `execute` if the user gives an explicit `--server-url`.

### Auth needed

If the inspected endpoint requires auth, tell the user before executing. Only ask for the token if they want a live call.

### Ambiguous match

If search returns several likely endpoints, show the top few and inspect the best one first. If the result is still ambiguous, mention the alternatives briefly.
