import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "dashboard" / "web"


class DashboardWebTests(unittest.TestCase):
    def test_index_contains_primary_control_regions(self) -> None:
        html = (ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="connectionStatus"', html)
        self.assertIn('id="systemStatus"', html)
        self.assertIn('id="startupButton"', html)
        self.assertIn('id="shutdownPoseButton"', html)
        self.assertIn('id="diagnosticsPanel"', html)
        self.assertIn('id="recordingList"', html)
        self.assertIn('id="hardwareNotes"', html)
        self.assertIn('id="connectivityHints"', html)
        self.assertIn('id="configSnippets"', html)
        self.assertIn('id="voiceStatus"', html)
        self.assertIn('id="voiceDiagnostics"', html)

    def test_dashboard_css_contains_responsive_layout_rules(self) -> None:
        css = (ROOT / "dashboard.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 1120px)", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn(".layout", css)
        self.assertIn("grid-template-columns: 1fr;", css)

    def test_dashboard_js_starts_polling_at_400ms(self) -> None:
        source_path = ROOT / "dashboard.js"
        script = f"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
const pollCalls = [];
const nodes = {{}};

function createNode(id) {{
  return {{
    id,
    textContent: "",
    disabled: false,
    value: "",
    innerHTML: "",
    children: [],
    addEventListener: function () {{}},
    appendChild: function (child) {{
      this.children.push(child);
      if (typeof child.value !== "undefined") {{
        this.value = child.value;
      }}
    }},
  }};
}}

const context = {{
  console,
  Promise,
  JSON,
  setTimeout,
  clearTimeout,
}};

context.fetch = function (url) {{
  pollCalls.push(url);
  if (url === "/api/actions") {{
            return Promise.resolve({{
              json: function () {{
                return Promise.resolve({{
                  busy: false,
                  recordings: ["curious"],
                  poll_ms: 400,
                  actions: {{
                    startup: {{ enabled: true, state: "enabled", label: "启动灯" }},
                    play: {{ enabled: true, state: "enabled", label: "播放动作" }},
                    stop: {{ enabled: true, state: "enabled", label: "回到待机" }},
                    shutdown_pose: {{ enabled: true, state: "enabled", label: "进入休息" }},
                    light_solid: {{ enabled: true, state: "enabled", label: "暖黄灯光" }},
                    light_clear: {{ enabled: true, state: "enabled", label: "关闭灯光" }},
                  }},
                }});
              }},
            }});
  }}

  return Promise.resolve({{
    json: function () {{
      return Promise.resolve({{
        system: {{ status: "ready", active_action: null, last_update_ms: 0, reachable_urls: [] }},
        motion: {{ status: "idle", available_recordings: ["curious"] }},
        light: {{ status: "off" }},
        audio: {{ status: "ready" }},
        errors: [],
      }});
    }},
  }});
}};

context.window = {{
  intervalMs: null,
  setInterval: function (_fn, ms) {{
    this.intervalMs = ms;
  }},
  addEventListener: function () {{}},
  fetch: null,
}};

context.document = {{
  getElementById: function (id) {{
    if (!nodes[id]) {{
      nodes[id] = createNode(id);
    }}
    return nodes[id];
  }},
  createElement: function () {{
    return createNode("option");
  }},
}};

context.window.fetch = context.fetch;
vm.createContext(context);
vm.runInContext(source, context);

Promise.resolve(context.DashboardApp.start(context.document, context.window, context.fetch, 400))
  .then(function () {{
    console.log(JSON.stringify({{
      intervalMs: context.window.intervalMs,
      pollCalls,
      startupLabel: nodes.startupButton.textContent,
    }}));
  }})
  .catch(function (error) {{
    console.error(error && error.stack ? error.stack : String(error));
    process.exit(1);
  }});
"""

        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout.strip())
        self.assertEqual(payload["intervalMs"], 400)
        self.assertEqual(payload["pollCalls"], ["/api/actions", "/api/state"])
        self.assertEqual(payload["startupLabel"], "启动灯")

    def test_dashboard_js_disables_controls_when_action_catalog_fails_and_resyncs_recordings_from_state(self) -> None:
        source_path = ROOT / "dashboard.js"
        script = f"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
const nodes = {{}};

function createNode(id) {{
  return {{
    id,
    textContent: "",
    disabled: false,
    value: "",
    innerHTML: "",
    children: [],
    addEventListener: function () {{}},
    appendChild: function (child) {{
      this.children.push(child);
      if (typeof child.value !== "undefined") {{
        this.value = child.value;
      }}
    }},
  }};
}}

const context = {{
  console,
  Promise,
  JSON,
  setTimeout,
  clearTimeout,
}};

context.fetch = function (url) {{
  if (url === "/api/actions") {{
    return Promise.reject(new Error("offline"));
  }}
  return Promise.resolve({{
    json: function () {{
      return Promise.resolve({{
        system: {{ status: "ready", active_action: null, last_update_ms: 0, reachable_urls: ["http://127.0.0.1:8765"], uptime_s: 3 }},
        motion: {{
          status: "idle",
          available_recordings: ["curious"],
          current_recording: null,
          last_completed_recording: null,
          home_recording: "home_safe",
          startup_recording: "wake_up",
          motors_connected: "unknown",
          calibration_state: "unknown",
          last_result: null
        }},
        light: {{ status: "off", color: null }},
        audio: {{ status: "ready", output_device: "Line", volume_percent: 64 }},
        errors: []
      }});
    }}
  }});
}};

context.window = {{
  intervalMs: null,
  setInterval: function (_fn, ms) {{ this.intervalMs = ms; }},
  addEventListener: function () {{}},
  fetch: null,
}};

context.document = {{
  getElementById: function (id) {{
    if (!nodes[id]) {{
      nodes[id] = createNode(id);
    }}
    return nodes[id];
  }},
  createElement: function () {{
    return createNode("option");
  }},
}};

context.window.fetch = context.fetch;
vm.createContext(context);
vm.runInContext(source, context);

Promise.resolve(context.DashboardApp.start(context.document, context.window, context.fetch, 400))
  .then(function () {{
    console.log(JSON.stringify({{
      startupDisabled: nodes.startupButton.disabled,
      playDisabled: nodes.playButton.disabled,
      recordings: nodes.recordingSelect.children.map(function (child) {{ return child.value; }}),
    }}));
  }})
  .catch(function (error) {{
    console.error(error && error.stack ? error.stack : String(error));
    process.exit(1);
  }});
"""

        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout.strip())
        self.assertTrue(payload["startupDisabled"])
        self.assertTrue(payload["playDisabled"])
        self.assertEqual(payload["recordings"], ["curious"])

    def test_dashboard_js_avoids_browser_microphone_console_strings(self) -> None:
        js = (ROOT / "dashboard.js").read_text(encoding="utf-8")

        self.assertNotIn("setMicrophoneEnabled", js)
        self.assertNotIn("需 HTTPS", js)
        self.assertNotIn("/api/livekit/session", js)


if __name__ == "__main__":
    unittest.main()
