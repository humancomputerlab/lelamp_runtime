# LeLamp Manager + Item Layer v1 Design

Date: 2026-04-19
Status: Drafted for review
Scope: `lelamp_runtime` next-version control architecture for multi-model reasoning, derived memory management, and constrained action generation

## 1. Problem

LeLamp now works, but the control stack is still too flat.

One realtime model is doing almost everything:

- hear the user
- decide what to say
- decide whether to move or light up
- call hardware tools directly
- rely on a hard-coded expression map when it cannot decide fast enough

That is good enough for a demo path. It is not good enough for an embodied character that should accumulate memory, vary its behavior, and evolve its expression without becoming unsafe or random.

Three concrete problems show up today:

1. `lelamp/expression_engine.py` collapses most emotional expression into a fixed recording plus a fixed RGB payload.
2. `lelamp/memory/*` is intentionally file-first and stable, but it only records and summarizes. It does not yet manage higher-order preference, policy, or action priors.
3. `smooth_animation.py` wires a single speaker agent directly to tools, so there is no separate layer for slower judgment, scene planning, or action authoring.

The next version should separate fast speech from slower management and separate "what should the lamp express" from "how do servos and LEDs safely execute it".

## 2. Goals

- Keep the current low-latency voice path intact.
- Introduce an `item layer` as the canonical internal language between perception, speech, memory, and action.
- Add a slower `manager model` that can inspect turns, summarize memory, and propose behavior updates without blocking speech.
- Replace hard-coded expression mapping as the main growth path with a constrained action DSL that the agent can author safely.
- Preserve `events.jsonl` and session summaries as ground truth. Manager output should be derived state, not the source of truth.
- Allow Qwen realtime, GLM realtime, and future speaker models to share the same downstream execution and memory contract.

## 3. Non-Goals

- Do not let an LLM directly emit raw servo target arrays.
- Do not replace the existing CSV recording playback engine in v1.
- Do not make the manager model part of the latency-critical speech loop.
- Do not turn memory into a black-box vector store with no audit trail.
- Do not ship full autonomous choreography generation in the first cut.

## 4. What Exists Today

### 4.1 Realtime speaker path

- `lelamp/runtime_config.py` already supports `qwen`, `glm`, and `openai` as realtime providers.
- `lelamp/qwen_realtime.py` and `lelamp/glm_realtime.py` already normalize provider quirks into the LiveKit/OpenAI realtime session model.
- `smooth_animation.py` creates `LeLamp`, loads prompt instructions, starts `AnimationService` and `RGBService`, and exposes tools like `express`, `play_recording`, `set_rgb_solid`, `paint_rgb_pattern`, and `set_volume`.

This is the good part. Keep it.

### 4.2 Memory v0

- `lelamp/memory/writer.py` stores append-only events.
- `lelamp/memory/summary.py`, `recent_index.py`, and `reader.py` derive prompt-safe summaries and a budgeted memory header.
- `lelamp/memory/runtime.py` provides the no-throw integration seam.

This is also the right direction. Keep it as ground truth.

### 4.3 Action and light execution

- `lelamp/expression_engine.py` maps style labels to a fixed motion recording and fixed RGB cue.
- `lelamp/service/motors/animation_service.py` can interpolate from live pose into recorded sequences and startup trajectories.
- `lelamp/service/rgb/rgb_service.py` supports `solid` and `paint`.

This layer works, but it is too rigid and too low-level for the agent to grow through.

## 5. Product Synthesis, YC + A16Z + Design + Engineering

### 5.1 YC / product view

The wedge is not "more models". The wedge is "a lamp that feels like it remembers you and behaves with intent."

Users do not care whether GLM or Qwen made the choice. They care that:

- the lamp responds quickly
- the lamp does not repeat the same three gestures forever
- the lamp seems to form taste and habits over time
- the lamp stays safe and reliable on the desk

That means the next version should optimize for behavior quality and iteration speed, not model maximalism.

### 5.2 Design view

The biggest design problem is repetition.

Right now emotion is mostly "pick one recording and one color". That creates visible pattern fatigue very quickly. The lamp starts to feel like a button board instead of a character.

The design answer is not unconstrained generation. The design answer is a richer stage language:

- scene intent
- body motif
- light motif
- tempo
- restraint rules
- transition rules

That gives personality without chaos.

### 5.3 Engineering view

The current flat architecture will get worse if we simply add more prompts and more tools to the speaker model.

The system needs one layer that speaks in user time and one layer that thinks in system time:

- fast layer: speak now, react now
- slow layer: summarize, reflect, adjust priors, propose richer scenes

The bridge between them must be typed, logged, and replayable.

That is the whole game.

## 6. Candidate Architectures

### Option A: Keep one realtime model, just add more prompt rules and more tools

What it does:

- keep current voice loop
- stuff more memory and action instructions into prompt
- add more direct RGB and motion tools

Why it is tempting:

- smallest change
- fastest to demo

Why not recommended:

- still conflates speech, judgment, memory management, and action planning
- prompt bloat becomes the control plane
- hard to audit why behavior changed
- still no durable item layer

Verdict: good for a one-day hack, bad as the next stable architecture.

### Option B: Realtime speaker + manager sidecar + item layer + constrained action DSL

What it does:

- keep realtime model for low-latency voice
- add a slower manager model, likely GLM first, for memory and behavior reflection
- introduce typed `items` as the internal contract
- compile action scenes into existing motor/RGB capabilities through a safety layer

Why it is recommended:

- preserves current responsiveness
- gives memory and behavior a stable audit trail
- makes model swapping much easier
- opens the path to richer motion without direct unsafe control

Verdict: recommended.

### Option C: Full planner-led architecture, manager generates raw motion trajectories and speaker just narrates

What it does:

- move nearly all behavior and motion generation to a manager/planner
- speaker becomes a thin output layer

Why not now:

- too much latency and too much safety risk
- raw motion generation is the wrong first abstraction
- debugging this on real hardware would be a mess

Verdict: maybe later, not v1.

## 7. Recommended Architecture

### 7.1 Core principle

Keep the current realtime agent as the `speaker`. Add a `manager` that never blocks the speaker. Put a typed `item layer` between them. Route all non-trivial physical expression through a constrained `action DSL`.

```text
audio / telemetry / user input
        ↓
  perception items
        ↓
  speaker realtime agent  ───────────────→ spoken reply + immediate coarse expression
        ↓                                  ↓
  conversation items                     execution items
        ↓                                  ↓
  manager model ───────────────→ memory items + scene items + policy items
        ↓
  action DSL planner
        ↓
  validator / compiler
        ↓
  AnimationService + RGBService
```

### 7.2 Layers

#### Layer 1: Speaker

Responsibilities:

- low-latency conversational reply
- immediate, coarse emotional reaction
- explicit tool invocation only when needed
- no long-form reflection

Allowed outputs:

- `spoken_reply`
- `scene_hint`
- direct safe utility tools, if low-latency is required

Not responsible for:

- long-term memory compression
- policy updates
- authoring raw servo trajectories

#### Layer 2: Item layer

The item layer is the canonical event grammar inside the runtime.

Initial item families:

- `PerceptionItem`
  - ASR text
  - wake word / sound / interruption markers
  - telemetry snapshots
- `ConversationItem`
  - user turn committed
  - assistant reply finalized
  - tool invoke/result
- `MemoryItem`
  - episodic summary
  - profile delta
  - preference hint
  - banned behavior hint
- `SceneItem`
  - emotional intent
  - body motif
  - light motif
  - urgency / energy / restraint
- `ActionItem`
  - validated DSL scene to execute
- `ExecutionItem`
  - execution result
  - guardrail rejection
  - fallback reason

Every item must be:

- typed
- timestamped
- attributable to a producer
- serializable to disk

This matters because it lets us replay, inspect, summarize, and swap models without changing the execution contract.

#### Layer 3: Manager

The manager is a slower model, GLM-first in the initial design because you explicitly want that line explored.

Responsibilities:

- consume recent items and memory state
- write derived memory artifacts
- maintain profile and policy hints
- propose richer scene plans than the realtime speaker can safely improvise inline
- generate action DSL, not raw hardware commands

Cadence:

- after each completed turn, async
- on idle windows
- on startup recovery, if derived memory artifacts are stale

Outputs:

- `MemoryItem`s
- `SceneItem`s
- `ActionItem`s
- updated prompt snapshot for the speaker

#### Layer 4: Action DSL

The DSL is the key design move.

The manager should not invent servo values directly. It should author scenes from a constrained vocabulary.

Example primitives:

- `pose(name, hold_ms)`
- `gesture(name, intensity, repeats)`
- `look(direction, amount, duration_ms)`
- `sweep(axis, from, to, duration_ms)`
- `settle(style)`
- `light.solid(rgb, fade_ms)`
- `light.gradient(palette, duration_ms)`
- `light.pulse(palette, bpm, cycles)`
- `light.sparkle(palette, density, duration_ms)`
- `sync(body, light)`

Execution contract:

- the DSL is validated against servo bounds, velocity bounds, and known-safe motifs
- the compiler may lower a scene to:
  - existing recordings
  - transformed recordings
  - bounded interpolated pose sequences
  - RGB solid/paint sequences

This gives the agent room to design behavior without letting it freestyle dangerous hardware control.

### 7.3 Memory v1 stance

Memory should stay two-tier:

#### Ground truth

Continue writing file-first raw events:

- `events.jsonl`
- `summary.json`
- `recent_index.json`

These remain the auditable source of truth.

#### Derived memory

Add manager-produced artifacts:

- `profile.v1.json`
- `policy_hints.v1.json`
- `episodes/*.json`
- `scene_priors.v1.json`

These are disposable and rebuildable. They can be wrong without corrupting the ledger.

That split is important. If the manager hallucinates, we lose a summary. We do not lose history.

## 8. Data Contracts

### 8.1 Item envelope

Every item should look like this:

```json
{
  "schema": "lelamp.item.v1",
  "item_id": "itm_...",
  "ts_ms": 1776500000000,
  "session_id": "sess_2026-04-19_20-00-00",
  "kind": "conversation.reply",
  "producer": "speaker_realtime",
  "payload": {}
}
```

### 8.2 Manager output snapshot

The manager should publish a compact snapshot that the speaker can consume on the next turn:

```json
{
  "profile_summary": "User likes teasing banter after midnight work sessions.",
  "preference_hints": ["keep replies short", "celebrate small wins", "avoid repeated white light"],
  "scene_priors": {
    "greeting": ["warm gradient", "wake_up derived sweep"],
    "comfort": ["amber pulse", "soft tilt"]
  },
  "banned_patterns": ["repeat same scene twice in a row"],
  "updated_at_ms": 1776500000000
}
```

### 8.3 Action DSL safety contract

The compiler must reject any action scene that violates:

- servo angle bounds
- velocity / acceleration caps
- hold time caps
- unsupported primitive names
- unsupported palette size or invalid RGB ranges

Rejected plans should emit `ExecutionItem(kind="guardrail_reject")`, not fail silently.

## 9. Rollout Plan

### Phase 1: Item layer without changing behavior

- introduce item schema and storage
- mirror current conversation/tool/memory events into item form
- no behavior change yet

### Phase 2: Manager sidecar for derived memory only

- add GLM manager
- generate profile and episodic summaries
- feed compact snapshot back into speaker prompt
- no generated action scenes yet

### Phase 3: Action DSL on top of existing recordings and RGB paint

- introduce constrained scene language
- compile to recordings and RGB sequences
- keep current `express(style)` as fallback

### Phase 4: Manager-authored scene plans

- manager emits `SceneItem` and `ActionItem`
- compiler lowers them to safe motor + RGB execution
- add canary metrics for rejection rate and repeated-scene rate

## 10. Risks

### 10.1 Model disagreement

The speaker may say one thing while the manager prefers another tone.

Mitigation:

- speaker always wins the live turn
- manager updates only future-turn context and asynchronous scene proposals

### 10.2 Prompt sprawl

Manager output can become just another giant prompt blob.

Mitigation:

- force manager snapshot into a compact typed artifact with hard size caps

### 10.3 Action generation gets unsafe fast

This is the real hardware risk.

Mitigation:

- no raw trajectories from models
- DSL only
- validator before compiler
- fallback to known-safe scenes

### 10.4 Memory becomes non-auditable

If we let the manager rewrite history, we lose trust.

Mitigation:

- raw event log never becomes manager-owned
- derived artifacts are versioned sidecars only

## 11. Why This Is The Right Next Version

This design keeps what already works:

- realtime voice
- file-first memory
- existing motor and RGB executors

And it changes what is currently blocking quality:

- no more flat one-model-does-everything control plane
- no more dependence on a tiny hard-coded expression map as the growth path
- no more false choice between "fully scripted" and "fully unsafe generative motion"

It gives LeLamp a path to become more expressive and more personal without becoming fragile.

## 12. Decision

Build **Option B**:

- realtime speaker stays
- GLM-first manager sidecar is added
- item layer becomes the canonical runtime contract
- memory remains file-first at the raw layer
- action generation happens through a constrained DSL

That is the next version.
