"""
tests/test_phase5h_ui_bundle_ssr.py — verifies the Phase 5 UI console bundles and renders via SSR.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_console_bundle_and_ssr_render():
    root = Path(__file__).resolve().parents[1]
    ui_entry = root / "ui" / "console.jsx"
    assert ui_entry.exists(), "ui/console.jsx must exist"

    esbuild_bin = root / ".ui_verify" / "node_modules" / "esbuild" / "bin" / "esbuild"
    assert esbuild_bin.exists(), f"esbuild binary not found at {esbuild_bin}"

    node_bin = shutil.which("node")
    assert node_bin, "Node.js must be available on PATH"

    with TemporaryDirectory(dir=root) as tmpdir:
        tmpdir_path = Path(tmpdir)
        bundle_path = tmpdir_path / "bundle.js"
        render_script = tmpdir_path / "render.js"

        bundle_cmd = [
            node_bin,
            str(esbuild_bin),
            str(ui_entry),
            "--bundle",
            f"--outfile={bundle_path}",
            "--platform=node",
            "--format=cjs",
            "--loader:.jsx=jsx",
            "--external:react",
            "--external:lucide-react",
            "--log-level=warning",
        ]

        env = os.environ.copy()
        env["NODE_PATH"] = str(root / ".ui_verify" / "node_modules")

        bundle_proc = subprocess.run(bundle_cmd, cwd=root, capture_output=True, text=True, env=env)
        assert bundle_proc.returncode == 0, (
            "esbuild bundle failed:\n"
            f"stdout:\n{bundle_proc.stdout}\n"
            f"stderr:\n{bundle_proc.stderr}\n"
        )

        render_script.write_text(
            "const React = require('react');\n"
            "const ReactDOMServer = require('react-dom/server');\n"
            "const Console = require('./bundle.js').default;\n"
            "const html = ReactDOMServer.renderToStaticMarkup(React.createElement(Console));\n"
            "console.log(html.slice(0, 100));\n",
            encoding="utf-8",
        )

        render_proc = subprocess.run(
            [node_bin, str(render_script)],
            cwd=tmpdir_path,
            capture_output=True,
            text=True,
            env=env,
        )

        assert render_proc.returncode == 0, (
            "SSR render failed:\n"
            f"stdout:\n{render_proc.stdout}\n"
            f"stderr:\n{render_proc.stderr}\n"
        )
        assert render_proc.stdout.strip().startswith("<div"), "Rendered output must start with HTML markup"
