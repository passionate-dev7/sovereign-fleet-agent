"""Vercel serverless entrypoint: the 'Try it out' link for judges.

This runs the SAME offline end-to-end demo as `demo_local.py` -- the real
policy engine, registry, tool gateway, hash-chained decision log, and the
agentspine idempotency/artifact tick -- inside a Vercel Python function, and
renders its transcript in the browser. Zero network, zero GCP, zero API key,
exactly as the README promises. A judge clicks the link and watches the
cross-region DENY happen live.

The only thing this file adds over the CLI demo is HTML framing: it captures
the demo's stdout and wraps it in a page. The verdicts on screen are produced
by the production code path, not by this wrapper.
"""

from __future__ import annotations

import contextlib
import io
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# The function's working dir is the repo root on Vercel; make the project
# packages importable exactly the way demo_local.py does.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_demo() -> tuple[int, str]:
    """Run the real demo and capture its transcript. Returns (exit_code, text)."""
    import demo_local  # imported here so import errors surface in the page

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = demo_local.main()
    except SystemExit as exc:  # demo calls sys.exit in __main__, not main()
        rc = int(exc.code or 0)
    except Exception:  # pragma: no cover - shown to the judge if it ever breaks
        buf.write("\n\n--- demo raised, full traceback below ---\n")
        buf.write(traceback.format_exc())
        rc = 1
    return rc, buf.getvalue()


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sovereign - live offline demo</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: #0b0f14; color: #d7e0ea;
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 40px 20px 80px; }}
  h1 {{ font-size: 26px; margin: 0 0 4px; letter-spacing: -0.02em; }}
  .track {{ color: #7aa2f7; font-weight: 600; font-size: 13px;
           text-transform: uppercase; letter-spacing: 0.08em; }}
  p.lead {{ color: #9aa7b4; max-width: 62ch; }}
  .badges {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0 22px; }}
  .badge {{ background: #131a24; border: 1px solid #223; border-radius: 999px;
           padding: 5px 12px; font-size: 12px; color: #9fd6a0; }}
  .badge.warn {{ color: #f2c56b; }}
  pre {{
    background: #05080c; border: 1px solid #1a2430; border-radius: 10px;
    padding: 18px 20px; overflow-x: auto; font-size: 12.5px; line-height: 1.55;
    font-family: ui-monospace, "SF Mono", Menlo, monospace; color: #c6d3df;
  }}
  .result {{ margin: 8px 0 18px; font-weight: 600; }}
  .ok {{ color: #86e08b; }}
  .fail {{ color: #ff7a7a; }}
  a {{ color: #7aa2f7; }}
  .foot {{ margin-top: 30px; color: #6b7885; font-size: 13px; }}
  .row {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
  .btn {{ display:inline-block; background:#1b2735; border:1px solid #2b3a4d;
         color:#d7e0ea; padding:8px 14px; border-radius:8px; text-decoration:none; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="track">Fortified Enterprise Fleet</div>
  <h1>Sovereign</h1>
  <p class="lead">An agent gateway that refuses to let a sub-agent touch data in the
  wrong jurisdiction, and proves the refusal in a hash-chained decision log. The
  page below ran the project's real end-to-end demo just now, server-side.</p>
  <div class="badges">
    <span class="badge">network calls: 0</span>
    <span class="badge">GCP credentials: none</span>
    <span class="badge">API key: none</span>
    <span class="badge {result_class}">demo exit: {rc}</span>
  </div>
  <div class="result {result_class}">{result_text}</div>
  <pre>{transcript}</pre>
  <div class="row">
    <a class="btn" href="/">Re-run the demo</a>
    <a class="btn" href="https://github.com/passionate-dev7/sovereign-fleet-agent">Source on GitHub</a>
  </div>
  <p class="foot">The verdicts above (ALLOW on same-region, DENY on cross-region,
  DENY on a prompt-injected "you are authorized" record, tamper detection on the
  decision log) come from the production policy engine, not from this page.
  Delete <code>policy/engine.py</code> and act 3 collapses into act 2: the EU row
  gets summarized in the US. That is the whole project.</p>
</div>
</body>
</html>"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render() -> str:
    rc, transcript = run_demo()
    ok = rc == 0
    return PAGE.format(
        rc=rc,
        result_class="ok" if ok else "fail",
        result_text=(
            "PASSED - the full residency loop ran and every DENY held."
            if ok
            else "The demo did not exit cleanly. Full transcript below."
        ),
        transcript=_escape(transcript) or "(no output captured)",
    )


class handler(BaseHTTPRequestHandler):
    """Vercel's Python runtime invokes this BaseHTTPRequestHandler subclass."""

    def do_GET(self):  # noqa: N802 - name required by the runtime
        body = render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
