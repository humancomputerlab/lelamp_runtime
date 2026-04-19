# LeLamp Generative Motion V2a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-safe version of agent-generated full-body motion by introducing a typed `action.program`, a local motion compiler, a guarded frame executor, and runtime wiring that executes generated motion on real hardware without abandoning the current recording-based fallback path.

**Architecture:** Add a new `lelamp.motion_v2` package that owns robot profile facts, body-state snapshots, program validation, critique, compilation, and execution. Extend the item layer and manager runtime to emit `action.program` items, then update the memory runtime to compile and execute those programs through `AnimationService` using a new guarded frame-sequence path while keeping the existing `action.plan` scene path intact.

**Tech Stack:** Python 3.12, pytest, existing `lelamp` item store/memory runtime, `AnimationService`, calibrated follower joint space, JSON-like typed items.

---

## File Structure

### New files

- `lelamp/motion_v2/__init__.py`
  Exports the v2 motion public API.
- `lelamp/motion_v2/robot_profile.py`
  Stores the default servo map, hard limits, comfort/protected windows, and dynamic rate limits.
- `lelamp/motion_v2/body_state.py`
  Builds `body.state_snapshot` payloads from current pose plus robot profile.
- `lelamp/motion_v2/program_schema.py`
  Validates and normalizes `action.program`.
- `lelamp/motion_v2/critic.py`
  Applies the first-pass non-LLM critique/patch rules for clarity and repetition.
- `lelamp/motion_v2/compiler.py`
  Resolves programs into safe keyframes and frame sequences.
- `lelamp/motion_v2/executor.py`
  Dispatches compiled frame sequences and emits compile/execution telemetry helpers.
- `lelamp/test/test_motion_v2_robot_profile.py`
  Verifies robot profile contents and guard-band facts.
- `lelamp/test/test_motion_v2_body_state.py`
  Verifies slack and near-boundary snapshot generation.
- `lelamp/test/test_motion_v2_program_schema.py`
  Verifies validation and normalization of `action.program`.
- `lelamp/test/test_motion_v2_critic.py`
  Verifies critique patches preserve intent while trimming weak accents.
- `lelamp/test/test_motion_v2_compiler.py`
  Verifies compile decisions, lead-joint preservation, clipping, and rejection.
- `lelamp/test/test_motion_v2_executor.py`
  Verifies frame dispatch and telemetry emission.

### Modified files

- `lelamp/items/schema.py`
  Register new item kinds.
- `lelamp/items/projections.py`
  Add builders for `body.state_snapshot`, `action.program`, `action.critique`, and `action.compile_result`.
- `lelamp/manager/runtime.py`
  Emit `action.program` items in addition to the current scene path.
- `lelamp/manager/glm_manager.py`
  Replace the current scene-only heuristic output with v2 motion program output.
- `lelamp/memory/runtime.py`
  Build body-state snapshots, execute `action.program`, and write compile/execution telemetry items.
- `lelamp/service/motors/animation_service.py`
  Expose current pose and accept frame-sequence playback events.
- `lelamp/test/test_items_schema.py`
  Cover new item kinds and projection helpers.
- `lelamp/test/test_manager_runtime.py`
  Cover `action.program` emission and duplicate suppression.
- `lelamp/test/test_memory_runtime.py`
  Cover program compile/execute success and guardrail rejection.

## Task 1: Extend Item Contracts And Projection Helpers

**Files:**
- Modify: `lelamp/items/schema.py`
- Modify: `lelamp/items/projections.py`
- Modify: `lelamp/test/test_items_schema.py`

- [ ] **Step 1: Write the failing item-schema tests**

```python
from __future__ import annotations

from lelamp.items.projections import (
    project_action_compile_result,
    project_action_program,
    project_body_state_snapshot,
)
from lelamp.items.schema import ITEM_KINDS, validate_item


def test_item_schema_includes_motion_v2_kinds() -> None:
    assert "body.state_snapshot" in ITEM_KINDS
    assert "action.program" in ITEM_KINDS
    assert "action.critique" in ITEM_KINDS
    assert "action.compile_result" in ITEM_KINDS


def test_project_action_program_builds_valid_item() -> None:
    item = project_action_program(
        session_id="sess_2026-04-19_20-00-00",
        summary="Lamp performs a proud upward look.",
        program={
            "version": "v2",
            "intent": "proud_look_up",
            "phases": [],
            "style": {"exaggeration": 0.5, "smoothness": 0.5, "tension": 0.5, "tempo": 1.0, "symmetry_break": 0.0},
            "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.4},
            "settle_policy": {"return_to_home_bias": 0.5, "preserve_attention_heading": True},
            "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
        },
        source_item_id="itm_user_1",
        fingerprint="fp_motion_1",
        ts_ms=1776500000000,
    )

    validate_item(item)
    assert item["kind"] == "action.program"
    assert item["payload"]["fingerprint"] == "fp_motion_1"


def test_project_body_state_snapshot_builds_valid_item() -> None:
    item = project_body_state_snapshot(
        session_id="sess_2026-04-19_20-00-00",
        snapshot={
            "pose_norm": {"base_yaw": 0.0, "base_pitch": 1.0, "elbow_pitch": 2.0, "wrist_roll": 3.0, "wrist_pitch": 4.0},
            "slack": {"base_pitch_up": 20.0},
            "near_boundary": [],
            "recent_motion_energy": 0.1,
            "stability_mode": "normal",
        },
        ts_ms=1776500000000,
    )

    validate_item(item)
    assert item["kind"] == "body.state_snapshot"
    assert item["payload"]["stability_mode"] == "normal"


def test_project_action_compile_result_builds_valid_item() -> None:
    item = project_action_compile_result(
        session_id="sess_2026-04-19_20-00-00",
        action_item_id="itm_action_1",
        result={
            "decision": "exact",
            "generated_frames": 4,
            "adjustments": [],
            "preserved_intent": {"lead_joint": "base_pitch", "intent_retention": 1.0},
            "keyframes": [],
        },
        ts_ms=1776500000100,
    )

    validate_item(item)
    assert item["kind"] == "action.compile_result"
    assert item["payload"]["action_item_id"] == "itm_action_1"
```

- [ ] **Step 2: Run the targeted schema test file and confirm it fails**

Run: `pytest lelamp/test/test_items_schema.py -v`

Expected: FAIL with import errors for the new projection helpers and assertions that the new item kinds are missing from `ITEM_KINDS`.

- [ ] **Step 3: Add the new item kinds and projection builders**

```python
# lelamp/items/schema.py
ITEM_KINDS = frozenset(
    {
        "perception.asr_commit",
        "conversation.user_turn",
        "conversation.reply",
        "conversation.tool_invoke",
        "conversation.tool_result",
        "memory.profile_update",
        "memory.episode_summary",
        "scene.proposal",
        "action.plan",
        "body.state_snapshot",
        "action.program",
        "action.critique",
        "action.compile_result",
        "execution.result",
        "execution.guardrail_reject",
    }
)
```

```python
# lelamp/items/projections.py
def project_body_state_snapshot(*, session_id: str, snapshot: Mapping[str, Any], ts_ms: int) -> dict[str, Any]:
    return build_item(
        kind="body.state_snapshot",
        producer="action_executor",
        session_id=session_id,
        payload=_normalize_json_like(snapshot),
        ts_ms=ts_ms,
        item_id=_item_id("body_state_snapshot"),
    )


def project_action_program(
    *,
    session_id: str,
    summary: str,
    program: Mapping[str, Any],
    source_item_id: str | None,
    fingerprint: str,
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="action.program",
        producer="manager_sidecar",
        session_id=session_id,
        payload={
            "summary": summary,
            "program": _normalize_json_like(program),
            "source_item_id": source_item_id,
            "fingerprint": fingerprint,
        },
        ts_ms=ts_ms,
        item_id=_item_id("action_program"),
    )


def project_action_critique(
    *,
    session_id: str,
    action_item_id: str,
    critique: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="action.critique",
        producer="manager_sidecar",
        session_id=session_id,
        payload={
            "action_item_id": action_item_id,
            "critique": _normalize_json_like(critique),
        },
        ts_ms=ts_ms,
        item_id=_item_id("action_critique"),
    )


def project_action_compile_result(
    *,
    session_id: str,
    action_item_id: str,
    result: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="action.compile_result",
        producer="action_executor",
        session_id=session_id,
        payload={
            "action_item_id": action_item_id,
            "result": _normalize_json_like(result),
        },
        ts_ms=ts_ms,
        item_id=_item_id("action_compile_result"),
    )
```

- [ ] **Step 4: Run the schema tests again and confirm they pass**

Run: `pytest lelamp/test/test_items_schema.py -v`

Expected: PASS for the new motion-v2 item contract coverage.

- [ ] **Step 5: Commit the item-contract changes**

```bash
git add lelamp/items/schema.py lelamp/items/projections.py lelamp/test/test_items_schema.py
git commit -m "feat(items): add motion v2 item contracts"
```

## Task 2: Add Robot Profile Facts And Body-State Snapshots

**Files:**
- Create: `lelamp/motion_v2/__init__.py`
- Create: `lelamp/motion_v2/robot_profile.py`
- Create: `lelamp/motion_v2/body_state.py`
- Modify: `lelamp/service/motors/animation_service.py`
- Create: `lelamp/test/test_motion_v2_robot_profile.py`
- Create: `lelamp/test/test_motion_v2_body_state.py`

- [ ] **Step 1: Write the failing robot-profile and body-state tests**

```python
from __future__ import annotations

from lelamp.motion_v2.body_state import build_body_state_snapshot
from lelamp.motion_v2.robot_profile import load_default_robot_profile


def test_default_robot_profile_contains_expected_joint_map() -> None:
    profile = load_default_robot_profile()

    assert profile["joint_order"] == [
        "base_yaw",
        "base_pitch",
        "elbow_pitch",
        "wrist_roll",
        "wrist_pitch",
    ]
    assert profile["joints"]["base_yaw"]["servo_id"] == 1
    assert profile["joints"]["base_pitch"]["semantic_role"] == "attention_pitch"
    assert profile["joints"]["base_yaw"]["risk_tags"] == ["yaw_axis", "guard_band_required"]


def test_build_body_state_snapshot_marks_near_boundary_and_slack() -> None:
    profile = load_default_robot_profile()
    snapshot = build_body_state_snapshot(
        pose_norm={
            "base_yaw": 0.0,
            "base_pitch": 88.0,
            "elbow_pitch": 33.0,
            "wrist_roll": 99.0,
            "wrist_pitch": 71.0,
        },
        profile=profile,
        recent_motion_energy=0.42,
    )

    assert snapshot["stability_mode"] == "guarded"
    assert "base_pitch" in snapshot["near_boundary"]
    assert snapshot["slack"]["base_pitch_up"] < snapshot["slack"]["base_pitch_down"]
```

- [ ] **Step 2: Run the new motion-v2 profile tests and confirm they fail**

Run: `pytest lelamp/test/test_motion_v2_robot_profile.py lelamp/test/test_motion_v2_body_state.py -v`

Expected: FAIL because the new `lelamp.motion_v2` package and body-state helpers do not exist yet.

- [ ] **Step 3: Implement the robot profile constants and body-state builder**

```python
# lelamp/motion_v2/robot_profile.py
from __future__ import annotations

from copy import deepcopy

DEFAULT_ROBOT_PROFILE = {
    "joint_order": ["base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch"],
    "joints": {
        "base_yaw": {
            "servo_id": 1,
            "physical_hard_limit_rad": [-5.02103, 1.26215],
            "physical_hard_limit_deg": [-287.68, 72.32],
            "comfort_window_norm": [-25.0, 25.0],
            "protected_window_norm": [-35.0, 30.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 6.0,
            "max_speed_norm_s": 45.0,
            "max_accel_norm_s2": 120.0,
            "semantic_role": "attention_heading",
            "risk_tags": ["yaw_axis", "guard_band_required"],
        },
        "base_pitch": {
            "servo_id": 2,
            "physical_hard_limit_rad": [-1.08426, 2.05734],
            "physical_hard_limit_deg": [-62.12, 117.88],
            "comfort_window_norm": [-20.0, 90.0],
            "protected_window_norm": [-30.0, 98.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 7.0,
            "max_speed_norm_s": 60.0,
            "max_accel_norm_s2": 150.0,
            "semantic_role": "attention_pitch",
            "risk_tags": ["lead_axis"],
        },
        "elbow_pitch": {
            "servo_id": 3,
            "physical_hard_limit_rad": [-2.82002, 0.32157],
            "physical_hard_limit_deg": [-161.58, 18.42],
            "comfort_window_norm": [-10.0, 70.0],
            "protected_window_norm": [-20.0, 80.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 8.0,
            "max_speed_norm_s": 70.0,
            "max_accel_norm_s2": 160.0,
            "semantic_role": "reach_axis",
            "risk_tags": ["support_axis"],
        },
        "wrist_roll": {
            "servo_id": 4,
            "physical_hard_limit_rad": [-3.78654, 2.49665],
            "physical_hard_limit_deg": [-216.95, 143.05],
            "comfort_window_norm": [40.0, 100.0],
            "protected_window_norm": [20.0, 110.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 10.0,
            "max_speed_norm_s": 90.0,
            "max_accel_norm_s2": 220.0,
            "semantic_role": "accent_roll",
            "risk_tags": ["accent_axis"],
        },
        "wrist_pitch": {
            "servo_id": 5,
            "physical_hard_limit_rad": [-0.85334, 2.28826],
            "physical_hard_limit_deg": [-48.89, 131.11],
            "comfort_window_norm": [45.0, 90.0],
            "protected_window_norm": [35.0, 100.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 8.0,
            "max_speed_norm_s": 70.0,
            "max_accel_norm_s2": 170.0,
            "semantic_role": "attention_fine_pitch",
            "risk_tags": ["head_axis"],
        },
    },
}


def load_default_robot_profile() -> dict[str, object]:
    return deepcopy(DEFAULT_ROBOT_PROFILE)
```

```python
# lelamp/motion_v2/body_state.py
from __future__ import annotations

from collections.abc import Mapping


def build_body_state_snapshot(
    *,
    pose_norm: Mapping[str, float],
    profile: Mapping[str, object],
    recent_motion_energy: float = 0.0,
) -> dict[str, object]:
    joints = profile["joints"]
    slack = {}
    near_boundary = []

    for joint, current in pose_norm.items():
        cfg = joints[joint]
        comfort_low, comfort_high = cfg["comfort_window_norm"]
        slack[f"{joint}_down"] = round(float(current) - float(comfort_low), 3)
        slack[f"{joint}_up"] = round(float(comfort_high) - float(current), 3)
        if slack[f"{joint}_down"] < 10.0 or slack[f"{joint}_up"] < 10.0:
            near_boundary.append(joint)

    stability_mode = "guarded" if near_boundary else "normal"
    return {
        "pose_norm": {joint: float(value) for joint, value in pose_norm.items()},
        "slack": slack,
        "near_boundary": near_boundary,
        "recent_motion_energy": float(recent_motion_energy),
        "stability_mode": stability_mode,
    }
```

```python
# lelamp/motion_v2/__init__.py
from .body_state import build_body_state_snapshot
from .robot_profile import load_default_robot_profile

__all__ = ["build_body_state_snapshot", "load_default_robot_profile"]
```

- [ ] **Step 4: Add a public current-pose accessor on `AnimationService`**

```python
# lelamp/service/motors/animation_service.py
    def get_current_pose(self) -> Dict[str, float] | None:
        if self._current_state is not None:
            return self._current_state.copy()
        current_pose = self._read_current_pose()
        if current_pose is None:
            return None
        self._current_state = current_pose.copy()
        return current_pose
```

- [ ] **Step 5: Run the new profile and body-state tests and confirm they pass**

Run: `pytest lelamp/test/test_motion_v2_robot_profile.py lelamp/test/test_motion_v2_body_state.py -v`

Expected: PASS with one profile fact test and one snapshot/slack behavior test green.

- [ ] **Step 6: Commit the robot-profile foundation**

```bash
git add lelamp/motion_v2/__init__.py lelamp/motion_v2/robot_profile.py lelamp/motion_v2/body_state.py lelamp/service/motors/animation_service.py lelamp/test/test_motion_v2_robot_profile.py lelamp/test/test_motion_v2_body_state.py
git commit -m "feat(motion-v2): add robot profile and body state snapshot"
```

## Task 3: Add Action-Program Validation And First-Pass Critique

**Files:**
- Create: `lelamp/motion_v2/program_schema.py`
- Create: `lelamp/motion_v2/critic.py`
- Create: `lelamp/test/test_motion_v2_program_schema.py`
- Create: `lelamp/test/test_motion_v2_critic.py`

- [ ] **Step 1: Write the failing program-schema and critic tests**

```python
from __future__ import annotations

import pytest

from lelamp.motion_v2.critic import critique_program
from lelamp.motion_v2.program_schema import normalize_program, validate_program


def test_validate_program_accepts_minimal_v2_program() -> None:
    program = {
        "version": "v2",
        "intent": "proud_look_up",
        "why": "User asked lamp to look up.",
        "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.6},
        "style": {"exaggeration": 0.7, "smoothness": 0.4, "tension": 0.5, "tempo": 1.0, "symmetry_break": 0.2},
        "settle_policy": {"return_to_home_bias": 0.5, "preserve_attention_heading": True},
        "phases": [
            {
                "name": "accent",
                "duration_ms": 220,
                "easing": "ease_in_out",
                "joints": {"base_pitch": {"target": 0.7, "role": "lead"}},
            }
        ],
        "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
    }

    validate_program(program)
    normalized = normalize_program(program)
    assert normalized["phases"][0]["joints"]["base_pitch"]["role"] == "lead"


def test_validate_program_rejects_unknown_joint_role() -> None:
    with pytest.raises(ValueError, match="unknown joint role"):
        validate_program(
            {
                "version": "v2",
                "intent": "bad",
                "why": "bad",
                "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.6},
                "style": {"exaggeration": 0.7, "smoothness": 0.4, "tension": 0.5, "tempo": 1.0, "symmetry_break": 0.2},
                "settle_policy": {"return_to_home_bias": 0.5, "preserve_attention_heading": True},
                "phases": [
                    {
                        "name": "accent",
                        "duration_ms": 220,
                        "easing": "ease_in_out",
                        "joints": {"base_pitch": {"target": 0.7, "role": "main"}},
                    }
                ],
                "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
            }
        )


def test_critic_trims_small_accent_when_lead_axis_is_clear() -> None:
    critique = critique_program(
        program={
            "intent": "proud_look_up",
            "phases": [
                {
                    "name": "accent",
                    "joints": {
                        "base_pitch": {"target": 0.72, "role": "lead"},
                        "wrist_roll": {"target": 0.02, "role": "accent"},
                    },
                }
            ],
        },
        recent_fingerprints={"old_motion"},
    )

    assert critique["decision"] == "revise"
    assert critique["patch"]["phase_adjustments"][0]["joint_overrides"]["wrist_roll"] == 0.0
```

- [ ] **Step 2: Run the new schema and critic tests and confirm they fail**

Run: `pytest lelamp/test/test_motion_v2_program_schema.py lelamp/test/test_motion_v2_critic.py -v`

Expected: FAIL because validation and critique modules do not exist.

- [ ] **Step 3: Implement `program_schema.py` with normalization and strict validation**

```python
# lelamp/motion_v2/program_schema.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy

JOINT_ROLES = frozenset({"lead", "support", "accent"})
EASINGS = frozenset({"linear", "ease_in", "ease_out", "ease_in_out"})


def validate_program(program: Mapping[str, object]) -> None:
    if not isinstance(program, Mapping):
        raise ValueError("program must be a mapping")
    if program.get("version") != "v2":
        raise ValueError("program version must be 'v2'")
    if not isinstance(program.get("intent"), str) or not str(program.get("intent")).strip():
        raise ValueError("intent must be a non-empty string")

    phases = program.get("phases")
    if not isinstance(phases, Sequence) or isinstance(phases, (str, bytes)) or not phases:
        raise ValueError("phases must be a non-empty list")

    for phase in phases:
        joints = phase.get("joints", {})
        for joint_name, joint_cfg in joints.items():
            if joint_cfg.get("role") not in JOINT_ROLES:
                raise ValueError(f"unknown joint role: {joint_cfg.get('role')!r}")
            target = joint_cfg.get("target")
            if not isinstance(target, (int, float)):
                raise ValueError(f"joint target must be numeric for {joint_name}")
            if float(target) < -1.0 or float(target) > 1.0:
                raise ValueError(f"joint target must be within [-1.0, 1.0] for {joint_name}")
        if phase.get("easing") not in EASINGS:
            raise ValueError(f"unknown easing: {phase.get('easing')!r}")


def normalize_program(program: Mapping[str, object]) -> dict[str, object]:
    validate_program(program)
    normalized = deepcopy(dict(program))
    normalized["intent"] = str(normalized["intent"]).strip()
    return normalized
```

- [ ] **Step 4: Implement the first-pass rule-based critic**

```python
# lelamp/motion_v2/critic.py
from __future__ import annotations

from collections.abc import Mapping, Set
from copy import deepcopy


def critique_program(*, program: Mapping[str, object], recent_fingerprints: Set[str]) -> dict[str, object]:
    patched = deepcopy(dict(program))
    phase_adjustments = []
    issues = []

    for phase in patched.get("phases", []):
        joint_overrides = {}
        joints = phase.get("joints", {})
        lead_present = any(cfg.get("role") == "lead" for cfg in joints.values())
        for joint_name, cfg in joints.items():
            if lead_present and cfg.get("role") == "accent" and abs(float(cfg.get("target", 0.0))) < 0.05:
                joint_overrides[joint_name] = 0.0
                issues.append(f"weak accent removed: {joint_name}")
        if joint_overrides:
            phase_adjustments.append({"name": phase.get("name"), "joint_overrides": joint_overrides})

    if recent_fingerprints:
        issues.append("novelty pressure increased because recent motion history is non-empty")

    if phase_adjustments:
        return {
            "decision": "revise",
            "summary": "Trim weak accents and reserve motion budget for the lead axis.",
            "scores": {"expressivity": 0.7, "novelty": 0.5, "clarity": 0.8, "safety_prior": 0.8},
            "issues": issues,
            "patch": {"phase_adjustments": phase_adjustments},
        }

    return {
        "decision": "accept",
        "summary": "Program is clear enough for compilation.",
        "scores": {"expressivity": 0.7, "novelty": 0.5, "clarity": 0.8, "safety_prior": 0.8},
        "issues": issues,
        "patch": {"phase_adjustments": []},
    }
```

- [ ] **Step 5: Run the new program-schema and critic tests and confirm they pass**

Run: `pytest lelamp/test/test_motion_v2_program_schema.py lelamp/test/test_motion_v2_critic.py -v`

Expected: PASS for validation, normalization, and critique behavior.

- [ ] **Step 6: Commit the program-schema layer**

```bash
git add lelamp/motion_v2/program_schema.py lelamp/motion_v2/critic.py lelamp/test/test_motion_v2_program_schema.py lelamp/test/test_motion_v2_critic.py
git commit -m "feat(motion-v2): add action program schema and critic"
```

## Task 4: Build The Motion Compiler

**Files:**
- Create: `lelamp/motion_v2/compiler.py`
- Create: `lelamp/test/test_motion_v2_compiler.py`

- [ ] **Step 1: Write the failing compiler tests**

```python
from __future__ import annotations

from lelamp.motion_v2.compiler import compile_action_program
from lelamp.motion_v2.robot_profile import load_default_robot_profile


def test_compile_action_program_preserves_lead_joint_and_trims_accent() -> None:
    profile = load_default_robot_profile()
    compiled = compile_action_program(
        program={
            "version": "v2",
            "intent": "proud_look_up",
            "why": "User asked lamp to look up.",
            "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.6},
            "style": {"exaggeration": 0.8, "smoothness": 0.4, "tension": 0.5, "tempo": 1.0, "symmetry_break": 0.2},
            "settle_policy": {"return_to_home_bias": 0.5, "preserve_attention_heading": True},
            "phases": [
                {
                    "name": "accent",
                    "duration_ms": 200,
                    "easing": "ease_in_out",
                    "joints": {
                        "base_pitch": {"target": 0.9, "role": "lead"},
                        "wrist_roll": {"target": 0.03, "role": "accent"},
                    },
                }
            ],
            "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
        },
        critique={"decision": "revise", "patch": {"phase_adjustments": [{"name": "accent", "joint_overrides": {"wrist_roll": 0.0}}]}},
        snapshot={
            "pose_norm": {"base_yaw": 0.0, "base_pitch": 35.0, "elbow_pitch": 33.0, "wrist_roll": 90.0, "wrist_pitch": 70.0},
            "slack": {"base_pitch_up": 30.0, "base_pitch_down": 55.0},
            "near_boundary": [],
            "recent_motion_energy": 0.1,
            "stability_mode": "normal",
        },
        profile=profile,
    )

    assert compiled["decision"] in {"exact", "clipped"}
    assert compiled["preserved_intent"]["lead_joint"] == "base_pitch"
    assert compiled["generated_frames"] > 0


def test_compile_action_program_rejects_motion_that_exceeds_hard_window() -> None:
    profile = load_default_robot_profile()
    compiled = compile_action_program(
        program={
            "version": "v2",
            "intent": "unsafe_pitch",
            "why": "unsafe",
            "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.6},
            "style": {"exaggeration": 1.0, "smoothness": 0.4, "tension": 0.5, "tempo": 1.0, "symmetry_break": 0.2},
            "settle_policy": {"return_to_home_bias": 0.5, "preserve_attention_heading": True},
            "phases": [
                {
                    "name": "accent",
                    "duration_ms": 200,
                    "easing": "ease_in_out",
                    "joints": {"base_pitch": {"target": 1.0, "role": "lead"}},
                }
            ],
            "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
        },
        critique={"decision": "accept", "patch": {"phase_adjustments": []}},
        snapshot={
            "pose_norm": {"base_yaw": 0.0, "base_pitch": 99.0, "elbow_pitch": 33.0, "wrist_roll": 90.0, "wrist_pitch": 70.0},
            "slack": {"base_pitch_up": 1.0, "base_pitch_down": 119.0},
            "near_boundary": ["base_pitch"],
            "recent_motion_energy": 0.1,
            "stability_mode": "guarded",
        },
        profile=profile,
    )

    assert compiled["decision"] == "rejected"
    assert "base_pitch" in compiled["reason"]
```

- [ ] **Step 2: Run the compiler tests and confirm they fail**

Run: `pytest lelamp/test/test_motion_v2_compiler.py -v`

Expected: FAIL because `compile_action_program` does not exist.

- [ ] **Step 3: Implement critique patching, envelope solving, and keyframe synthesis**

```python
# lelamp/motion_v2/compiler.py
from __future__ import annotations

from copy import deepcopy

from .program_schema import normalize_program


def compile_action_program(*, program, critique, snapshot, profile):
    normalized = normalize_program(program)
    patched = _apply_critique_patch(normalized, critique or {"patch": {"phase_adjustments": []}})
    pose_norm = snapshot["pose_norm"]
    joints_cfg = profile["joints"]
    keyframes = []
    adjustments = []
    lead_joint = None

    for phase in patched["phases"]:
        frame = dict(pose_norm)
        for joint_name, joint_cfg in phase["joints"].items():
            if joint_cfg["role"] == "lead" and lead_joint is None:
                lead_joint = joint_name
            resolved = _resolve_joint_target(
                current=float(frame[joint_name]),
                target=float(joint_cfg["target"]),
                joint_cfg=joints_cfg[joint_name],
                exaggeration=float(patched["style"]["exaggeration"]),
            )
            frame[joint_name] = resolved["value"]
            adjustments.extend(resolved["adjustments"])
            if resolved["decision"] == "rejected":
                return {
                    "decision": "rejected",
                    "reason": f"{joint_name} exceeded protected hard window",
                    "generated_frames": 0,
                    "adjustments": adjustments,
                    "preserved_intent": {"lead_joint": lead_joint, "intent_retention": 0.0},
                    "keyframes": [],
                    "frames": [],
                }
        keyframes.append({"t_ms": phase["duration_ms"], "pose_norm": frame})

    frames = _expand_keyframes(keyframes)
    decision = "clipped" if adjustments else "exact"
    return {
        "decision": decision,
        "generated_frames": len(frames),
        "adjustments": adjustments,
        "preserved_intent": {"lead_joint": lead_joint, "intent_retention": 1.0 if decision == "exact" else 0.85},
        "keyframes": keyframes,
        "frames": frames,
        "lighting": patched.get("lighting"),
    }
```

- [ ] **Step 4: Implement the helper functions used by `compile_action_program`**

```python
# lelamp/motion_v2/compiler.py
def _apply_critique_patch(program: dict[str, object], critique: dict[str, object]) -> dict[str, object]:
    patched = deepcopy(program)
    for adjustment in critique.get("patch", {}).get("phase_adjustments", []):
        for phase in patched["phases"]:
            if phase["name"] != adjustment["name"]:
                continue
            for joint_name, target in adjustment.get("joint_overrides", {}).items():
                if joint_name in phase["joints"]:
                    phase["joints"][joint_name]["target"] = target
    return patched


def _resolve_joint_target(*, current: float, target: float, joint_cfg: dict[str, object], exaggeration: float) -> dict[str, object]:
    comfort_low, comfort_high = joint_cfg["comfort_window_norm"]
    protected_low, protected_high = joint_cfg["protected_window_norm"]
    desired = current + (target * exaggeration * 20.0)
    adjustments: list[str] = []
    if desired < protected_low or desired > protected_high:
        if desired < joint_cfg["hard_window_norm"][0] or desired > joint_cfg["hard_window_norm"][1]:
            return {"decision": "rejected", "value": current, "adjustments": adjustments}
        desired = max(protected_low, min(protected_high, desired))
        adjustments.append("applied_protected_window_clip")
    if desired < comfort_low or desired > comfort_high:
        adjustments.append("left_comfort_window")
    return {"decision": "accepted", "value": round(desired, 3), "adjustments": adjustments}


def _expand_keyframes(keyframes: list[dict[str, object]]) -> list[dict[str, float]]:
    return [frame["pose_norm"] for frame in keyframes]
```

- [ ] **Step 5: Run the compiler test file and confirm it passes**

Run: `pytest lelamp/test/test_motion_v2_compiler.py -v`

Expected: PASS with one preservation test and one hard-window rejection test green.

- [ ] **Step 6: Commit the compiler**

```bash
git add lelamp/motion_v2/compiler.py lelamp/test/test_motion_v2_compiler.py
git commit -m "feat(motion-v2): add guarded motion compiler"
```

## Task 5: Add Frame Execution And Memory-Runtime Wiring

**Files:**
- Create: `lelamp/motion_v2/executor.py`
- Modify: `lelamp/service/motors/animation_service.py`
- Modify: `lelamp/memory/runtime.py`
- Modify: `lelamp/test/test_memory_runtime.py`
- Create: `lelamp/test/test_motion_v2_executor.py`

- [ ] **Step 1: Write the failing executor and memory-runtime tests**

```python
from __future__ import annotations

from types import SimpleNamespace

from lelamp.motion_v2.executor import execute_compiled_program


def test_execute_compiled_program_dispatches_frames_and_lights() -> None:
    class _Service:
        def __init__(self) -> None:
            self.calls = []

        def dispatch(self, event_type: str, payload: object) -> None:
            self.calls.append((event_type, payload))

    animation = _Service()
    rgb = _Service()

    result = execute_compiled_program(
        compiled={
            "decision": "exact",
            "frames": [{"base_pitch.pos": 40.0}, {"base_pitch.pos": 48.0}],
            "lighting": {"mode": "gradient", "palette": [(120, 180, 255), (255, 255, 255)]},
        },
        animation_service=animation,
        rgb_service=rgb,
    )

    assert result["motion_frame_count"] == 2
    assert animation.calls == [("frames", [{"base_pitch.pos": 40.0}, {"base_pitch.pos": 48.0}])]
    assert rgb.calls == [("paint", [(120, 180, 255), (255, 255, 255)])]


def test_agent_memory_runtime_executes_action_program_and_records_compile_result(tmp_path) -> None:
    from lelamp.memory.runtime import AgentMemoryRuntime

    class FakeItemStore:
        def __init__(self) -> None:
            self.items = []

        def append(self, item):
            self.items.append(item)

        def iter_session_items(self, session_id):
            return [item for item in self.items if item["session_id"] == session_id]

    class FakeAnimation:
        def __init__(self) -> None:
            self.calls = []

        def dispatch(self, event_type, payload):
            self.calls.append((event_type, payload))

        def get_current_pose(self):
            return {
                "base_yaw.pos": 0.0,
                "base_pitch.pos": 35.0,
                "elbow_pitch.pos": 33.0,
                "wrist_roll.pos": 90.0,
                "wrist_pitch.pos": 70.0,
            }

    item_store = FakeItemStore()
    runtime = AgentMemoryRuntime(
        enabled=True,
        writer=SimpleNamespace(write_conversation=lambda **kwargs: None),
        session_handle=SimpleNamespace(session_id="sess_2026-04-18_12-00-00"),
        item_store=item_store,
    )
    runtime.bind_action_executor(animation_service=FakeAnimation(), rgb_service=SimpleNamespace(dispatch=lambda *args: None), get_animation_service_error=lambda: None)

    item_store.append(
        {
            "schema": "lelamp.item.v1",
            "item_id": "itm_program_1",
            "ts_ms": 1713412800500,
            "session_id": "sess_2026-04-18_12-00-00",
            "kind": "action.program",
            "producer": "manager_sidecar",
            "payload": {
                "summary": "Lamp performs a proud upward look.",
                "program": {
                    "version": "v2",
                    "intent": "proud_look_up",
                    "why": "User asked lamp to look up.",
                    "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.6},
                    "style": {"exaggeration": 0.7, "smoothness": 0.4, "tension": 0.5, "tempo": 1.0, "symmetry_break": 0.2},
                    "settle_policy": {"return_to_home_bias": 0.5, "preserve_attention_heading": True},
                    "phases": [{"name": "accent", "duration_ms": 220, "easing": "ease_in_out", "joints": {"base_pitch": {"target": 0.7, "role": "lead"}}}],
                    "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
                },
                "source_item_id": "itm_user_1",
                "fingerprint": "fp_motion_1",
            },
        }
    )

    runtime._execute_manager_action_items(item_store.items)

    assert [item["kind"] for item in item_store.items][-2:] == [
        "action.compile_result",
        "execution.result",
    ]
```

- [ ] **Step 2: Run the new executor and memory-runtime tests and confirm they fail**

Run: `pytest lelamp/test/test_motion_v2_executor.py lelamp/test/test_memory_runtime.py -k "action_program or execute_compiled_program" -v`

Expected: FAIL because the new executor does not exist and `AgentMemoryRuntime` only knows how to execute `action.plan`.

- [ ] **Step 3: Add a frame-sequence dispatch helper and teach `AnimationService` to accept `frames`**

```python
# lelamp/motion_v2/executor.py
from __future__ import annotations


def execute_compiled_program(*, compiled, animation_service=None, rgb_service=None):
    frames = list(compiled.get("frames", []))
    lighting = compiled.get("lighting")
    if animation_service is not None and frames:
        animation_service.dispatch("frames", frames)
    if rgb_service is not None and lighting:
        if lighting["mode"] == "solid":
            rgb_service.dispatch("solid", tuple(lighting["rgb"]))
        else:
            rgb_service.dispatch("paint", [tuple(color) for color in lighting["palette"]])
    return {
        "decision": compiled.get("decision", "exact"),
        "motion_frame_count": len(frames),
        "light_event_count": 1 if lighting else 0,
    }
```

```python
# lelamp/service/motors/animation_service.py
    def handle_event(self, event_type: str, payload: Any):
        if event_type == "play":
            self._handle_play(payload)
        elif event_type == "frames":
            self._handle_frames(payload)
        elif event_type == "startup":
            self._handle_startup(payload)
        else:
            print(f"Unknown event type: {event_type}")

    def _handle_frames(self, frames: list[dict[str, float]]) -> None:
        if not self.robot:
            print("Robot not connected")
            return
        self._playback_done.clear()
        self._pending_playback_completion = False
        self._current_recording = "frames"
        self._current_actions = [dict(frame) for frame in frames]
        self._current_frame_index = 0
        self._interpolation_frames = 0
        self._interpolation_target = None
```

- [ ] **Step 4: Update `AgentMemoryRuntime` to compile and execute `action.program` items**

```python
# lelamp/memory/runtime.py
from lelamp.items.projections import (
    project_action_compile_result,
    project_body_state_snapshot,
    project_execution_guardrail_reject,
    project_execution_result,
)
from lelamp.motion_v2 import build_body_state_snapshot, load_default_robot_profile
from lelamp.motion_v2.compiler import compile_action_program
from lelamp.motion_v2.critic import critique_program
from lelamp.motion_v2.executor import execute_compiled_program


    def _execute_manager_action_items(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            if item.get("kind") == "action.plan":
                self._execute_action_plan(item)
            if item.get("kind") == "action.program":
                self._execute_action_program(item)


    def _execute_action_program(self, item: dict[str, Any]) -> None:
        payload = item.get("payload") or {}
        program = payload.get("program")
        if not isinstance(program, dict):
            self._append_item(
                project_execution_guardrail_reject(
                    session_id=self.session_handle.session_id,
                    action_item_id=str(item.get("item_id") or ""),
                    reason="action program is missing a valid program payload",
                    scene={},
                    ts_ms=_ids.current_timestamp_ms(),
                )
            )
            return

        current_pose = self.animation_service.get_current_pose() if self.animation_service is not None else None
        if not current_pose:
            self._append_item(
                project_execution_guardrail_reject(
                    session_id=self.session_handle.session_id,
                    action_item_id=str(item.get("item_id") or ""),
                    reason="current pose unavailable for action program execution",
                    scene={},
                    ts_ms=_ids.current_timestamp_ms(),
                )
            )
            return

        pose_norm = {joint.removesuffix(".pos"): value for joint, value in current_pose.items()}
        profile = load_default_robot_profile()
        snapshot = build_body_state_snapshot(pose_norm=pose_norm, profile=profile, recent_motion_energy=0.0)
        self._append_item(project_body_state_snapshot(session_id=self.session_handle.session_id, snapshot=snapshot, ts_ms=_ids.current_timestamp_ms()))
        critique = critique_program(program=program, recent_fingerprints=set())
        compiled = compile_action_program(program=program, critique=critique, snapshot=snapshot, profile=profile)
        self._append_item(project_action_compile_result(session_id=self.session_handle.session_id, action_item_id=str(item.get("item_id") or ""), result=compiled, ts_ms=_ids.current_timestamp_ms()))

        if compiled["decision"] == "rejected":
            self._append_item(
                project_execution_guardrail_reject(
                    session_id=self.session_handle.session_id,
                    action_item_id=str(item.get("item_id") or ""),
                    reason=str(compiled["reason"]),
                    scene={"program": program},
                    ts_ms=_ids.current_timestamp_ms(),
                )
            )
            return

        outcome = execute_compiled_program(compiled=compiled, animation_service=self.animation_service, rgb_service=self.rgb_service)
        self._append_item(
            project_execution_result(
                session_id=self.session_handle.session_id,
                action_item_id=str(item.get("item_id") or ""),
                compiled=compiled,
                motion_event_count=outcome["motion_frame_count"],
                light_event_count=outcome["light_event_count"],
                skipped_motion=False,
                skipped_light=False,
                ts_ms=_ids.current_timestamp_ms(),
            )
        )
```

- [ ] **Step 5: Run the targeted executor and memory-runtime tests and confirm they pass**

Run: `pytest lelamp/test/test_motion_v2_executor.py lelamp/test/test_memory_runtime.py -k "action_program or execute_compiled_program" -v`

Expected: PASS for both the low-level frame dispatch test and the memory-runtime integration test.

- [ ] **Step 6: Commit the execution path**

```bash
git add lelamp/motion_v2/executor.py lelamp/service/motors/animation_service.py lelamp/memory/runtime.py lelamp/test/test_motion_v2_executor.py lelamp/test/test_memory_runtime.py
git commit -m "feat(motion-v2): execute compiled motion programs"
```

## Task 6: Upgrade The Manager To Emit `action.program`

**Files:**
- Modify: `lelamp/manager/glm_manager.py`
- Modify: `lelamp/manager/runtime.py`
- Modify: `lelamp/test/test_manager_runtime.py`

- [ ] **Step 1: Write the failing manager tests for action-program emission**

```python
from __future__ import annotations

from types import SimpleNamespace

from lelamp.items.projections import project_conversation_user_turn
from lelamp.manager.glm_manager import GLMManager
from lelamp.manager.runtime import ManagerRuntime


def test_glm_manager_builds_action_program_for_upward_look_request() -> None:
    manager = GLMManager(settings=SimpleNamespace())

    snapshot = manager.process(
        items=[{"kind": "conversation.user_turn", "item_id": "itm_user_1", "payload": {"text": "抬头看看我"}}],
        previous_snapshot=None,
    )

    assert snapshot["_action_program"]["summary"] == "User requested a confident upward attention shift."
    assert snapshot["_action_program"]["program"]["intent"] == "proud_look_up"
    assert snapshot["_action_program"]["program"]["phases"][0]["joints"]["base_pitch"]["role"] == "lead"


def test_manager_runtime_emits_action_program_item_for_manager_output(tmp_path) -> None:
    session_id = "sess_2026-04-19_20-00-00"
    user_turn = project_conversation_user_turn(session_id=session_id, text="抬头看看我", ts_ms=1776500000000)

    class FakeManager:
        def process(self, *, items, previous_snapshot):
            return {
                "profile_summary": "Recent user theme: 抬头看看我",
                "preference_hints": [],
                "scene_priors": {"look_up": ["cool gradient"]},
                "banned_patterns": [],
                "updated_at_ms": 1776500000100,
                "_action_program": {
                    "summary": "User requested a confident upward attention shift.",
                    "program": {
                        "version": "v2",
                        "intent": "proud_look_up",
                        "why": "User asked lamp to look up.",
                        "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.6},
                        "style": {"exaggeration": 0.7, "smoothness": 0.4, "tension": 0.5, "tempo": 1.0, "symmetry_break": 0.2},
                        "settle_policy": {"return_to_home_bias": 0.5, "preserve_attention_heading": True},
                        "phases": [{"name": "accent", "duration_ms": 220, "easing": "ease_in_out", "joints": {"base_pitch": {"target": 0.7, "role": "lead"}}}],
                        "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
                    },
                    "source_item_id": user_turn["item_id"],
                },
            }

    runtime = ManagerRuntime(manager=FakeManager(), item_store_path=tmp_path / "items.jsonl", derived_root=tmp_path / "memory")
    runtime.process_once(session_id=session_id, items=[user_turn])

    emitted = list(runtime._item_store.iter_session_items(session_id))
    assert [item["kind"] for item in emitted] == ["action.program"]
    assert emitted[0]["payload"]["program"]["intent"] == "proud_look_up"
```

- [ ] **Step 2: Run the manager tests and confirm they fail**

Run: `pytest lelamp/test/test_manager_runtime.py -k "action_program or upward_look_request" -v`

Expected: FAIL because the manager still only emits `_scene_proposal` and the runtime only writes `scene.proposal` / `action.plan`.

- [ ] **Step 3: Replace the scene-only heuristic in `GLMManager` with an action-program heuristic**

```python
# lelamp/manager/glm_manager.py
def _action_program_for_text(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if _contains_any(lowered, text, "抬头", "仰头", "look up", "往上看", "向上看"):
        return {
            "summary": "User requested a confident upward attention shift.",
            "program": {
                "version": "v2",
                "intent": "proud_look_up",
                "why": "User asked the lamp to look up.",
                "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.62},
                "style": {"exaggeration": 0.74, "smoothness": 0.38, "tension": 0.52, "tempo": 1.0, "symmetry_break": 0.18},
                "settle_policy": {"return_to_home_bias": 0.55, "preserve_attention_heading": True},
                "phases": [
                    {
                        "name": "prepare",
                        "duration_ms": 140,
                        "easing": "ease_out",
                        "joints": {
                            "base_pitch": {"target": -0.12, "role": "lead"},
                            "elbow_pitch": {"target": 0.06, "role": "support"},
                        },
                    },
                    {
                        "name": "accent",
                        "duration_ms": 220,
                        "easing": "ease_in_out",
                        "joints": {
                            "base_pitch": {"target": 0.70, "role": "lead"},
                            "wrist_pitch": {"target": 0.14, "role": "support"},
                            "wrist_roll": {"target": 0.06, "role": "accent"},
                        },
                    },
                ],
                "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
            },
        }
    return None


    action_program = _action_program_for_text(text)
    if action_program is not None:
        result["_action_program"] = {
            "summary": action_program["summary"],
            "program": action_program["program"],
            "source_item_id": (last_user_turn or {}).get("item_id"),
        }
```

- [ ] **Step 4: Teach `ManagerRuntime` to emit deduplicated `action.program` items**

```python
# lelamp/manager/runtime.py
from lelamp.items.projections import project_action_program, project_scene_proposal, project_action_plan


    def _emit_scene_plan_items(self, *, session_id: str, items: list[dict[str, Any]], raw_output: dict[str, Any]) -> None:
        action_program = raw_output.get("_action_program")
        if isinstance(action_program, dict):
            self._emit_action_program_item(session_id=session_id, items=items, proposal=action_program)

        proposal = raw_output.get("_scene_proposal")
        if not isinstance(proposal, dict):
            return
        scene = proposal.get("scene")
        if not isinstance(scene, dict):
            return

        summary = str(proposal.get("summary") or "").strip() or "Manager proposed a new scene."
        source_item_id = proposal.get("source_item_id")
        if not isinstance(source_item_id, str) or not source_item_id:
            latest_user_turn = _latest_user_turn(items)
            candidate = (latest_user_turn or {}).get("item_id")
            source_item_id = candidate if isinstance(candidate, str) and candidate else None

        fingerprint = _scene_fingerprint(scene)
        if self._has_existing_generated_action(
            session_id=session_id,
            source_item_id=source_item_id,
            fingerprint=fingerprint,
        ):
            return

        ts_ms = _proposal_ts_ms(proposal, items)
        scene_item = project_scene_proposal(
            session_id=session_id,
            summary=summary,
            scene=scene,
            source_item_id=source_item_id,
            fingerprint=fingerprint,
            ts_ms=ts_ms,
        )
        action_item = project_action_plan(
            session_id=session_id,
            summary=summary,
            scene=scene,
            source_item_id=source_item_id,
            scene_item_id=scene_item["item_id"],
            fingerprint=fingerprint,
            ts_ms=ts_ms,
        )
        self._item_store.append(scene_item)
        self._item_store.append(action_item)

    def _emit_action_program_item(self, *, session_id: str, items: list[dict[str, Any]], proposal: dict[str, Any]) -> None:
        program = proposal.get("program")
        if not isinstance(program, dict):
            return
        summary = str(proposal.get("summary") or "").strip() or "Manager proposed a motion program."
        source_item_id = proposal.get("source_item_id")
        fingerprint = _scene_fingerprint(program)
        if self._has_existing_generated_action(session_id=session_id, source_item_id=source_item_id, fingerprint=fingerprint):
            return
        ts_ms = _proposal_ts_ms(proposal, items)
        action_item = project_action_program(
            session_id=session_id,
            summary=summary,
            program=program,
            source_item_id=source_item_id,
            fingerprint=fingerprint,
            ts_ms=ts_ms,
        )
        self._item_store.append(action_item)

    def _has_existing_generated_action(
        self,
        *,
        session_id: str,
        source_item_id: str | None,
        fingerprint: str,
    ) -> bool:
        for item in self._item_store.iter_session_items(session_id):
            if item.get("kind") not in {"action.plan", "action.program"}:
                continue
            payload = item.get("payload") or {}
            if source_item_id and payload.get("source_item_id") == source_item_id:
                return True
            if payload.get("fingerprint") == fingerprint:
                return True
        return False
```

- [ ] **Step 5: Run the manager test file and confirm the new action-program tests pass**

Run: `pytest lelamp/test/test_manager_runtime.py -v`

Expected: PASS for the new `action.program` coverage while keeping the existing scene-path tests green.

- [ ] **Step 6: Commit the manager upgrade**

```bash
git add lelamp/manager/glm_manager.py lelamp/manager/runtime.py lelamp/test/test_manager_runtime.py
git commit -m "feat(manager): emit motion v2 action programs"
```

## Task 7: Full Verification And Hardware Canary

**Files:**
- Modify: `lelamp/test/test_memory_runtime.py`
- Modify: `lelamp/test/test_manager_runtime.py`

- [ ] **Step 1: Run the focused motion-v2 suite**

Run:

```bash
pytest \
  lelamp/test/test_items_schema.py \
  lelamp/test/test_motion_v2_robot_profile.py \
  lelamp/test/test_motion_v2_body_state.py \
  lelamp/test/test_motion_v2_program_schema.py \
  lelamp/test/test_motion_v2_critic.py \
  lelamp/test/test_motion_v2_compiler.py \
  lelamp/test/test_motion_v2_executor.py \
  lelamp/test/test_manager_runtime.py \
  lelamp/test/test_memory_runtime.py -v
```

Expected: PASS for the new v2-focused test surface with no regressions in existing manager/runtime tests.

- [ ] **Step 2: Run the full Python test suite**

Run: `pytest lelamp/test -v`

Expected: PASS with the existing suite still green. If there are unrelated hardware-skipped tests, they should remain skipped rather than newly fail.

- [ ] **Step 3: Run a local shadow smoke test**

Run:

```bash
uv run python - <<'PY'
from lelamp.motion_v2.compiler import compile_action_program
from lelamp.motion_v2.critic import critique_program
from lelamp.motion_v2.body_state import build_body_state_snapshot
from lelamp.motion_v2.robot_profile import load_default_robot_profile

profile = load_default_robot_profile()
snapshot = build_body_state_snapshot(
    pose_norm={"base_yaw": 0.0, "base_pitch": 35.0, "elbow_pitch": 33.0, "wrist_roll": 90.0, "wrist_pitch": 70.0},
    profile=profile,
    recent_motion_energy=0.0,
)
program = {
    "version": "v2",
    "intent": "proud_look_up",
    "why": "shadow smoke",
    "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.6},
    "style": {"exaggeration": 0.7, "smoothness": 0.4, "tension": 0.5, "tempo": 1.0, "symmetry_break": 0.2},
    "settle_policy": {"return_to_home_bias": 0.5, "preserve_attention_heading": True},
    "phases": [{"name": "accent", "duration_ms": 220, "easing": "ease_in_out", "joints": {"base_pitch": {"target": 0.7, "role": "lead"}}}],
    "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
}
critique = critique_program(program=program, recent_fingerprints=set())
compiled = compile_action_program(program=program, critique=critique, snapshot=snapshot, profile=profile)
print(compiled["decision"], compiled["generated_frames"], compiled["preserved_intent"]["lead_joint"])
PY
```

Expected output: `exact 1 base_pitch` or `clipped 1 base_pitch`

- [ ] **Step 4: Run a Pi hardware canary with a tiny upward-look motion**

Run on the Pi runtime checkout:

```bash
uv run python - <<'PY'
from lelamp.motion_v2.body_state import build_body_state_snapshot
from lelamp.motion_v2.compiler import compile_action_program
from lelamp.motion_v2.critic import critique_program
from lelamp.motion_v2.executor import execute_compiled_program
from lelamp.motion_v2.robot_profile import load_default_robot_profile
from lelamp.remote_control import _build_animation_service, _build_rgb_service
from types import SimpleNamespace

args = SimpleNamespace(
    port="/dev/ttyACM0",
    id="lelamp",
    fps=30,
    duration=0.6,
    idle_recording="home_safe",
    home_recording="home_safe",
    use_home_pose_relative=True,
)

animation = _build_animation_service(args)
rgb = _build_rgb_service(args)
animation.start()
rgb.start()
try:
    pose = animation.get_current_pose()
    assert pose is not None, "current pose unavailable"
    profile = load_default_robot_profile()
    snapshot = build_body_state_snapshot(
        pose_norm={joint.removesuffix(".pos"): value for joint, value in pose.items()},
        profile=profile,
        recent_motion_energy=0.0,
    )
    program = {
        "version": "v2",
        "intent": "micro_look_up",
        "why": "hardware canary",
        "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.4},
        "style": {"exaggeration": 0.25, "smoothness": 0.5, "tension": 0.3, "tempo": 0.8, "symmetry_break": 0.0},
        "settle_policy": {"return_to_home_bias": 0.7, "preserve_attention_heading": True},
        "phases": [{"name": "accent", "duration_ms": 180, "easing": "ease_in_out", "joints": {"base_pitch": {"target": 0.25, "role": "lead"}}}],
        "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
    }
    critique = critique_program(program=program, recent_fingerprints=set())
    compiled = compile_action_program(program=program, critique=critique, snapshot=snapshot, profile=profile)
    assert compiled["decision"] != "rejected", compiled
    print(execute_compiled_program(compiled=compiled, animation_service=animation, rgb_service=rgb))
finally:
    animation.stop()
    rgb.stop()
PY
```

Expected output: a small executed motion with a returned dict containing `motion_frame_count` greater than zero and no runtime exceptions.

- [ ] **Step 5: Record the exact green verification commands in the work log**

```text
Focused suite:
pytest lelamp/test/test_items_schema.py lelamp/test/test_motion_v2_robot_profile.py lelamp/test/test_motion_v2_body_state.py lelamp/test/test_motion_v2_program_schema.py lelamp/test/test_motion_v2_critic.py lelamp/test/test_motion_v2_compiler.py lelamp/test/test_motion_v2_executor.py lelamp/test/test_manager_runtime.py lelamp/test/test_memory_runtime.py -v

Full suite:
pytest lelamp/test -v

Hardware canary:
uv run python - <<'PY'
from lelamp.motion_v2.body_state import build_body_state_snapshot
from lelamp.motion_v2.compiler import compile_action_program
from lelamp.motion_v2.critic import critique_program
from lelamp.motion_v2.executor import execute_compiled_program
from lelamp.motion_v2.robot_profile import load_default_robot_profile
from lelamp.remote_control import _build_animation_service, _build_rgb_service
from types import SimpleNamespace

args = SimpleNamespace(
    port="/dev/ttyACM0",
    id="lelamp",
    fps=30,
    duration=0.6,
    idle_recording="home_safe",
    home_recording="home_safe",
    use_home_pose_relative=True,
)

animation = _build_animation_service(args)
rgb = _build_rgb_service(args)
animation.start()
rgb.start()
try:
    pose = animation.get_current_pose()
    assert pose is not None, "current pose unavailable"
    profile = load_default_robot_profile()
    snapshot = build_body_state_snapshot(
        pose_norm={joint.removesuffix(".pos"): value for joint, value in pose.items()},
        profile=profile,
        recent_motion_energy=0.0,
    )
    program = {
        "version": "v2",
        "intent": "micro_look_up",
        "why": "hardware canary",
        "expression": {"attention": "up", "attitude": "confident", "emotion": "warm", "novelty": 0.4},
        "style": {"exaggeration": 0.25, "smoothness": 0.5, "tension": 0.3, "tempo": 0.8, "symmetry_break": 0.0},
        "settle_policy": {"return_to_home_bias": 0.7, "preserve_attention_heading": True},
        "phases": [{"name": "accent", "duration_ms": 180, "easing": "ease_in_out", "joints": {"base_pitch": {"target": 0.25, "role": "lead"}}}],
        "lighting": {"mode": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]},
    }
    critique = critique_program(program=program, recent_fingerprints=set())
    compiled = compile_action_program(program=program, critique=critique, snapshot=snapshot, profile=profile)
    assert compiled["decision"] != "rejected", compiled
    print(execute_compiled_program(compiled=compiled, animation_service=animation, rgb_service=rgb))
finally:
    animation.stop()
    rgb.stop()
PY
```
