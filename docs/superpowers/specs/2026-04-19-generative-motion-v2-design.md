## LeLamp Generative Motion v2 Design

Date: 2026-04-19
Status: Drafted for review
Scope: `lelamp_runtime` next-step architecture for agent-generated full-body motion under strict hardware guardrails

## 1. Problem

`manager-item-layer-v1` solved the first architectural split:

- the fast voice path stays realtime
- a slower manager can inspect items and propose behavior
- the runtime executes manager-authored `action.plan`

But v1 is still a scene-planning system, not a generative motion system.

Today the manager can choose from a constrained scene vocabulary and the runtime lowers that into:

- existing motion recordings
- existing RGB patterns

That is good enough for safe demos. It is not good enough for the target product behavior:

- the lamp should generate new motions, not only choose old ones
- the lamp should preserve a clear expressive intent, not flatten into repeated canned gestures
- the lamp should evolve motion taste over time
- the lamp must stay physically safe on real hardware

The core design challenge is therefore:

How do we give the agent enough expressive bandwidth to design new motions while keeping the final hardware control bounded, auditable, and safe?

## 2. Product Direction

The next version should follow a harness-engineering approach.

Do not over-constrain the agent at the expression layer.
Do not let the agent directly author raw servo trajectories.

Instead:

- let the agent design a structured motion program
- let a local compiler translate that program into safe executable motion
- let memory and critique improve the motion language over time

This design is informed by two Apple research directions:

- ELEGNT: expressive functional movement should optimize for both task utility and communicated intent, not only shortest-path movement
- EMOTION: new expressive motions can be generated when the system reasons in structured expressive dimensions instead of only replaying a fixed skill library

LeLamp v2 should absorb that lesson:

- motion is generated from expressive intent
- physical execution remains under a strict local compiler

## 3. Goals

- Preserve the current low-latency voice path.
- Move from "agent selects motion" to "agent generates motion".
- Add a typed motion program contract that supports novel whole-body expression.
- Keep physical execution local, deterministic, and auditable.
- Use the existing item layer as the canonical bridge between language, memory, critique, and motion.
- Preserve real-hardware safety with joint limits, guard bands, rate limits, and rejection paths.
- Capture execution outcomes so the manager can learn what works on this specific lamp and with this specific user.

## 4. Non-Goals

- Do not let the model emit raw servo arrays or unlimited frame sequences.
- Do not remove the current recording-based path in the first v2 cut.
- Do not make the manager part of the latency-critical speech loop.
- Do not auto-modify calibration or hardware limits online.
- Do not optimize for fully autonomous long-duration choreography in the first implementation.

## 5. Existing Facts We Must Respect

### 5.1 Servo mapping

The current mapping is consistent across docs and runtime:

- `base_yaw = 1`
- `base_pitch = 2`
- `elbow_pitch = 3`
- `wrist_roll = 4`
- `wrist_pitch = 5`

### 5.2 Mechanical limits

The `simulation/robot.xml` and `simulation/robot.urdf` files provide joint hard limits, velocity, and effort metadata for all 5 joints.

Important detail:

- simulation limits are provided in physical robot coordinates
- runtime execution today is not driven by those raw physical values

### 5.3 Runtime execution space

The live runtime currently uses the calibrated normalized follower joint space by default, not raw simulation radians.

Evidence:

- `LeLampFollowerConfig.use_degrees` defaults to `False`
- current recordings and pose presets operate in normalized calibrated motor space
- capture and replay paths already assume the local calibration is the ground truth for executable motion

This means v2 must keep two distinct representations:

1. physical reference limits from simulation
2. executable calibrated joint targets in runtime normalized space

Confusing these would create a dangerous compiler.

### 5.4 Real-hardware caution

The public control docs explicitly warn that the two yaw-like axes must not be treated as ordinary "rotate through everything" joints during calibration or testing.

So v2 must not rely on hard limits alone.
It needs stricter runtime guard bands for yaw-sensitive motion.

## 6. Recommended Architecture

### 6.1 High-level structure

```text
audio / telemetry / user input
        |
        v
speaker realtime agent
        |
        +---------------------------> spoken reply + immediate low-latency cues
        |
        v
typed items
        |
        v
manager
  |- generator
  |- critic
        |
        v
action.program
        |
        v
compiler
        |
        v
compiled keyframes / safe frame sequence
        |
        v
executor
        |
        v
hardware + execution telemetry
        |
        v
memory + future manager context
```

### 6.2 Layer responsibilities

#### Speaker

Responsibilities:

- low-latency conversation
- immediate lightweight reaction
- optional coarse `scene_hint`

Not responsible for:

- designing full-body motion programs
- memory policy
- long-horizon behavior reflection

#### Manager / Generator

Responsibilities:

- design a new motion program for the current turn
- use conversation, current pose, memory, and recent execution outcomes
- optimize for expressive intent, novelty, and character consistency

#### Manager / Critic

Responsibilities:

- reject or revise weak, repetitive, or unclear motion programs
- preserve expressive intent while improving clarity and diversity
- provide structured patches before compile-time

#### Compiler

Responsibilities:

- translate expressive motion programs into safe executable motion
- preserve the primary expressive axis when budgets are tight
- own all physical guardrails and final authority

#### Executor

Responsibilities:

- run the safe frame sequence on the actual lamp
- interrupt or fail safely if the runtime environment becomes invalid
- record what really happened

#### Memory

Responsibilities:

- store motion taste and execution learnings
- help future motion generation become more personal and less repetitive

## 7. Item Layer Additions

The item layer stays the canonical contract. v2 extends it with motion-specific kinds.

### 7.1 New item kinds

- `body.state_snapshot`
- `action.program`
- `action.critique`
- `action.compile_result`
- `execution.result`
- `execution.guardrail_reject`
- `memory.motion_taste`
- `memory.motion_learnings`

### 7.2 Body state snapshot

`body.state_snapshot` should describe the current motion context in runtime normalized joint space and expose enough information for safe planning.

Required fields:

- current pose per joint in runtime normalized space
- optional pose in degrees for debugging only
- per-direction slack summary
- near-boundary joints
- recent motion energy
- stability mode

Example:

```json
{
  "kind": "body.state_snapshot",
  "payload": {
    "pose_norm": {
      "base_yaw": -2.8,
      "base_pitch": 35.0,
      "elbow_pitch": 33.6,
      "wrist_roll": 100.0,
      "wrist_pitch": 70.8
    },
    "slack": {
      "base_pitch_up": 22.0,
      "base_pitch_down": 40.0,
      "elbow_pitch_extend": 18.0,
      "wrist_pitch_up": 26.0
    },
    "near_boundary": ["wrist_roll"],
    "recent_motion_energy": 0.18,
    "stability_mode": "normal"
  }
}
```

## 8. Motion Program Contract

### 8.1 Design principle

`action.program` is the expressive contract.

It should not contain:

- raw servo arrays
- unlimited dense frame sequences
- direct hardware register intent

It should contain:

- what the lamp is trying to express
- how strong, smooth, tense, or exaggerated it should be
- how that expression unfolds over phases
- which joints matter most in each phase

### 8.2 Canonical schema

```json
{
  "version": "v2",
  "intent": "proud_look_up",
  "why": "User asked the lamp to look up, so respond with a confident upward attention shift.",
  "expression": {
    "attention": "up",
    "attitude": "confident",
    "emotion": "playful_pride",
    "novelty": 0.72
  },
  "style": {
    "exaggeration": 0.78,
    "smoothness": 0.34,
    "tension": 0.58,
    "tempo": 1.12,
    "symmetry_break": 0.22
  },
  "settle_policy": {
    "return_to_home_bias": 0.60,
    "preserve_attention_heading": true
  },
  "phases": [
    {
      "name": "prepare",
      "duration_ms": 180,
      "easing": "ease_out",
      "joints": {
        "base_pitch": { "target": -0.16, "role": "lead" },
        "elbow_pitch": { "target": 0.08, "role": "support" },
        "wrist_pitch": { "target": -0.06, "role": "accent" }
      }
    },
    {
      "name": "accent",
      "duration_ms": 240,
      "easing": "ease_in_out",
      "joints": {
        "base_pitch": { "target": 0.66, "role": "lead" },
        "elbow_pitch": { "target": -0.22, "role": "support" },
        "wrist_pitch": { "target": 0.20, "role": "support" },
        "wrist_roll": { "target": 0.10, "role": "accent" }
      }
    },
    {
      "name": "hold",
      "duration_ms": 220,
      "easing": "linear",
      "joints": {}
    },
    {
      "name": "settle",
      "duration_ms": 320,
      "easing": "ease_out",
      "joints": {
        "base_pitch": { "target": 0.22, "role": "lead" },
        "elbow_pitch": { "target": -0.06, "role": "support" }
      }
    }
  ],
  "lighting": {
    "mode": "gradient",
    "palette": [[120, 180, 255], [255, 255, 255]]
  }
}
```

### 8.3 Meaning of `target`

`target` is an expression-space control value in `[-1.0, 1.0]`.

It is not:

- a raw servo value
- a simulation radian value
- a runtime normalized target yet

The compiler is responsible for mapping expression-space targets into executable joint targets using:

- robot profile
- live slack
- phase role
- style parameters

### 8.4 Joint roles

Each joint target in a phase must declare a role:

- `lead`
- `support`
- `accent`

This lets the compiler preserve the important expressive axis under physical constraints.

## 9. Critic Contract

The critic exists so the manager can think more, not so the system can add more hard-coded bans.

The critic should answer:

- Is the motion expressive enough?
- Is it too similar to recent motions?
- Is the main expressive axis clear?
- Is the motion likely to waste budget on weak accents?

### 9.1 Critique output

```json
{
  "decision": "revise",
  "summary": "The accent phase is expressive but too close to recent upward looks and overuses wrist motion.",
  "scores": {
    "expressivity": 0.83,
    "novelty": 0.41,
    "clarity": 0.79,
    "safety_prior": 0.74
  },
  "issues": [
    "too_similar_to_recent_look_up",
    "wrist_roll_not_needed_for_primary_intent"
  ],
  "patch": {
    "style": {
      "symmetry_break": 0.35
    },
    "phase_adjustments": [
      {
        "name": "accent",
        "joint_overrides": {
          "base_pitch": 0.72,
          "wrist_roll": 0.04
        }
      }
    ]
  }
}
```

## 10. Robot Profile

### 10.1 Purpose

`robot_profile` is the single source of truth for motion compilation.

It must unify:

- servo mapping
- simulation hard limits
- runtime calibrated execution space
- comfort zones
- protected zones
- dynamic step/rate limits
- joint semantics

### 10.2 Required representations

Each joint must have both:

1. physical reference limits
2. runtime executable limits

Suggested shape:

```json
{
  "joint_order": [
    "base_yaw",
    "base_pitch",
    "elbow_pitch",
    "wrist_roll",
    "wrist_pitch"
  ],
  "joints": {
    "base_yaw": {
      "servo_id": 1,
      "physical_hard_limit_rad": [-5.02103, 1.26215],
      "physical_hard_limit_deg": [-287.68, 72.32],
      "runtime_space": "normalized_calibrated",
      "comfort_window_norm": [-25.0, 25.0],
      "protected_window_norm": [-35.0, 30.0],
      "hard_window_norm": [-100.0, 100.0],
      "max_step_norm": 6.0,
      "max_speed_norm_s": 45.0,
      "max_accel_norm_s2": 120.0,
      "semantic_role": "attention_heading",
      "risk_tags": ["yaw_axis", "guard_band_required"]
    }
  }
}
```

The exact runtime comfort and protected windows are lamp-specific and must be checked into the profile after hardware validation.

### 10.3 Why three boundary rings

Each joint needs three rings:

- `hard`
  Absolute never-cross boundary.
- `protected`
  High-risk area that requires stronger clipping, rate limiting, or rejection.
- `comfort`
  Default expressive operating zone.

The agent should mostly feel the comfort zone.
The compiler may borrow protected-zone budget only when:

- the expressive need is strong
- live slack supports it
- the joint is not in a restricted risk mode
- the result still satisfies dynamic guardrails

### 10.4 Yaw policy

Yaw-sensitive joints must be treated differently from ordinary joints.

Rules:

- do not allow large high-frequency yaw sweeps by default
- if yaw is not the lead axis, it should stay near its current heading
- if yaw is the lead axis, apply stricter rate and jerk limits
- do not allow repeated same-direction phase accumulation toward the protected edge
- require stronger settle behavior after meaningful yaw displacement

## 11. Motion Basis

V2 should introduce a small set of body-language primitives as composable motion basis units.

Recommended initial basis set:

- `lift`
- `dip`
- `stretch`
- `curl`
- `tilt`
- `twist`
- `glance`
- `recoil`
- `hover`
- `settle`

These are not pre-made motions.
They are semantic motion building blocks the generator can combine across phases.

That gives the agent a body grammar without forcing it back into a fixed recording catalog.

## 12. Compiler Design

### 12.1 Compiler philosophy

The compiler is not just a limiter.
It is an intent-preserving translator with physical authority.

When motion budget is tight, it should try to preserve expression in this order:

1. preserve the lead joint
2. preserve the support joints if possible
3. reduce accent joints first
4. reduce overall exaggeration only after lower-value motion is exhausted
5. reject only if the result would still be unsafe or no longer semantically valid

### 12.2 Compile stages

#### Stage 1: Resolve program

Read:

- `action.program`
- latest `body.state_snapshot`
- `robot_profile`
- recent execution outcomes

Produce a resolved expressive package:

- phase list
- lead/support/accent budget
- style modifiers
- settle policy

#### Stage 2: Solve motion envelope

For each joint and direction, compute what movement budget is actually available from the current pose.

Inputs:

- live slack
- comfort and protected windows
- style exaggeration
- stability mode
- joint risk tags

#### Stage 3: Salience-preserving allocation

Allocate movement budget while preserving the expressive axis.

Priority:

- lead
- support
- accent

#### Stage 4: Temporal synthesis

Convert phases into a short keyframe program and then a bounded frame sequence.

Rules:

- keep the program short
- do not accept arbitrary long frame dumps from the model
- use easing and timing locally
- preserve prepare / accent / hold / settle structure

#### Stage 5: Safety pass

Enforce:

- max per-frame delta
- max speed
- max acceleration / jerk proxy
- yaw guard band rules
- settle requirements

#### Stage 6: Compile output

Return one of:

- `exact`
- `clipped`
- `degraded`
- `rejected`

And explain what changed.

### 12.3 Compile result contract

```json
{
  "decision": "compiled_with_clipping",
  "generated_frames": 28,
  "keyframes": [
    {
      "t_ms": 0,
      "pose_norm": {
        "base_yaw": -2.8,
        "base_pitch": 36.5,
        "elbow_pitch": 35.2,
        "wrist_roll": 96.0,
        "wrist_pitch": 68.3
      }
    }
  ],
  "preserved_intent": {
    "lead_joint": "base_pitch",
    "intent_retention": 0.87
  },
  "adjustments": [
    "reduced_wrist_roll_for_margin",
    "applied_yaw_guard_band"
  ]
}
```

## 13. Executor Design

The executor should extend the existing runtime rather than replace it.

### 13.1 New execution path

The runtime must gain a frame-sequence execution path in addition to the current recording playback path.

That path should:

- receive compiled safe frames
- stream them through the current motor service infrastructure
- support interruption and fail-safe stop
- record actual execution outcomes

### 13.2 Execution outputs

`execution.result` should include:

- action id
- total duration
- whether execution was exact, clipped, degraded, or interrupted
- max realized amplitude per joint
- final pose summary

`execution.guardrail_reject` should include:

- action id
- rejection reason
- rejected stage
- summary of the unsafe condition

## 14. Memory Extensions

Memory should not only remember the user.
It should also remember what this lamp body has learned.

### 14.1 Motion taste

`memory.motion_taste` should capture long-lived preferences such as:

- user prefers bold vs restrained motions
- user responds well to upward attention shifts
- user prefers warm vs cool lighting pairings
- user likes playful asymmetry

### 14.2 Motion learnings

`memory.motion_learnings` should capture system-side learnings such as:

- `wrist_roll` often clips under current hardware conditions
- upward looks are clearest when `base_pitch` remains the lead joint
- recent motion diversity is dropping and novelty should be boosted

These memories should influence future generation and critique.

## 15. Rollout Plan

### 15.1 V2a: Parametric Motion Foundation

Deliver:

- `robot_profile`
- `body.state_snapshot`
- `action.program`
- compile pipeline from program to safe frame sequence
- executor support for safe frame sequences
- manager integration that emits new motion programs
- compile and execution telemetry

Success criteria:

- the agent can generate new motions for the same user intent
- the motion is not limited to recording-name selection
- real hardware remains stable under guarded execution

### 15.2 V2b: Generative Style + Critic Loop

Deliver:

- full critic pass
- motion basis composition
- motion taste memory
- novelty and repetition scoring
- user-facing style controls such as exaggeration and tempo tuning

Success criteria:

- motion remains varied without becoming incoherent
- repeated prompts produce family resemblance, not identical playback

### 15.3 V2c: Embodied Taste Engine

Deliver:

- stronger long-horizon motion taste
- deeper language-to-motion coupling
- richer multi-stage expressive responses

Success criteria:

- the lamp feels like it has a body style, not just motion capability

## 16. Testing Strategy

V2a must ship with a strong safety-focused test plan.

### 16.1 Unit tests

Cover:

- schema validation
- joint mapping
- comfort/protected/hard boundary resolution
- slack computation
- phase expansion
- clip/degrade/reject decisions

### 16.2 Property and fuzz tests

Generate random valid motion programs and prove:

- compiled frames never exceed hard windows
- rate limits are respected
- rejection happens instead of unsafe execution

### 16.3 Shadow / replay tests

Run compile and execution logic without live hardware ownership to verify:

- compile outputs are stable
- telemetry is recorded correctly
- manager integration stays bounded

### 16.4 Hardware canaries

Real-hardware canaries must include:

- small-amplitude expressive motions
- edge-near-but-safe motions
- repeated look-direction requests
- interruption and recoverability tests

## 17. Risks and Mitigations

### Risk: the generator is still too conservative

Mitigation:

- allow richer phase structure
- add motion basis composition
- keep critic focused on clarity and novelty, not only safety

### Risk: the generator becomes expressive but unsafe

Mitigation:

- keep all physical execution under compiler authority
- preserve hard/protected/comfort boundaries
- reject when semantic preservation and safety cannot both be satisfied

### Risk: the system feels repetitive again

Mitigation:

- track recent motion families
- score novelty in the critic
- store motion taste and motion learnings

## 18. Final Recommendation

The next implementation step should be `V2a`.

That is the smallest version that crosses the real boundary from:

- choosing an old motion

to:

- generating a new full-body motion program

while keeping the local runtime responsible for physical truth.

This architecture keeps the best parts of the current system:

- fast voice
- typed items
- auditability
- safe execution

and opens the path to the actual target behavior:

- agent-generated motion
- richer expression
- lower repetition
- hardware-safe embodiment
