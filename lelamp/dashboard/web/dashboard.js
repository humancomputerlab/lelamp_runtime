var DashboardApp = (function () {
  var ACTION_META = {
    startupButton: {
      actionKey: "startup",
      baseClass: "action-button action-button--primary",
      label: "启动灯",
      runningLabel: "启动中",
    },
    stopButton: {
      actionKey: "stop",
      baseClass: "action-button",
      label: "回到待机",
      runningLabel: "回位中",
    },
    shutdownPoseButton: {
      actionKey: "shutdown_pose",
      baseClass: "action-button action-button--warn",
      label: "进入休息",
      runningLabel: "休息中",
    },
    lightAmberButton: {
      actionKey: "light_solid",
      baseClass: "action-button action-button--amber",
      label: "暖黄灯光",
      runningLabel: "点亮中",
    },
    lightClearButton: {
      actionKey: "light_clear",
      baseClass: "action-button",
      label: "关闭灯光",
      runningLabel: "关闭中",
    },
  };

  var runtimeMeta = {
    pollMs: 400,
    dashboardHost: "0.0.0.0",
    dashboardPort: 8765,
  };

  var _volumeDragging = false;
  var _thresholdDragging = false;

  function byId(documentRef, id) {
    return documentRef.getElementById(id);
  }

  function text(node, value) {
    if (node) {
      node.textContent = value == null ? "" : String(value);
    }
  }

  function setClassName(node, className) {
    if (node) {
      node.className = className;
    }
  }

  function translateStatus(status) {
    if (status === "ready" || status === "solid") {
      return "就绪";
    }
    if (status === "muted") {
      return "安静";
    }
    if (status === "running") {
      return "进行中";
    }
    if (status === "transition") {
      return "过渡中";
    }
    if (status === "warning") {
      return "注意";
    }
    if (status === "error") {
      return "异常";
    }
    if (status === "live") {
      return "在线";
    }
    if (status === "offline") {
      return "离线";
    }
    if (status === "off") {
      return "已关闭";
    }
    return "未知";
  }

  function translateMotionStatus(status) {
    if (status === "idle") {
      return "醒着";
    }
    if (status === "running") {
      return "动作中";
    }
    if (status === "homing") {
      return "回位中";
    }
    if (status === "error") {
      return "需要检查";
    }
    return "未连接";
  }

  function translateCalibrationState(state) {
    if (state === "ok") {
      return "校准正常";
    }
    if (state === "suspect") {
      return "校准可疑";
    }
    if (state === "missing") {
      return "缺少校准";
    }
    return "校准未知";
  }

  function translateMotorConnectivity(value) {
    if (value === true) {
      return "在线";
    }
    if (value === false) {
      return "未连接";
    }
    return "未知";
  }

  function translateVoiceState(state) {
    if (state === "idle") {
      return "待命";
    }
    if (state === "calibrating") {
      return "自校准";
    }
    if (state === "listening") {
      return "收音中";
    }
    if (state === "suppressed") {
      return "抑制中";
    }
    if (state === "committing") {
      return "送识别";
    }
    if (state === "replying") {
      return "回答中";
    }
    return "未知";
  }

  function translateAsrStatus(status) {
    if (status === "ok") {
      return "成功";
    }
    if (status === "failed") {
      return "失败";
    }
    return "未知";
  }

  function translateActionKey(actionKey) {
    if (!actionKey) {
      return "空闲";
    }
    if (actionKey === "startup") {
      return "启动灯";
    }
    if (actionKey === "play") {
      return "播放动作";
    }
    if (actionKey === "stop") {
      return "回到待机";
    }
    if (actionKey === "shutdown_pose") {
      return "进入休息";
    }
    if (actionKey === "light_solid") {
      return "暖黄灯光";
    }
    if (actionKey === "light_clear") {
      return "关闭灯光";
    }
    return String(actionKey);
  }

  function statusTone(status) {
    if (status === "ready" || status === "solid" || status === "muted") {
      return "ready";
    }
    if (status === "running" || status === "transition") {
      return "running";
    }
    if (status === "warning") {
      return "warn";
    }
    if (status === "error") {
      return "error";
    }
    if (status === "live") {
      return "live";
    }
    if (status === "offline") {
      return "offline";
    }
    return "unknown";
  }

  function formatRgb(color) {
    if (!color) {
      return "--";
    }
    return [color.red, color.green, color.blue].join(", ");
  }

  function formatLightDetail(light) {
    if (!light || !light.color) {
      return light && light.effect ? String(light.effect) : "--";
    }
    return "RGB " + formatRgb(light.color);
  }

  function formatVolume(audio) {
    if (!audio || audio.volume_percent == null) {
      return audio && audio.output_device ? String(audio.output_device) : "--";
    }
    return String(audio.volume_percent) + "%";
  }

  function formatMs(value) {
    return String(value || 0) + " 毫秒";
  }

  function formatSeconds(value) {
    return String(value || 0) + " 秒";
  }

  function formatDb(value) {
    if (value == null || value === "") {
      return "--";
    }
    return String(Math.round(Number(value) * 10) / 10) + " dB";
  }

  function trimText(value, maxLength) {
    if (value == null) {
      return "";
    }
    value = String(value);
    if (value.length <= maxLength) {
      return value;
    }
    return value.slice(0, maxLength - 1) + "…";
  }

  function renderTokens(node, items, emptyLabel) {
    if (!node) {
      return;
    }

    if (!items || !items.length) {
      node.innerHTML = '<span class="token-list__item token-list__item--empty">' + emptyLabel + "</span>";
      return;
    }

    node.innerHTML = items
      .map(function (item) {
        return '<span class="token-list__item">' + String(item) + "</span>";
      })
      .join("");
  }

  function renderErrors(node, errors) {
    if (!node) {
      return;
    }

    if (!errors || !errors.length) {
      node.innerHTML = '<div class="error-feed__item"><div class="error-feed__message">当前没有错误信息。</div></div>';
      return;
    }

    node.innerHTML = errors
      .map(function (error) {
        var severity = error.severity || "warning";
        var status = error.active ? "持续中" : "已恢复";
        var severityText = severity === "error" ? "异常" : "注意";
        return (
          '<div class="error-feed__item error-feed__item--' + severity + '">' +
          '<div class="error-feed__meta"><span>' + severityText + "</span><span>" + status + "</span></div>" +
          '<div class="error-feed__message">' + String(error.message || error.code || "未知错误") + "</div>" +
          "</div>"
        );
      })
      .join("");
  }

  function buttonClass(baseClass, state) {
    if (state === "running") {
      return baseClass + " action-button--running";
    }
    if (state === "error") {
      return baseClass + " action-button--error";
    }
    if (state === "disabled") {
      return baseClass + " action-button--disabled";
    }
    return baseClass;
  }

  function actionLabel(meta, state) {
    if (state === "running" && meta.runningLabel) {
      return meta.runningLabel;
    }
    return meta.label;
  }

  function defaultActionPayload(disableAll) {
    var payload = {
      busy: disableAll,
      actions: {},
      recordings: [],
      poll_ms: runtimeMeta.pollMs,
      config: {
        dashboard_host: runtimeMeta.dashboardHost,
        dashboard_port: runtimeMeta.dashboardPort,
        poll_ms: runtimeMeta.pollMs,
      },
    };

    Object.keys(ACTION_META).forEach(function (id) {
      var meta = ACTION_META[id];
      payload.actions[meta.actionKey] = {
        enabled: !disableAll,
        state: disableAll ? "disabled" : "enabled",
        label: meta.label,
      };
    });

    return payload;
  }

  function applyActionAvailability(documentRef, payload) {
    var actions = payload && payload.actions ? payload.actions : defaultActionPayload(true).actions;

    Object.keys(ACTION_META).forEach(function (id) {
      var node = byId(documentRef, id);
      var meta = ACTION_META[id];
      var config = actions[meta.actionKey] || {
        enabled: false,
        state: "disabled",
        label: meta.label,
      };
      if (node) {
        node.disabled = config.enabled === false;
        node.textContent = actionLabel(meta, config.state || "enabled");
        node.className = buttonClass(meta.baseClass, config.state || "enabled");
      }
    });

    // Recording buttons mirror the "play" action availability.
    var playConfig = actions["play"] || { enabled: false };
    var recordingButtons = documentRef.querySelectorAll(".recording-button");
    for (var i = 0; i < recordingButtons.length; i++) {
      recordingButtons[i].disabled = playConfig.enabled === false;
    }
  }

  function applyInterveneStatus(documentRef, intervene) {
    var statusNode = byId(documentRef, "interveneStatus");
    var stopNode = byId(documentRef, "interveneBreathStop");
    var running = !!(intervene && intervene.breath_running);
    if (statusNode) {
      statusNode.textContent = running ? "呼吸运行中。" : "呼吸未启动。";
      statusNode.className = running ? "intervene-status intervene-status--running" : "intervene-status";
    }
    if (stopNode) {
      stopNode.disabled = !running;
    }
  }

  function wireRecordingButtons(documentRef, fetchRef) {
    var buttons = documentRef.querySelectorAll(".recording-button");
    for (var i = 0; i < buttons.length; i++) {
      (function (btn) {
        var recording = btn.getAttribute("data-recording");
        if (!recording) {
          return;
        }
        btn.addEventListener("click", function () {
          if (btn.disabled) {
            return;
          }
          postJson(fetchRef, "/api/actions/play", { name: recording }).then(function () {
            return refresh(documentRef, fetchRef);
          });
        });
      })(buttons[i]);
    }
  }

  function heroCaption(motion) {
    if (motion && motion.last_result) {
      return String(motion.last_result);
    }
    if (!motion || motion.status === "unknown") {
      return "还没有拿到灯的实时状态，可能是台灯未连接。";
    }
    if (motion.status === "idle") {
      return "灯已在稳定姿态，可以继续互动或切换动作。";
    }
    if (motion.status === "running") {
      return "动作中";
    }
    if (motion.status === "homing") {
      return "灯正在慢慢回到安全姿态。";
    }
    if (motion.status === "error") {
      return "动作系统需要检查，建议看下方现场信息。";
    }
    return "状态已更新。";
  }

  function renderHardwareNotes(documentRef, motion, light, audio, voice) {
    renderTokens(
      byId(documentRef, "hardwareNotes"),
      [
        "电机 " + translateMotorConnectivity(motion.motors_connected),
        translateCalibrationState(motion.calibration_state),
        "音频输出 " + String(audio.output_device || "未知"),
        "语音 " + translateVoiceState(voice.local_state || "unknown"),
        "灯光 " + translateStatus(light.status || "unknown"),
      ],
      "还没有硬件提示。"
    );
  }

  function renderConnectivityHints(documentRef, reachableUrls) {
    var hints = [];
    if (reachableUrls && reachableUrls.length) {
      hints.push("Pi 屏幕 http://127.0.0.1:8765");
      hints = hints.concat(reachableUrls.slice(0, 2).map(function (url) {
        return "同网络设备 " + url;
      }));
    }
    renderTokens(byId(documentRef, "connectivityHints"), hints, "还没有连接建议。");
  }

  function renderConfigSnippets(documentRef, motion) {
    renderTokens(
      byId(documentRef, "configSnippets"),
      [
        "LELAMP_DASHBOARD_HOST=" + runtimeMeta.dashboardHost,
        "LELAMP_DASHBOARD_PORT=" + String(runtimeMeta.dashboardPort),
        "LELAMP_DASHBOARD_POLL_MS=" + String(runtimeMeta.pollMs),
        "LELAMP_HOME_RECORDING=" + String(motion.home_recording || "--"),
        "LELAMP_STARTUP_RECORDING=" + String(motion.startup_recording || "--"),
      ],
      "还没有配置片段。"
    );
  }

  function renderVoiceDiagnostics(documentRef, voice) {
    var items = [
      "状态 " + translateVoiceState(voice.local_state || "unknown"),
      "阈值 " + formatDb(voice.speech_threshold_db),
      "噪声底 " + formatDb(voice.noise_floor_db),
      "当前电平 " + formatDb(voice.last_level_db),
      "识别 " + translateAsrStatus(voice.last_asr_status || "unknown"),
    ];

    if (voice.last_asr_error_code) {
      items.push("错误 " + String(voice.last_asr_error_code));
    }
    if (voice.last_asr_text) {
      items.push("你说: " + trimText(voice.last_asr_text, 24));
    }
    if (voice.last_reply_text) {
      items.push("灯说: " + trimText(voice.last_reply_text, 24));
    }

    renderTokens(byId(documentRef, "voiceDiagnostics"), items, "还没有语音遥测。");
  }

  function renderState(documentRef, state) {
    var system = state.system || {};
    var motion = state.motion || {};
    var light = state.light || {};
    var audio = state.audio || {};
    var voice = state.voice || {};
    var reachable = system.reachable_urls || [];
    var connection = reachable.length ? "live" : "offline";

    text(byId(documentRef, "connectionStatus"), translateStatus(connection));
    text(byId(documentRef, "systemStatus"), translateStatus(system.status || "unknown"));
    text(byId(documentRef, "activeAction"), translateActionKey(system.active_action));
    text(byId(documentRef, "lastUpdateTopbar"), formatMs(system.last_update_ms));
    text(byId(documentRef, "lastUpdate"), formatMs(system.last_update_ms));
    text(byId(documentRef, "uptimeSeconds"), formatSeconds(system.uptime_s));

    text(byId(documentRef, "motionStatus"), translateMotionStatus(motion.status || "unknown"));
    text(byId(documentRef, "currentRecording"), motion.current_recording || "--");
    text(byId(documentRef, "lastCompletedRecording"), motion.last_completed_recording || "--");
    text(byId(documentRef, "homeRecording"), motion.home_recording || "--");
    text(byId(documentRef, "startupRecording"), motion.startup_recording || "--");
    text(byId(documentRef, "motorConnectivity"), translateMotorConnectivity(motion.motors_connected));
    text(byId(documentRef, "calibrationState"), translateCalibrationState(motion.calibration_state));

    text(byId(documentRef, "lightStatus"), translateStatus(light.status || "unknown"));
    text(byId(documentRef, "lightColor"), formatLightDetail(light));

    text(byId(documentRef, "audioStatus"), translateStatus(audio.status || "unknown"));
    text(byId(documentRef, "audioVolume"), formatVolume(audio));
    text(byId(documentRef, "voiceStatus"), translateStatus(voice.status || "unknown"));
    text(byId(documentRef, "voiceState"), translateVoiceState(voice.local_state || "unknown"));

    // Sync audio / voice controls from state
    var volumeSlider2 = byId(documentRef, "volumeSlider");
    var volumeDisplay2 = byId(documentRef, "volumeDisplay");
    if (!_volumeDragging && volumeSlider2 && audio.volume_percent != null) {
      volumeSlider2.value = audio.volume_percent;
      if (volumeDisplay2) {
        volumeDisplay2.textContent = audio.volume_percent + "%";
      }
    }
    var thresholdSlider2 = byId(documentRef, "thresholdSlider");
    var thresholdDisplay2 = byId(documentRef, "thresholdDisplay");
    if (!_thresholdDragging && thresholdSlider2 && voice.speech_threshold_db != null) {
      thresholdSlider2.value = voice.speech_threshold_db;
      if (thresholdDisplay2) {
        thresholdDisplay2.textContent = Number(voice.speech_threshold_db).toFixed(1) + " dB";
      }
    }

    setClassName(byId(documentRef, "connectionStatus"), "status-pill status-pill--" + statusTone(connection));
    setClassName(byId(documentRef, "systemStatus"), "status-pill status-pill--" + statusTone(system.status || "unknown"));

    renderConfigSnippets(documentRef, motion);
    renderVoiceDiagnostics(documentRef, voice);
    renderErrors(byId(documentRef, "errorFeed"), state.errors || []);
  }

  function pollState(fetchRef, onState) {
    return fetchRef("/api/state")
      .then(function (response) {
        return response.json();
      })
      .then(function (state) {
        onState(state);
        return state;
      })
      .catch(function () {
        var fallback = {
          system: { status: "unknown", active_action: null, last_update_ms: 0, reachable_urls: [], uptime_s: 0 },
          motion: { status: "unknown", available_recordings: [] },
          light: { status: "unknown", color: null },
          audio: { status: "unknown", output_device: null, volume_percent: null },
          voice: { status: "unknown", local_state: "unknown" },
          errors: [{ code: "ui.poll_failed", message: "状态轮询失败。", severity: "warning", active: true }],
        };
        onState(fallback);
        return fallback;
      });
  }

  function loadActions(documentRef, fetchRef) {
    return fetchRef("/api/actions")
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        runtimeMeta.pollMs = payload.poll_ms || runtimeMeta.pollMs;
        if (payload.config) {
          runtimeMeta.dashboardHost = payload.config.dashboard_host || runtimeMeta.dashboardHost;
          runtimeMeta.dashboardPort = payload.config.dashboard_port || runtimeMeta.dashboardPort;
        }
        applyActionAvailability(documentRef, payload);
        applyInterveneStatus(documentRef, payload.intervene);
        return payload;
      })
      .catch(function () {
        var fallback = defaultActionPayload(true);
        applyActionAvailability(documentRef, fallback);
        return fallback;
      });
  }

  function postJson(fetchRef, url, payload) {
    return fetchRef(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload ? JSON.stringify(payload) : "{}",
    })
      .then(function (response) {
        if (response && response.json) {
          return response.json();
        }
        return {};
      })
      .catch(function () {
        return {};
      });
  }

  function refresh(documentRef, fetchRef) {
    return loadActions(documentRef, fetchRef).then(function () {
      return pollState(fetchRef, function (state) {
        renderState(documentRef, state);
      });
    });
  }

  function wireActions(documentRef, fetchRef) {
    wireRecordingButtons(documentRef, fetchRef);

    function bind(id, handler) {
      var node = byId(documentRef, id);
      if (node && node.addEventListener) {
        node.addEventListener("click", handler);
      }
    }

    bind("startupButton", function () {
      postJson(fetchRef, "/api/actions/startup").then(function () {
        return refresh(documentRef, fetchRef);
      });
    });
    bind("stopButton", function () {
      postJson(fetchRef, "/api/actions/stop").then(function () {
        return refresh(documentRef, fetchRef);
      });
    });
    bind("shutdownPoseButton", function () {
      postJson(fetchRef, "/api/actions/shutdown_pose").then(function () {
        return refresh(documentRef, fetchRef);
      });
    });
    bind("lightAmberButton", function () {
      postJson(fetchRef, "/api/lights/solid", { red: 255, green: 178, blue: 91 }).then(function () {
        return refresh(documentRef, fetchRef);
      });
    });
    bind("lightClearButton", function () {
      postJson(fetchRef, "/api/lights/clear").then(function () {
        return refresh(documentRef, fetchRef);
      });
    });

    // Manual fallback row — DEMO_PLAN §1.2. Selector-based so we don't
    // need separate bindings per style; any button with data-intervene
    // POSTs /api/actions/intervene with that style.
    var interveneButtons = documentRef.querySelectorAll("[data-intervene]");
    if (interveneButtons && interveneButtons.length) {
      Array.prototype.forEach.call(interveneButtons, function (btn) {
        btn.addEventListener("click", function () {
          var style = btn.getAttribute("data-intervene");
          if (!style) {
            return;
          }
          postJson(fetchRef, "/api/actions/intervene", { style: style }).then(function () {
            return refresh(documentRef, fetchRef);
          });
        });
      });
    }

    bind("interveneBreathStop", function () {
      postJson(fetchRef, "/api/actions/intervene/stop").then(function () {
        return refresh(documentRef, fetchRef);
      });
    });

    // ── audio / voice controls ──

    var volumeSlider = byId(documentRef, "volumeSlider");
    var volumeDisplay = byId(documentRef, "volumeDisplay");
    if (volumeSlider && volumeDisplay) {
      volumeSlider.addEventListener("input", function () {
        _volumeDragging = true;
        volumeDisplay.textContent = volumeSlider.value + "%";
      });
      volumeSlider.addEventListener("change", function () {
        _volumeDragging = false;
        postJson(fetchRef, "/api/audio/volume", { percent: Number(volumeSlider.value) });
      });
    }

    var thresholdSlider = byId(documentRef, "thresholdSlider");
    var thresholdDisplay = byId(documentRef, "thresholdDisplay");
    if (thresholdSlider && thresholdDisplay) {
      thresholdSlider.addEventListener("input", function () {
        _thresholdDragging = true;
        thresholdDisplay.textContent = Number(thresholdSlider.value).toFixed(1) + " dB";
      });
      thresholdSlider.addEventListener("change", function () {
        _thresholdDragging = false;
        postJson(fetchRef, "/api/voice/threshold", { speech_threshold_db: Number(thresholdSlider.value) });
      });
    }

    bind("voiceCalibrateBtn", function () {
      postJson(fetchRef, "/api/voice/calibrate", { enable: true }).then(function () {
        updateAudioModeLabel(documentRef, true);
      });
    });

    var manualToggle = byId(documentRef, "voiceManualToggle");
    if (manualToggle) {
      manualToggle.addEventListener("change", function () {
        var manual = manualToggle.checked;
        postJson(fetchRef, "/api/voice/calibrate", { enable: !manual });
        updateAudioModeLabel(documentRef, !manual);
      });
    }

    function updateAudioModeLabel(documentRef, auto) {
      var label = byId(documentRef, "audioModeLabel");
      if (label) {
        label.textContent = auto ? "自动" : "手动";
        label.style.color = auto ? "var(--ready)" : "var(--warn)";
      }
    }
  }

  function start(documentRef, windowRef, fetchRef, pollMs) {
    wireActions(documentRef, fetchRef);
    return loadActions(documentRef, fetchRef).then(function (payload) {
      var effectivePollMs = payload.poll_ms || pollMs;
      return pollState(fetchRef, function (state) {
        renderState(documentRef, state);
      }).then(function () {
        windowRef.setInterval(function () {
          refresh(documentRef, fetchRef);
        }, effectivePollMs);
        return payload;
      });
    });
  }

  return {
    applyActionAvailability: applyActionAvailability,
    loadActions: loadActions,
    pollState: pollState,
    renderState: renderState,
    start: start,
  };
}());

window.addEventListener("DOMContentLoaded", function () {
  DashboardApp.start(document, window, window.fetch.bind(window), 400);
});