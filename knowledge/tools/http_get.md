---
tools: [http_get, try_http_login, grep_file]
phase: [network, recon]
---

# http_get

## When to Use
- Fetch a web page (router admin UI, login page) before any authentication attempt.
- The FULL response body is always saved to `state/sessions/<id>/artifacts/http_get_*.html` (`artifact_path` in the result); the inline `content` field is only a preview.

## Typical Workflow (web_auth)
1. `http_get(url=<target>)` — note `artifact_path`, `keyword_hits`, `content_length`.
2. `grep_file(path=<artifact_path>, pattern='login|xmlobj|password|form|action=|\\.js', context_lines=1)` — locate the real login mechanism (form fields, XML token, JS endpoint).
3. Only then `try_http_login(...)` with the discovered field names/endpoint. Blind login attempts before fetching are blocked by the planner.

## Example
```json
{"name":"http_get","arguments":{"url":"http://192.168.1.1/"}}
```
Then:
```json
{"name":"grep_file","arguments":{"path":"state/sessions/<id>/artifacts/http_get_20260610_x.html","pattern":"xmlobj|form|action=|password","max_matches":40,"context_lines":1}}
```

## Do Not Use
- Not for sniffing traffic (use capture_packets/analyze_pcapng).
- Do not raise `max_chars` to dump huge pages into context — grep the artifact instead.
