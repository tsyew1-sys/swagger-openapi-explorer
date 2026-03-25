#!/usr/bin/env python3
"""Helper for exploring Swagger/OpenAPI specs.

Capabilities:
- initialize from a Swagger UI page, raw JSON/YAML URL, or local file path
- cache the normalized spec on disk
- fuzzy-ish search across operations
- inspect an endpoint and produce example request/response JSON
- optionally execute a live API call

This script intentionally prefers standard-library behavior and a few common dependencies
(`requests`, `yaml`) so it remains portable inside a skill runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse

import requests
import yaml

DEFAULT_CACHE_DIR = os.environ.get("OPENAPI_HELPER_CACHE", "/tmp/swagger-openapi-explorer")
USER_AGENT = "swagger-openapi-explorer/1.0"
REQUEST_TIMEOUT = 20
MAX_RESPONSE_BYTES = 1_000_000
PRIVATE_HOST_PATTERNS = [
    r"^localhost$",
    r"^127\.",
    r"^10\.",
    r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
    r"^192\.168\.",
    r"^0\.0\.0\.0$",
    r"^169\.254\.",
    r"^::1$",
]


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


class SkillError(Exception):
    pass


def ensure_cache_dir(cache_dir: str) -> Path:
    path = Path(cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(cache_dir: str) -> Path:
    return ensure_cache_dir(cache_dir) / "state.json"


def make_spec_id(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def load_state(cache_dir: str) -> Dict[str, Any]:
    p = state_path(cache_dir)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def save_state(cache_dir: str, state: Dict[str, Any]) -> None:
    state_path(cache_dir).write_text(json.dumps(state, indent=2))


def get_active_spec_id(cache_dir: str, spec_id: Optional[str]) -> str:
    if spec_id:
        return spec_id
    state = load_state(cache_dir)
    active = state.get("active_spec_id")
    if not active:
        raise SkillError("No active spec. Run init first or pass --spec-id.")
    return active


def spec_file(cache_dir: str, spec_id: str) -> Path:
    return ensure_cache_dir(cache_dir) / f"{spec_id}.json"


def fetch_text(source: str) -> Tuple[str, str, str]:
    """Return content, final URL, and content-type."""
    if source.startswith("file://"):
        path = Path(source[7:])
        text = path.read_text(encoding="utf-8")
        return text, source, guess_content_type(path.name, text)
    if Path(source).exists():
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        return text, str(path.resolve()), guess_content_type(path.name, text)

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/yaml, text/yaml, text/plain, text/html, */*"}
    resp = requests.get(source, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    text = resp.text
    return text, resp.url, content_type


def guess_content_type(name: str, text: str) -> str:
    lname = name.lower()
    if lname.endswith(".json"):
        return "application/json"
    if lname.endswith(".yaml") or lname.endswith(".yml"):
        return "application/yaml"
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "application/json"
    if stripped.startswith("openapi:") or stripped.startswith("swagger:"):
        return "application/yaml"
    return "text/plain"


CONFIG_URL_PATTERNS = [
    re.compile(r"url\s*:\s*['\"]([^'\"]+)['\"]", re.I),
    re.compile(r"['\"]url['\"]\s*:\s*['\"]([^'\"]+)['\"]", re.I),
    re.compile(r"['\"]swaggerUrl['\"]\s*:\s*['\"]([^'\"]+)['\"]", re.I),
]

CONFIG_URLS_ARRAY_PATTERNS = [
    re.compile(r"urls\s*:\s*\[(.*?)\]", re.I | re.S),
    re.compile(r"['\"]urls['\"]\s*:\s*\[(.*?)\]", re.I | re.S),
]


def discover_spec_candidates(html: str, base_url: str) -> List[str]:
    candidates: List[str] = []
    for pattern in CONFIG_URL_PATTERNS:
        candidates.extend([urljoin(base_url, m.group(1).strip()) for m in pattern.finditer(html)])

    for pattern in CONFIG_URLS_ARRAY_PATTERNS:
        for outer in pattern.finditer(html):
            inner = outer.group(1)
            for m in re.finditer(r"url\s*:\s*['\"]([^'\"]+)['\"]", inner, re.I):
                candidates.append(urljoin(base_url, m.group(1).strip()))

    link_like = re.findall(r"['\"]([^'\"]+\.(?:json|ya?ml)(?:\?[^'\"]*)?)['\"]", html, flags=re.I)
    candidates.extend([urljoin(base_url, item) for item in link_like])

    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    common_paths = [
        "/swagger.json",
        "/openapi.json",
        "/openapi.yaml",
        "/openapi.yml",
        "/v3/api-docs",
        "/v2/api-docs",
        "/api-docs",
    ]
    for p in common_paths:
        candidates.append(urljoin(root, p))

    seen = set()
    ordered: List[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def parse_possible_spec(text: str) -> Optional[Dict[str, Any]]:
    stripped = text.lstrip()
    try:
        if stripped.startswith("{") or stripped.startswith("["):
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except Exception:
        return None
    if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
        return data
    return None


def fetch_and_detect_spec(source: str) -> Tuple[Dict[str, Any], str, List[str]]:
    text, final_url, content_type = fetch_text(source)
    warnings: List[str] = []
    spec = parse_possible_spec(text)
    if spec is not None:
        return spec, final_url, warnings

    if "html" not in content_type.lower() and "<html" not in text.lower():
        raise SkillError("Source did not look like a valid OpenAPI document or Swagger UI page.")

    candidates = discover_spec_candidates(text, final_url)
    if not candidates:
        raise SkillError("Could not discover an OpenAPI spec URL from the supplied page.")

    failures = []
    for candidate in candidates[:20]:
        try:
            candidate_text, resolved_url, _ = fetch_text(candidate)
            spec = parse_possible_spec(candidate_text)
            if spec is not None:
                warnings.append(f"Discovered spec from HTML page: {resolved_url}")
                return spec, resolved_url, warnings
        except Exception as exc:
            failures.append(f"{candidate}: {exc}")
            continue

    short_failures = failures[:5]
    raise SkillError("Could not fetch a valid spec from discovered candidates. Attempts: " + " | ".join(short_failures))


def get_json_pointer(doc: Dict[str, Any], pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise SkillError(f"Unsupported ref: {pointer}")
    current: Any = doc
    for token in pointer[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current[token]
    return current


def deref(obj: Any, root: Dict[str, Any], stack: Optional[List[str]] = None) -> Any:
    if stack is None:
        stack = []
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref = obj["$ref"]
            if ref in stack:
                return {"x-circular-ref": ref}
            if not isinstance(ref, str) or not ref.startswith("#/"):
                return {"x-unresolved-ref": ref}
            target = deepcopy(get_json_pointer(root, ref))
            merged = deepcopy(target)
            for k, v in obj.items():
                if k != "$ref":
                    merged[k] = deref(v, root, stack + [ref])
            return deref(merged, root, stack + [ref])
        return {k: deref(v, root, stack) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deref(v, root, stack) for v in obj]
    return obj


def normalize_spec(spec: Dict[str, Any], source_url: str) -> Dict[str, Any]:
    resolved = deref(spec, spec)
    servers = []
    if "servers" in resolved and isinstance(resolved["servers"], list):
        for item in resolved["servers"]:
            if isinstance(item, dict) and item.get("url"):
                servers.append(item["url"])
    elif "host" in resolved:
        scheme = (resolved.get("schemes") or ["https"])[0]
        base_path = resolved.get("basePath", "")
        servers.append(f"{scheme}://{resolved['host']}{base_path}")
    else:
        parsed = urlparse(source_url)
        if parsed.scheme and parsed.netloc:
            servers.append(f"{parsed.scheme}://{parsed.netloc}")

    endpoints = []
    global_security = resolved.get("security") or []
    global_tags = []
    for tag in resolved.get("tags", []) or []:
        if isinstance(tag, dict) and tag.get("name"):
            global_tags.append(tag["name"])

    paths = resolved.get("paths") or {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        common_params = path_item.get("parameters") or []
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if not isinstance(operation, dict):
                continue
            merged_params = []
            if common_params:
                merged_params.extend(common_params)
            if operation.get("parameters"):
                merged_params.extend(operation.get("parameters"))
            operation_copy = deepcopy(operation)
            operation_copy["parameters"] = merged_params
            endpoint = {
                "method": method.upper(),
                "path": path,
                "summary": operation.get("summary") or "",
                "description": operation.get("description") or "",
                "operationId": operation.get("operationId") or "",
                "tags": operation.get("tags") or [],
                "security": operation.get("security", global_security),
                "details": operation_copy,
            }
            endpoint["search_blob"] = " ".join(
                str(x) for x in [
                    endpoint["method"], endpoint["path"], endpoint["summary"], endpoint["description"],
                    endpoint["operationId"], " ".join(endpoint["tags"]),
                    " ".join(p.get("name", "") for p in merged_params if isinstance(p, dict)),
                ] if x
            ).lower()
            endpoints.append(endpoint)

    return {
        "title": (resolved.get("info") or {}).get("title") or "untitled api",
        "version": (resolved.get("info") or {}).get("version") or "unknown",
        "openapi": resolved.get("openapi") or resolved.get("swagger") or "unknown",
        "servers": servers,
        "tags": sorted(set(global_tags + [t for e in endpoints for t in e.get("tags", [])])),
        "securitySchemes": extract_security_schemes(resolved),
        "source_url": source_url,
        "spec": resolved,
        "endpoints": endpoints,
    }


def extract_security_schemes(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    comps = (spec.get("components") or {}).get("securitySchemes") or {}
    if not comps:
        comps = spec.get("securityDefinitions") or {}
    for name, scheme in comps.items():
        if isinstance(scheme, dict):
            result.append({
                "name": name,
                "type": scheme.get("type"),
                "scheme": scheme.get("scheme"),
                "in": scheme.get("in"),
                "header_name": scheme.get("name"),
            })
    return result


def save_spec(cache_dir: str, spec_id: str, normalized: Dict[str, Any]) -> None:
    spec_file(cache_dir, spec_id).write_text(json.dumps(normalized, indent=2))
    state = load_state(cache_dir)
    state["active_spec_id"] = spec_id
    state.setdefault("known_specs", {})[spec_id] = {
        "title": normalized["title"],
        "version": normalized["version"],
        "source_url": normalized["source_url"],
    }
    save_state(cache_dir, state)


def load_spec(cache_dir: str, spec_id: str) -> Dict[str, Any]:
    p = spec_file(cache_dir, spec_id)
    if not p.exists():
        raise SkillError(f"Unknown spec_id: {spec_id}")
    return json.loads(p.read_text())


TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def score_endpoint(endpoint: Dict[str, Any], keyword: str) -> Tuple[int, int]:
    kw = keyword.lower().strip()
    blob = endpoint["search_blob"]
    if kw in blob:
        return (0, blob.index(kw))
    words = [w for w in TOKEN_SPLIT_RE.split(kw) if w]
    missing = sum(1 for w in words if w not in blob)
    return (missing, len(blob))


def search_endpoints(spec: Dict[str, Any], keyword: str, method: Optional[str] = None, tag: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
    items = spec["endpoints"]
    if method:
        items = [e for e in items if e["method"] == method.upper()]
    if tag:
        items = [e for e in items if tag in e.get("tags", [])]
    ranked = sorted(items, key=lambda e: score_endpoint(e, keyword))
    results = []
    for e in ranked[:limit]:
        results.append({
            "method": e["method"],
            "path": e["path"],
            "summary": e.get("summary") or e.get("description") or "",
            "operationId": e.get("operationId") or "",
            "tags": e.get("tags") or [],
        })
    return results


PRIMITIVE_DEFAULTS = {
    "string": "string",
    "integer": 0,
    "number": 0,
    "boolean": True,
}


def first_json_content(content_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(content_obj, dict):
        return None
    for content_type, value in content_obj.items():
        if "json" in content_type.lower():
            return value
    return next(iter(content_obj.values()), None)


def schema_to_example(schema: Optional[Dict[str, Any]], depth: int = 0) -> Any:
    if schema is None:
        return None
    if depth > 6:
        return "..."
    if "example" in schema:
        return schema["example"]
    if "examples" in schema and isinstance(schema["examples"], dict) and schema["examples"]:
        first = next(iter(schema["examples"].values()))
        if isinstance(first, dict) and "value" in first:
            return first["value"]
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]
    if schema.get("nullable"):
        base = deepcopy(schema)
        base.pop("nullable", None)
        return schema_to_example(base, depth + 1)
    if "oneOf" in schema and schema["oneOf"]:
        return schema_to_example(schema["oneOf"][0], depth + 1)
    if "anyOf" in schema and schema["anyOf"]:
        return schema_to_example(schema["anyOf"][0], depth + 1)
    if "allOf" in schema and schema["allOf"]:
        merged: Dict[str, Any] = {"type": "object", "properties": {}}
        required: List[str] = []
        for part in schema["allOf"]:
            if isinstance(part, dict):
                props = part.get("properties") or {}
                merged["properties"].update(props)
                required.extend(part.get("required") or [])
        if required:
            merged["required"] = sorted(set(required))
        return schema_to_example(merged, depth + 1)
    if schema.get("type") == "array":
        return [schema_to_example(schema.get("items") or {}, depth + 1)]
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        result = {}
        for name, value in props.items():
            result[name] = schema_to_example(value, depth + 1)
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            result["additionalProperty1"] = schema_to_example(additional, depth + 1)
        return result
    fmt = schema.get("format")
    if fmt == "date-time":
        return "2026-01-01T00:00:00Z"
    if fmt == "date":
        return "2026-01-01"
    return PRIMITIVE_DEFAULTS.get(schema.get("type"), "string")


def swagger2_request_body(operation: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    body_param = None
    form_props = {}
    for param in operation.get("parameters") or []:
        if not isinstance(param, dict):
            continue
        if param.get("in") == "body":
            body_param = param
        elif param.get("in") == "formData":
            form_props[param.get("name", "field")] = {
                "type": param.get("type", "string"),
                "description": param.get("description", ""),
            }
    if body_param and isinstance(body_param, dict):
        return body_param.get("schema"), "application/json"
    if form_props:
        return {"type": "object", "properties": form_props}, "application/x-www-form-urlencoded"
    return None, None


def extract_request_schema(operation: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        content = first_json_content(request_body.get("content") or {})
        if isinstance(content, dict):
            return content.get("schema"), next((k for k in (request_body.get("content") or {}) if "json" in k.lower()), next(iter((request_body.get("content") or {}).keys()), None))
    return swagger2_request_body(operation)


def extract_response_example(operation: Dict[str, Any], preferred_status: Optional[str] = None) -> Tuple[Optional[str], Any]:
    responses = operation.get("responses") or {}
    candidates = []
    if preferred_status and preferred_status in responses:
        candidates.append((preferred_status, responses[preferred_status]))
    for key in ["200", "201", "202", "default"]:
        if key in responses and key != preferred_status:
            candidates.append((key, responses[key]))
    for key, value in responses.items():
        if (key, value) not in candidates:
            candidates.append((key, value))

    for status_code, response in candidates:
        if not isinstance(response, dict):
            continue
        content = first_json_content(response.get("content") or {})
        if isinstance(content, dict):
            if "example" in content:
                return status_code, content["example"]
            if "examples" in content and isinstance(content["examples"], dict) and content["examples"]:
                first = next(iter(content["examples"].values()))
                if isinstance(first, dict) and "value" in first:
                    return status_code, first["value"]
            schema = content.get("schema")
            if isinstance(schema, dict):
                return status_code, schema_to_example(schema)
        schema = response.get("schema")
        if isinstance(schema, dict):
            return status_code, schema_to_example(schema)
    return None, None


def inspect_endpoint(spec: Dict[str, Any], method: str, path: str, preferred_status: Optional[str] = None) -> Dict[str, Any]:
    match = None
    for endpoint in spec["endpoints"]:
        if endpoint["method"] == method.upper() and endpoint["path"] == path:
            match = endpoint
            break
    if not match:
        raise SkillError(f"Endpoint not found: {method.upper()} {path}")

    operation = match["details"]
    request_schema, request_content_type = extract_request_schema(operation)
    status_code, response_example = extract_response_example(operation, preferred_status)

    params_grouped = {"path": [], "query": [], "header": [], "cookie": []}
    for param in operation.get("parameters") or []:
        if not isinstance(param, dict):
            continue
        loc = param.get("in")
        if loc in params_grouped:
            params_grouped[loc].append({
                "name": param.get("name"),
                "required": bool(param.get("required")),
                "type": ((param.get("schema") or {}).get("type") if isinstance(param.get("schema"), dict) else None) or param.get("type"),
                "description": param.get("description") or "",
            })

    return {
        "method": match["method"],
        "path": match["path"],
        "summary": match.get("summary") or "",
        "description": match.get("description") or "",
        "operationId": match.get("operationId") or "",
        "tags": match.get("tags") or [],
        "security_required": bool(match.get("security")),
        "security": match.get("security") or [],
        "parameters": params_grouped,
        "request_content_type": request_content_type,
        "mock_request_body": schema_to_example(request_schema) if request_schema else None,
        "response_status": status_code,
        "mock_response_body": response_example,
    }


def fill_path_template(path: str, path_params: Dict[str, Any]) -> str:
    result = path
    for key, value in path_params.items():
        result = result.replace("{" + key + "}", quote(str(value), safe=""))
    return result


def assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SkillError(f"Only http/https URLs are allowed for live execution. Got: {parsed.scheme}")
    host = parsed.hostname or ""
    for pattern in PRIVATE_HOST_PATTERNS:
        if re.match(pattern, host, re.I):
            raise SkillError(f"Refusing to call private or local host: {host}")


def execute_call(spec: Dict[str, Any], method: str, path: str, path_params: Optional[Dict[str, Any]] = None,
                 query_params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, Any]] = None,
                 body: Any = None, auth_token: Optional[str] = None, server_url: Optional[str] = None,
                 timeout: int = REQUEST_TIMEOUT) -> Dict[str, Any]:
    details = inspect_endpoint(spec, method, path)
    base = server_url or next(iter(spec.get("servers") or []), None)
    if not base:
        raise SkillError("No server URL available for execution.")
    assert_public_http_url(base)
    final_path = fill_path_template(path, path_params or {})
    url = urljoin(base.rstrip("/") + "/", final_path.lstrip("/"))

    if query_params:
        existing = dict(parse_qsl(urlparse(url).query))
        existing.update({k: str(v) for k, v in query_params.items()})
        parsed = urlparse(url)
        url = parsed._replace(query=urlencode(existing, doseq=True)).geturl()

    headers = dict(headers or {})
    if auth_token and "authorization" not in {k.lower() for k in headers}:
        headers["Authorization"] = f"Bearer {auth_token}"
    if body is not None and "content-type" not in {k.lower() for k in headers}:
        headers["Content-Type"] = details.get("request_content_type") or "application/json"
    headers.setdefault("Accept", "application/json, */*")
    headers.setdefault("User-Agent", USER_AGENT)

    redact_headers = {k: ("<redacted>" if k.lower() in {"authorization", "cookie", "x-api-key"} else v) for k, v in headers.items()}

    request_kwargs: Dict[str, Any] = {"headers": headers, "timeout": timeout, "allow_redirects": False}
    if body is not None:
        content_type = headers.get("Content-Type", "")
        if "json" in content_type:
            request_kwargs["json"] = body
        else:
            request_kwargs["data"] = body

    resp = requests.request(method.upper(), url, **request_kwargs)
    raw = resp.content[:MAX_RESPONSE_BYTES]
    response_text = raw.decode(resp.encoding or "utf-8", errors="replace")
    try:
        parsed_body: Any = json.loads(response_text)
    except Exception:
        parsed_body = response_text

    return {
        "request_summary": {
            "method": method.upper(),
            "url": url,
            "headers": redact_headers,
            "body": body,
        },
        "status_code": resp.status_code,
        "response_headers": dict(resp.headers),
        "live_response_data": parsed_body,
    }


def json_arg(value: Optional[str], field_name: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise SkillError(f"Invalid JSON for {field_name}: {exc}") from exc


def cmd_init(args: argparse.Namespace) -> Dict[str, Any]:
    spec, detected_url, warnings = fetch_and_detect_spec(args.source)
    normalized = normalize_spec(spec, detected_url)
    spec_id = make_spec_id(detected_url)
    save_spec(args.cache_dir, spec_id, normalized)
    return {
        "status": "success",
        "spec_id": spec_id,
        "api_title": normalized["title"],
        "version": normalized["version"],
        "openapi_version": normalized["openapi"],
        "servers": normalized["servers"],
        "available_tags": normalized["tags"],
        "security_schemes": normalized["securitySchemes"],
        "endpoint_count": len(normalized["endpoints"]),
        "warnings": warnings,
    }


def cmd_search(args: argparse.Namespace) -> Any:
    spec = load_spec(args.cache_dir, get_active_spec_id(args.cache_dir, args.spec_id))
    return search_endpoints(spec, args.keyword, args.method, args.tag, args.limit)


def cmd_inspect(args: argparse.Namespace) -> Any:
    spec = load_spec(args.cache_dir, get_active_spec_id(args.cache_dir, args.spec_id))
    return inspect_endpoint(spec, args.method, args.path, args.status)


def cmd_execute(args: argparse.Namespace) -> Any:
    spec = load_spec(args.cache_dir, get_active_spec_id(args.cache_dir, args.spec_id))
    return execute_call(
        spec,
        args.method,
        args.path,
        path_params=json_arg(args.path_json, "path_json") or {},
        query_params=json_arg(args.query_json, "query_json") or {},
        headers=json_arg(args.headers_json, "headers_json") or {},
        body=json_arg(args.body_json, "body_json"),
        auth_token=args.auth_token,
        server_url=args.server_url,
        timeout=args.timeout,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explore a Swagger/OpenAPI spec")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Fetch and cache a spec")
    p_init.add_argument("source", help="Swagger UI URL, spec URL, file path, or file:// URL")
    p_init.set_defaults(func=cmd_init)

    p_search = sub.add_parser("search", help="Search cached endpoints")
    p_search.add_argument("keyword")
    p_search.add_argument("--spec-id")
    p_search.add_argument("--method")
    p_search.add_argument("--tag")
    p_search.add_argument("--limit", type=int, default=5)
    p_search.set_defaults(func=cmd_search)

    p_inspect = sub.add_parser("inspect", help="Inspect one endpoint")
    p_inspect.add_argument("method")
    p_inspect.add_argument("path")
    p_inspect.add_argument("--spec-id")
    p_inspect.add_argument("--status")
    p_inspect.set_defaults(func=cmd_inspect)

    p_exec = sub.add_parser("execute", help="Execute one endpoint")
    p_exec.add_argument("method")
    p_exec.add_argument("path")
    p_exec.add_argument("--spec-id")
    p_exec.add_argument("--path-json", help='JSON object for path params, e.g. {"id":123}')
    p_exec.add_argument("--query-json", help='JSON object for query params')
    p_exec.add_argument("--headers-json", help='JSON object for headers')
    p_exec.add_argument("--body-json", help='JSON body for request payload')
    p_exec.add_argument("--auth-token")
    p_exec.add_argument("--server-url")
    p_exec.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT)
    p_exec.set_defaults(func=cmd_execute)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except requests.HTTPError as exc:
        response = exc.response
        error_payload = {
            "status": "error",
            "error": "http_error",
            "message": str(exc),
        }
        if response is not None:
            error_payload["status_code"] = response.status_code
        print(json.dumps(error_payload, indent=2), file=sys.stderr)
        return 1
    except SkillError as exc:
        print(json.dumps({"status": "error", "error": "skill_error", "message": str(exc)}, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error": "unexpected_error", "message": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
