# LeLamp Manager + Item Layer v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed item layer, a GLM-first manager sidecar, derived memory artifacts, and a constrained action DSL without breaking the current low-latency realtime voice path.

**Architecture:** Keep the existing realtime speaker model in charge of immediate conversation and tool calls. Add a slower manager pipeline that consumes typed items, writes derived memory snapshots, and emits scene/action proposals through a validated DSL that compiles into the current motor and RGB executors.

**Tech Stack:** Python 3.12, pytest, LiveKit realtime models, existing `lelamp.memory` file-first storage, `AnimationService`, `RGBService`, GLM/Qwen provider support already present in `runtime_config.py`

---

## File Map

- Create: `lelamp/items/__init__.py`
  - package export for item-layer helpers
- Create: `lelamp/items/schema.py`
  - typed item kinds, envelopes, and payload validation
- Create: `lelamp/items/store.py`
  - append-only item log + query helpers
- Create: `lelamp/items/projections.py`
  - projection helpers from current runtime events into item envelopes
- Create: `lelamp/manager/__init__.py`
  - package export for manager runtime
- Create: `lelamp/manager/base.py`
  - manager interface and snapshot schema
- Create: `lelamp/manager/glm_manager.py`
  - GLM-backed manager implementation
- Create: `lelamp/manager/runtime.py`
  - background manager loop, item ingestion, snapshot publishing
- Create: `lelamp/memory/derived.py`
  - read/write derived memory artifacts like profile, policy hints, and scene priors
- Create: `lelamp/action_dsl/__init__.py`
  - package export for DSL helpers
- Create: `lelamp/action_dsl/schema.py`
  - scene/action primitives and validation
- Create: `lelamp/action_dsl/compiler.py`
  - lower DSL scenes into recordings, interpolated motion segments, and RGB commands
- Create: `lelamp/action_dsl/executor.py`
  - validated execution bridge into `AnimationService` and `RGBService`
- Modify: `lelamp/runtime_config.py`
  - add manager model/provider settings and item-layer paths
- Modify: `lelamp/voice_profile.py`
  - prepend manager snapshot hints ahead of current personality prompt
- Modify: `lelamp/memory/runtime.py`
  - mirror current runtime events into item projections
- Modify: `smooth_animation.py`
  - start/stop manager runtime and wire item store + manager snapshot into agent bootstrap
- Create: `lelamp/test/test_items_schema.py`
- Create: `lelamp/test/test_items_store.py`
- Create: `lelamp/test/test_manager_runtime.py`
- Create: `lelamp/test/test_memory_derived.py`
- Create: `lelamp/test/test_action_dsl.py`
- Create: `lelamp/test/test_action_dsl_compiler.py`
- Modify: `lelamp/test/test_qwen_realtime.py`
- Modify: `lelamp/test/test_glm_realtime.py`
- Modify: `lelamp/test/test_memory_runtime.py`
- Modify: `README.md`
  - document the new architecture and toggles

### Task 1: Introduce The Typed Item Layer

**Files:**
- Create: `lelamp/items/__init__.py`
- Create: `lelamp/items/schema.py`
- Create: `lelamp/items/store.py`
- Create: `lelamp/items/projections.py`
- Create: `lelamp/test/test_items_schema.py`
- Create: `lelamp/test/test_items_store.py`

- [ ] **Step 1: Write the failing item schema tests**

```python
from lelamp.items.schema import build_item, validate_item


def test_build_item_emits_envelope():
    item = build_item(
        kind="conversation.reply",
        producer="speaker_realtime",
        session_id="sess_2026-04-19_20-00-00",
        payload={"text": "灯灯在。"},
        ts_ms=1776500000000,
        item_id="itm_1",
    )
    assert item["schema"] == "lelamp.item.v1"
    assert item["kind"] == "conversation.reply"
    assert item["payload"]["text"] == "灯灯在。"
    validate_item(item)


def test_validate_item_rejects_unknown_kind():
    item = {
        "schema": "lelamp.item.v1",
        "item_id": "itm_1",
        "ts_ms": 1776500000000,
        "session_id": "sess_2026-04-19_20-00-00",
        "kind": "unknown.kind",
        "producer": "speaker_realtime",
        "payload": {},
    }
    with pytest.raises(ValueError, match="unknown item kind"):
        validate_item(item)
```

- [ ] **Step 2: Run the new item schema tests to verify RED**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_items_schema.py -q`
Expected: FAIL because the `lelamp.items` package does not exist yet

- [ ] **Step 3: Implement the item schema and store**

```python
# lelamp/items/schema.py
ITEM_SCHEMA = "lelamp.item.v1"
ITEM_KINDS = {
    "perception.asr_commit",
    "conversation.user_turn",
    "conversation.reply",
    "conversation.tool_invoke",
    "conversation.tool_result",
    "memory.profile_update",
    "memory.episode_summary",
    "scene.proposal",
    "action.plan",
    "execution.result",
    "execution.guardrail_reject",
}


def build_item(*, kind, producer, session_id, payload, ts_ms, item_id):
    item = {
        "schema": ITEM_SCHEMA,
        "item_id": item_id,
        "ts_ms": ts_ms,
        "session_id": session_id,
        "kind": kind,
        "producer": producer,
        "payload": dict(payload),
    }
    validate_item(item)
    return item
```

```python
# lelamp/items/store.py
class ItemStore:
    def __init__(self, root: Path) -> None:
        self._path = Path(root)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, item: Mapping[str, Any]) -> None:
        validate_item(item)
        line = json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def iter_items(self) -> Iterator[dict[str, Any]]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
```

- [ ] **Step 4: Add a projection helper from existing runtime events**

```python
# lelamp/items/projections.py
def project_conversation_reply(*, session_id: str, text: str, ts_ms: int) -> dict[str, Any]:
    return build_item(
        kind="conversation.reply",
        producer="speaker_realtime",
        session_id=session_id,
        payload={"text": text},
        ts_ms=ts_ms,
        item_id=f"itm_reply_{ts_ms}",
    )
```

- [ ] **Step 5: Run the item-layer tests to verify GREEN**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_items_schema.py lelamp/test/test_items_store.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lelamp/items lelamp/test/test_items_schema.py lelamp/test/test_items_store.py
git commit -m "feat(items): add typed item layer foundation"
```

### Task 2: Add Derived Memory Sidecars And Manager Snapshot Types

**Files:**
- Create: `lelamp/manager/__init__.py`
- Create: `lelamp/manager/base.py`
- Create: `lelamp/memory/derived.py`
- Create: `lelamp/test/test_memory_derived.py`

- [ ] **Step 1: Write the failing derived-memory tests**

```python
from lelamp.memory.derived import DerivedMemoryStore


def test_profile_snapshot_round_trip(tmp_path):
    store = DerivedMemoryStore(tmp_path / "memory" / "default")
    payload = {
        "profile_summary": "User likes short teasing replies.",
        "preference_hints": ["keep replies short"],
        "scene_priors": {"greeting": ["warm_gradient"]},
        "banned_patterns": ["repeat_same_scene"],
        "updated_at_ms": 1776500000000,
    }
    store.write_snapshot("manager_snapshot.v1.json", payload)
    assert store.read_snapshot("manager_snapshot.v1.json") == payload
```

- [ ] **Step 2: Run the new derived-memory tests to verify RED**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_memory_derived.py -q`
Expected: FAIL because `DerivedMemoryStore` does not exist yet

- [ ] **Step 3: Implement the derived-memory store and snapshot schema**

```python
# lelamp/manager/base.py
@dataclass(frozen=True)
class ManagerSnapshot:
    profile_summary: str
    preference_hints: list[str]
    scene_priors: dict[str, list[str]]
    banned_patterns: list[str]
    updated_at_ms: int
```

```python
# lelamp/memory/derived.py
class DerivedMemoryStore:
    def __init__(self, user_dir: Path) -> None:
        self._root = Path(user_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def write_snapshot(self, filename: str, payload: Mapping[str, Any]) -> Path:
        path = self._root / filename
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def read_snapshot(self, filename: str) -> dict[str, Any] | None:
        path = self._root / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run the derived-memory tests to verify GREEN**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_memory_derived.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lelamp/manager lelamp/memory/derived.py lelamp/test/test_memory_derived.py
git commit -m "feat(memory): add derived manager snapshots"
```

### Task 3: Add The GLM-First Manager Runtime

**Files:**
- Modify: `lelamp/runtime_config.py`
- Create: `lelamp/manager/glm_manager.py`
- Create: `lelamp/manager/runtime.py`
- Create: `lelamp/test/test_manager_runtime.py`

- [ ] **Step 1: Write the failing manager runtime tests**

```python
from types import SimpleNamespace

from lelamp.manager.runtime import ManagerRuntime


def test_manager_runtime_writes_snapshot_from_items(tmp_path):
    class FakeManager:
        def process(self, items, previous_snapshot):
            return {
                "profile_summary": "User keeps saying hi after silence.",
                "preference_hints": ["use greeting scenes"],
                "scene_priors": {"greeting": ["warm_gradient"]},
                "banned_patterns": [],
                "updated_at_ms": 1776500000000,
            }

    runtime = ManagerRuntime(
        manager=FakeManager(),
        item_store_path=tmp_path / "items.jsonl",
        derived_root=tmp_path / "memory",
    )
    runtime.process_once(
        session_id="sess_2026-04-19_20-00-00",
        items=[{"kind": "conversation.user_turn", "payload": {"text": "你回来啦"}}],
    )
    snapshot = runtime.load_snapshot()
    assert snapshot["scene_priors"]["greeting"] == ["warm_gradient"]
```

- [ ] **Step 2: Run the manager tests to verify RED**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_manager_runtime.py -q`
Expected: FAIL because `ManagerRuntime` does not exist yet

- [ ] **Step 3: Add manager runtime settings**

```python
@dataclass(frozen=True)
class RuntimeSettings:
    ...
    manager_provider: str
    manager_api_key: str | None
    manager_model: str | None
    manager_base_url: str | None
    item_store_path: str
```

```python
def load_runtime_settings() -> RuntimeSettings:
    ...
    return RuntimeSettings(
        ...
        manager_provider=_normalize_model_provider(_get_optional_str("LELAMP_MANAGER_PROVIDER") or "glm"),
        manager_api_key=_get_optional_str("LELAMP_MANAGER_API_KEY") or _get_optional_str("ZAI_API_KEY"),
        manager_model=_get_optional_str("LELAMP_MANAGER_MODEL") or "glm-4.5-air",
        manager_base_url=_get_optional_str("LELAMP_MANAGER_BASE_URL"),
        item_store_path=_get_str("LELAMP_ITEM_STORE_PATH", "/tmp/lelamp-items.jsonl"),
    )
```

- [ ] **Step 4: Implement a synchronous manager interface and runtime loop**

```python
# lelamp/manager/runtime.py
class ManagerRuntime:
    SNAPSHOT_FILE = "manager_snapshot.v1.json"

    def __init__(self, *, manager, item_store_path: Path, derived_root: Path) -> None:
        self._manager = manager
        self._item_store = ItemStore(item_store_path)
        self._derived = DerivedMemoryStore(derived_root)

    def process_once(self, *, session_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        previous = self.load_snapshot()
        snapshot = self._manager.process(items=items, previous_snapshot=previous)
        self._derived.write_snapshot(self.SNAPSHOT_FILE, snapshot)
        return snapshot

    def load_snapshot(self) -> dict[str, Any] | None:
        return self._derived.read_snapshot(self.SNAPSHOT_FILE)
```

- [ ] **Step 5: Stub the first GLM manager implementation**

```python
# lelamp/manager/glm_manager.py
class GLMManager:
    def __init__(self, *, settings: RuntimeSettings) -> None:
        self._settings = settings

    def process(self, *, items: list[dict[str, Any]], previous_snapshot: dict[str, Any] | None) -> dict[str, Any]:
        del previous_snapshot
        last_user_turn = next(
            (item for item in reversed(items) if item.get("kind") == "conversation.user_turn"),
            None,
        )
        text = ((last_user_turn or {}).get("payload") or {}).get("text", "")
        return {
            "profile_summary": f"Recent user theme: {text}".strip(),
            "preference_hints": [],
            "scene_priors": {},
            "banned_patterns": [],
            "updated_at_ms": int(time.time() * 1000),
        }
```

- [ ] **Step 6: Run the manager tests to verify GREEN**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_manager_runtime.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add lelamp/runtime_config.py lelamp/manager lelamp/test/test_manager_runtime.py
git commit -m "feat(manager): add glm-first sidecar runtime"
```

### Task 4: Add The Constrained Action DSL

**Files:**
- Create: `lelamp/action_dsl/__init__.py`
- Create: `lelamp/action_dsl/schema.py`
- Create: `lelamp/action_dsl/compiler.py`
- Create: `lelamp/action_dsl/executor.py`
- Create: `lelamp/test/test_action_dsl.py`
- Create: `lelamp/test/test_action_dsl_compiler.py`

- [ ] **Step 1: Write the failing DSL validation tests**

```python
import pytest

from lelamp.action_dsl.schema import validate_scene


def test_validate_scene_accepts_known_primitives():
    scene = {
        "body": [{"type": "gesture", "name": "nod", "intensity": 0.4, "repeats": 1}],
        "light": [{"type": "pulse", "palette": [[255, 180, 90], [255, 120, 40]], "bpm": 92, "cycles": 2}],
    }
    validate_scene(scene)


def test_validate_scene_rejects_unknown_primitive():
    scene = {"body": [{"type": "servo_frame", "angles": [1, 2, 3]}], "light": []}
    with pytest.raises(ValueError, match="unsupported primitive"):
        validate_scene(scene)
```

- [ ] **Step 2: Run the DSL tests to verify RED**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_action_dsl.py -q`
Expected: FAIL because the DSL module does not exist yet

- [ ] **Step 3: Implement the DSL schema**

```python
BODY_PRIMITIVES = {"pose", "gesture", "look", "sweep", "settle"}
LIGHT_PRIMITIVES = {"solid", "gradient", "pulse", "sparkle"}


def validate_scene(scene: Mapping[str, Any]) -> None:
    for node in scene.get("body", []):
        if node.get("type") not in BODY_PRIMITIVES:
            raise ValueError(f"unsupported primitive: {node.get('type')}")
    for node in scene.get("light", []):
        if node.get("type") not in LIGHT_PRIMITIVES:
            raise ValueError(f"unsupported primitive: {node.get('type')}")
```

- [ ] **Step 4: Implement a first compiler that lowers scenes into current executors**

```python
# lelamp/action_dsl/compiler.py
def compile_scene(scene: Mapping[str, Any]) -> dict[str, list[tuple[str, object]]]:
    validate_scene(scene)
    motion_events: list[tuple[str, object]] = []
    light_events: list[tuple[str, object]] = []

    for node in scene.get("body", []):
        if node["type"] == "gesture" and node["name"] == "nod":
            motion_events.append(("play", "nod"))
        elif node["type"] == "gesture" and node["name"] == "greeting":
            motion_events.append(("play", "wake_up"))

    for node in scene.get("light", []):
        if node["type"] == "solid":
            light_events.append(("solid", tuple(node["rgb"])))
        elif node["type"] in {"gradient", "pulse", "sparkle"}:
            light_events.append(("paint", [tuple(color) for color in node["palette"]]))

    return {"motion": motion_events, "light": light_events}
```

- [ ] **Step 5: Run the DSL tests to verify GREEN**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_action_dsl.py lelamp/test/test_action_dsl_compiler.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lelamp/action_dsl lelamp/test/test_action_dsl.py lelamp/test/test_action_dsl_compiler.py
git commit -m "feat(action-dsl): add constrained scene compiler"
```

### Task 5: Integrate Speaker, Memory Runtime, And Manager Snapshot

**Files:**
- Modify: `lelamp/memory/runtime.py`
- Modify: `lelamp/voice_profile.py`
- Modify: `smooth_animation.py`
- Modify: `lelamp/test/test_memory_runtime.py`
- Modify: `lelamp/test/test_qwen_realtime.py`
- Modify: `lelamp/test/test_glm_realtime.py`

- [ ] **Step 1: Write the failing integration tests**

```python
def test_voice_profile_prepends_manager_snapshot_before_personality(monkeypatch):
    monkeypatch.setattr(
        "lelamp.voice_profile.load_manager_snapshot_hint",
        lambda: "<manager>prefer warm greeting scenes</manager>",
    )
    prompt = build_agent_instructions(settings)
    assert prompt.startswith("<manager>prefer warm greeting scenes</manager>")


def test_memory_runtime_mirrors_conversation_reply_into_item_store(tmp_path, monkeypatch):
    ...
```

- [ ] **Step 2: Run the targeted integration tests to verify RED**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_memory_runtime.py lelamp/test/test_qwen_realtime.py lelamp/test/test_glm_realtime.py -q`
Expected: FAIL on missing manager snapshot loading and missing item mirroring

- [ ] **Step 3: Mirror runtime events into items**

```python
# lelamp/memory/runtime.py
def _append_item(self, item: Mapping[str, Any]) -> None:
    if self._item_store is None:
        return
    self._item_store.append(item)

def note_conversation(...):
    ...
    self._append_item(
        project_conversation_reply(
            session_id=self.session_id,
            text=assistant_text,
            ts_ms=ts_ms,
        )
    )
```

- [ ] **Step 4: Feed manager snapshot into the speaker prompt**

```python
# lelamp/voice_profile.py
def load_manager_snapshot_hint() -> str:
    path = os.getenv("LELAMP_MANAGER_SNAPSHOT_PATH")
    if not path or not os.path.exists(path):
        return ""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    summary = data.get("profile_summary") or ""
    hints = data.get("preference_hints") or []
    if not summary and not hints:
        return ""
    lines = ["<manager>"]
    if summary:
        lines.append(summary)
    for hint in hints[:6]:
        lines.append(f"- {hint}")
    lines.append("</manager>")
    return "\n".join(lines)
```

- [ ] **Step 5: Start and stop the manager runtime in `smooth_animation.py`**

```python
manager_runtime = ManagerRuntime(...)
...
ctx.add_shutdown_callback(_shutdown_callback)
atexit.register(memory_runtime.close)
atexit.register(manager_runtime.flush)
```

- [ ] **Step 6: Re-run the integration tests to verify GREEN**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_memory_runtime.py lelamp/test/test_qwen_realtime.py lelamp/test/test_glm_realtime.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add lelamp/memory/runtime.py lelamp/voice_profile.py smooth_animation.py lelamp/test/test_memory_runtime.py lelamp/test/test_qwen_realtime.py lelamp/test/test_glm_realtime.py
git commit -m "feat(manager): integrate item layer into realtime runtime"
```

### Task 6: Full Regression, Docs, And Pi Gating

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the architecture and feature flags**

```markdown
## Manager + Item Layer v1

- `LELAMP_MANAGER_PROVIDER=glm`
- `LELAMP_MANAGER_MODEL=...`
- `LELAMP_ITEM_STORE_PATH=/tmp/lelamp-items.jsonl`
- manager snapshots are derived sidecars, not source-of-truth memory
- action scenes compile into current motion recordings and RGB paint/solid execution
```

- [ ] **Step 2: Run the new focused test matrix**

Run:

```bash
PYTHONPATH=. uv run --with pytest python -m pytest \
  lelamp/test/test_items_schema.py \
  lelamp/test/test_items_store.py \
  lelamp/test/test_memory_derived.py \
  lelamp/test/test_manager_runtime.py \
  lelamp/test/test_action_dsl.py \
  lelamp/test/test_action_dsl_compiler.py \
  lelamp/test/test_memory_runtime.py \
  lelamp/test/test_qwen_realtime.py \
  lelamp/test/test_glm_realtime.py
```

Expected: PASS

- [ ] **Step 3: Run full suite before Pi rollout**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest -q lelamp/test`
Expected: PASS

- [ ] **Step 4: Commit the docs and verification**

```bash
git add README.md
git commit -m "docs: describe manager item-layer architecture"
```

- [ ] **Step 5: Push and stage Pi canary**

```bash
git push origin design/manager-item-layer-v1
```

Canary checklist:

- speaker-only mode still works when manager is disabled
- manager snapshot file updates after completed turns
- rejected action DSL plans emit `execution.guardrail_reject`
- no servo movement happens from unsupported primitives
