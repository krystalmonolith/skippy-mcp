# Example JSON configurations

Drop-in `--config` files for SkippyMCP's security modes. Pass one with
`skippy-mcp --config <file>`. All were exercised end-to-end against a real
MSO5204 (see the validation summary in `claude-design/`).

| File | Mode |
|------|------|
| `http-apikey.json` | Plain HTTP + Bearer API key |
| `https-tls.json` | HTTPS/TLS, no auth |
| `https-tls-apikey.json` | HTTPS/TLS + Bearer API key |

(For plain HTTP with no auth, pass no `--config` at all.)

## Notes

- **`api_key`** is a placeholder (`change-me-example-key`) — replace it with your
  own secret. When set, every request must carry `Authorization: Bearer <key>`.
- **`tls.cert` / `tls.key`** point at PEM files. The paths here (`/cfg/...`) are
  the container mount point used by the test harness
  (`docker run -v <repo>/examples/json-configuration:/cfg ...`); change them to
  wherever your certificate and key live. A self-signed dev cert can be made with:

  ```bash
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout dev-key.pem -out dev-cert.pem \
    -subj "/CN=localhost" -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
  ```

  PEM/key files are git-ignored and never committed.
- **`host` / `resource`** are the lowest-priority address source; CLI
  `--host`/`--resource` override them.
