// State -------------------------------------------------------------------
const state = {
  videoId: null,
  analysisId: null,
  duration: 0,
  segments: [], // {start_s, end_s, is_on}
  media: [],
  analyses: [],
  pollingAnalyses: false,
  analysis: null,
  poseData: null,
  knobs: null,
  defaultKnobs: null,
  expandedSegments: new Set(),
  videoUrl: null,
  playMode: "free", // "free" | "segment" | "all_on"
  segmentEndS: null, // when in segment/all_on mode, auto-pause/skip at this time
  allOnQueue: [], // remaining ON segments to play through in "all_on" mode
  calibrationVideoId: null,
  calibrationPoints: [],
  ballTrajectory: [],
  ballAnalysisId: null,
  ballVideoPath: null, // Path to ball trajectory MP4 video
};

// Per-detector config schemas — single source of truth for which knobs/badges
// belong to which detector.
const _MEDIAN_FRAME_KEYS = [
  "sample_fps", "median_bg_samples",
  "diff_threshold", "motion_threshold",
  "merge_gap_s", "enable_merge_gap",
  "min_segment_s", "enable_min_segment",
  "segment_padding_s", "enable_padding",
  "range_start_s", "range_end_s",
  "duration_s", "target_width", "target_height", "sample_count",
  "detector", "detector_version",
];
const DETECTOR_CONFIGS = {
  median_frame: _MEDIAN_FRAME_KEYS,
  median_court_roi: [
    ..._MEDIAN_FRAME_KEYS,
    "court_weight", "outside_weight", "near_camera_weight",
  ],
  pose_skeleton_yolo: [
    "sample_fps",
    "pose_model", "pose_conf", "pose_imgsz", "model_name",
    "range_start_s", "range_end_s",
    "duration_s", "sample_count",
    "detector", "detector_version",
  ],
  ball_tracking_yolo: [
    "sample_fps",
    "range_start_s", "range_end_s",
    "duration_s", "sample_count",
    "detector", "detector_version",
  ],
};

const HELP_TEXT = {
  sample_fps: "Frames per second sampled for detection. Higher = finer time resolution but slower.",
  median_bg_samples: "Number of frames spread across the video to build the median background. More = cleaner empty-court estimate but slower phase 1.",
  diff_threshold: "Per-pixel intensity difference (0–255) above which a pixel counts as foreground when compared to the median background.",
  motion_threshold: "Fraction of foreground pixels (0–1) at or above which a sample is classified as in-play.",
  merge_gap_s: "Two in-play segments closer than this many seconds get merged. Bridges short gaps inside a real rally.",
  enable_merge_gap: "Toggles segment merging. Disable to keep raw detector output.",
  min_segment_s: "Segments shorter than this many seconds are discarded as transient noise.",
  enable_min_segment: "Toggles short-segment filtering.",
  segment_padding_s: "Seconds of padding added at each end of every segment so cuts don't clip a swing.",
  enable_padding: "Toggles segment padding.",
  court_weight: "Weight applied to foreground pixels inside the marked court polygon (median_court_roi only).",
  outside_weight: "Weight applied to foreground pixels outside the court polygon (median_court_roi only). Lower de-emphasizes background distractions.",
  near_camera_weight: "Weight applied to the near-camera band (median_court_roi only). Set below 1.0 to suppress close-up walking false positives; 0 disables that band entirely.",
  pose_model: "Ultralytics pose model weights file. yolo11n-pose.pt is fastest; larger variants are more accurate but slower.",
  pose_conf: "Minimum YOLO person-detection confidence (0–1). Lower picks up more small/distant players but increases false positives.",
  pose_imgsz: "YOLO inference image size in pixels. Higher helps small/far players but is slower per frame.",
  range_start_s: "Start time within the source video for this analysis run. Use a sub-range when iterating on a slow detector.",
  range_end_s: "End time within the source video for this analysis run.",
  detector: "Detector that produced this analysis.",
  detector_version: "Schema version of the detector's output artifact.",
  duration_s: "Source video duration in seconds.",
  target_width: "Width in pixels at which frames were processed (downscaled for speed).",
  target_height: "Height in pixels at which frames were processed.",
  sample_count: "Total number of frames sampled by the detector.",
  model_name: "Filename of the pose model that was loaded.",
};

// DOM helpers -------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const SECTIONS = ["library-section", "analyses-section", "upload-section", "status-section", "editor-section", "calibration-section"];
function showOnly(...ids) {
  for (const sec of SECTIONS) {
    $(sec).hidden = !ids.includes(sec);
  }
}
function fmt(t) {
  if (!isFinite(t)) return "0:00";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
function fmtPrecise(t) {
  if (!isFinite(t)) return "0:00.0";
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(1);
  return `${m}:${s.padStart(4, "0")}`;
}

// Library -----------------------------------------------------------------
async function refreshLibrary() {
  const [media, analyses] = await Promise.all([
    fetch("/media").then((r) => r.json()),
    fetch("/analyses").then((r) => r.json()),
  ]);
  state.media = media;
  state.analyses = analyses;
  if (!media.length) {
    $("library-section").hidden = true;
    $("analyses-section").hidden = true;
    showOnly("upload-section");
    return media;
  }
  renderLibrary(media);
  renderAnalyses(analyses);
  showOnly("library-section", "analyses-section");
  if (analyses.some((a) => a.status === "pending" || a.status === "analyzing")) {
    pollAnalyses();
  }
  return media;
}

function renderLibrary(videos) {
  const tbody = $("videos-tbody");
  tbody.innerHTML = "";
  for (const v of videos) {
    const tr = document.createElement("tr");

    const fileTd = document.createElement("td");
    fileTd.textContent = v.filename;

    const durTd = document.createElement("td");
    durTd.textContent = v.duration_s ? fmt(v.duration_s) : "—";

    const countTd = document.createElement("td");
    countTd.textContent = `${v.analysis_count || 0}`;

    const actTd = document.createElement("td");
    const analyze = document.createElement("button");
    analyze.className = "primary";
    analyze.textContent = "Start analysis";
    analyze.addEventListener("click", () => startAnalysis(v.video_id));
    actTd.appendChild(analyze);
    const calibrate = document.createElement("button");
    calibrate.textContent = v.has_court_calibration ? "Edit court" : "Mark court";
    calibrate.style.marginLeft = "6px";
    calibrate.addEventListener("click", () => openCalibration(v));
    actTd.appendChild(calibrate);
    const del = document.createElement("button");
    del.textContent = "✕";
    del.title = "Delete";
    del.style.marginLeft = "6px";
    del.addEventListener("click", () => deleteVideo(v));
    actTd.appendChild(del);

    tr.append(fileTd, durTd, countTd, actTd);
    tbody.appendChild(tr);
  }
  updateRangeDefaults();
}

function renderAnalyses(analyses) {
  const tbody = $("analyses-tbody");
  tbody.innerHTML = "";
  $("analyses-section").hidden = !analyses.length;
  for (const a of analyses) {
    const tr = document.createElement("tr");
    const fileTd = document.createElement("td");
    fileTd.textContent = a.filename;
    const algoTd = document.createElement("td");
    algoTd.textContent = a.algorithm;
    const statusTd = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge badge-" + a.status;
    badge.textContent = a.status;
    statusTd.appendChild(badge);
    if (a.error_msg) {
      const err = document.createElement("div");
      err.className = "muted";
      err.textContent = a.error_msg;
      statusTd.appendChild(err);
    }
    const progressTd = document.createElement("td");
    if (a.status === "analyzing" || a.status === "pending") {
      const p = document.createElement("progress");
      p.className = "mini-progress";
      p.value = a.progress_percent || 0;
      p.max = 100;
      progressTd.appendChild(p);
      const msg = document.createElement("div");
      msg.className = "muted";
      const eta = typeof a.progress_eta_s === "number" ? ` • ETA ${fmtEta(a.progress_eta_s)}` : "";
      msg.textContent = (a.progress_message || `${Math.round(a.progress_percent || 0)}%`) + eta;
      progressTd.appendChild(msg);
    } else {
      progressTd.textContent = a.status === "done" ? "100%" : "—";
    }
    const actTd = document.createElement("td");
    if (a.status === "done") {
      const open = document.createElement("button");
      open.className = "primary";
      open.textContent = "Open";
      open.addEventListener("click", () => openAnalysis(a));
      actTd.appendChild(open);
    }
    if (a.status === "error") {
      const del = document.createElement("button");
      del.textContent = "Delete";
      del.addEventListener("click", () => deleteAnalysis(a));
      actTd.appendChild(del);
    }
    if (a.status === "pending" || a.status === "analyzing") {
      const cancel = document.createElement("button");
      cancel.textContent = "Cancel";
      cancel.style.marginLeft = "6px";
      cancel.addEventListener("click", () => deleteAnalysis(a));
      actTd.appendChild(cancel);
    }
    const range = document.createElement("div");
    range.className = "muted";
    if (typeof a.range_start_s === "number" && typeof a.range_end_s === "number") {
      range.textContent = `${fmtPrecise(a.range_start_s)}-${fmtPrecise(a.range_end_s)}`;
      progressTd.appendChild(range);
    }
    tr.append(fileTd, algoTd, statusTd, progressTd, actTd);
    tbody.appendChild(tr);
  }
}

async function deleteAnalysis(a) {
  const action = a.status === "pending" || a.status === "analyzing" ? "Cancel" : "Delete";
  if (!confirm(`${action} analysis "${a.algorithm}" for "${a.filename}"?`)) return;
  const r = await fetch(`/analyses/${a.analysis_id}`, { method: "DELETE" });
  if (!r.ok) {
    alert("Could not delete analysis: " + (await r.text()));
    return;
  }
  await refreshLibrary();
}

function fmtEta(seconds) {
  if (!isFinite(seconds)) return "unknown";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = seconds / 60;
  if (mins < 60) return `${mins.toFixed(1)}m`;
  return `${(mins / 60).toFixed(1)}h`;
}

function updateRangeDefaults() {
  if (!state.media.length) return;
  const duration = Math.max(...state.media.map((m) => m.duration_s || 0));
  for (const id of ["start-range-start-slider", "start-range-end-slider"]) {
    const el = $(id);
    el.max = duration;
  }
  if (!$("start-range-end").dataset.touched) {
    $("start-range-end").value = fmtPrecise(duration);
    $("start-range-end-slider").value = duration;
  }
}

async function openVideo(v) {
  state.videoId = v.video_id;
  state.duration = v.duration_s || 0;
  if (v.status === "done") {
    await loadEditor();
  } else if (v.status === "analyzing") {
    showOnly("status-section");
    pollStatus();
  } else {
    await reanalyzeVideo(v.video_id);
  }
}

async function reanalyzeVideo(videoId) {
  state.videoId = videoId;
  await fetch(`/process/${videoId}`, { method: "POST" });
  showOnly("status-section");
  pollStatus();
}

async function startAnalysis(videoId) {
  await startBallTrajectoryAnalysis(videoId);
}

async function startBallTrajectoryAnalysis(videoId) {
  const media = state.media.find((m) => m.video_id === videoId);
  const rangeStart = parseTime($("start-range-start").value) ?? 0;
  const rangeEnd = parseTime($("start-range-end").value) ?? media.duration_s;

  // 读取勾选框状态
  const renderOptions = {
    ball_trail: $("opt-ball-trail").checked,
    ball_speed: $("opt-ball-speed").checked,
    running_speed: $("opt-running-speed").checked,
    skeleton: $("opt-skeleton").checked,
    spark: $("opt-spark").checked,
  };

  try {
    const r = await fetch(`/ball-trajectory-from-video/${videoId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        range_start_s: rangeStart,
        range_end_s: rangeEnd,
        render_options: renderOptions,
      }),
    });
    
    if (!r.ok) {
      alert("Could not start ball trajectory analysis: " + (await r.text()));
      return;
    }
    
    const data = await r.json();
    state.ballAnalysisId = data.ball_analysis_id;
    state.videoId = videoId;
    state.duration = media.duration_s || 0;
    
    // Store the video ID for later use
    sessionStorage.setItem("pendingBallAnalysisId", data.ball_analysis_id);
    
    // Immediately add a pending entry for the ball trajectory analysis
    state.analyses.push({
      analysis_id: data.ball_analysis_id,
      video_id: videoId,
      filename: media ? media.filename : "Unknown",
      algorithm: "ball_tracking_yolo",
      status: "pending",
      progress_percent: 0,
      range_start_s: rangeStart,
      range_end_s: rangeEnd,
      duration_s: media ? media.duration_s : 0,
    });
    renderAnalyses(state.analyses);
    
    await refreshLibrary();
    pollBallAnalysis(data.ball_analysis_id, videoId);
  } catch (e) {
    alert("Failed to start ball trajectory analysis: " + e.message);
  }
}

async function pollBallAnalysis(ballAnalysisId, videoId) {
  while (true) {
    const analyses = await fetch("/analyses").then(r => r.json());
    state.analyses = analyses;
    renderAnalyses(analyses);
    const ball = analyses.find(a => a.analysis_id === ballAnalysisId);
    
    if (ball) {
      if (ball.status === "done") {
        // Ball analysis done, now create a dummy analysis entry for the editor
        sessionStorage.setItem("ballAnalysisVideoId", videoId);
        sessionStorage.setItem("ballAnalysisId", ballAnalysisId);
        
        // Find the media and create a synthetic analysis entry
        const media = state.media.find(m => m.video_id === videoId);
        if (media) {
          state.videoId = videoId;
          state.analysisId = ballAnalysisId;
          state.duration = media.duration_s || 0;
          
          // Create a minimal segments array (full video)
          state.segments = [{
            start_s: 0,
            end_s: media.duration_s || 0,
            is_on: true,
            source: "ball_trajectory",
            decision_stage: "ball_analysis",
          }];
          
          // Load the editor with ball trajectory data
          await loadEditorWithBallTrajectory(videoId, ballAnalysisId);
        }
        await refreshLibrary();
        return;
      } else if (ball.status === "error") {
        alert("Ball trajectory analysis failed: " + (ball.error_msg || "Unknown error"));
        await refreshLibrary();
        return;
      }
    }
    
    await new Promise(r => setTimeout(r, 2000));
  }
}

async function loadEditorWithBallTrajectory(videoId, ballAnalysisId) {
  $("analyses-section").hidden = true;
  state.pollingAnalyses = false;  // Stop any ongoing analysis polling
  const [urlR, analysisR] = await Promise.all([
    fetch(`/video-file/${videoId}`).then(r => r.json()),
    fetch(`/analysis/${ballAnalysisId}`).then(r => r.ok ? r.json() : null).catch(() => null),
  ]);

  state.videoUrl = urlR.url;
  state.analysisId = ballAnalysisId;

  $("player").src = state.videoUrl;
  $("editor-title").textContent = `Ball Trajectory Analysis`;
  showOnly("editor-section");

  // Setup canvas and player
  setupCanvas();
  setupPlayer();

  // Setup resize observer for ball trail canvas
  const ballCanvas = $("ball-trail-canvas");
  if (ballCanvas) {
    ballCanvas._resizeObserver?.disconnect();
    ballCanvas._resizeObserver = new ResizeObserver(() => {
      console.log("[BallTrail] Canvas resized, redrawing");
      drawBallTrail();
    });
    ballCanvas._resizeObserver.observe(ballCanvas);
  }

  // Load ball trajectory data
  await loadBallTrajectory();

  // Auto-enable ball trail display
  $("ball-trail-toggle").checked = true;

  // Wait for video to be ready before drawing
  const video = $("player");
  if (video.readyState >= 1) {
    drawBallTrail();
  } else {
    video.addEventListener("loadedmetadata", () => {
      console.log("[BallTrail] Video metadata loaded, drawing trail");
      drawBallTrail();
    }, { once: true });
  }

  // Show ball trail panel
  const panel = $("ball-trail-panel");
  if (panel) panel.hidden = false;

  // Check if ball trajectory video is available
  const downloadBtn = $("download-ball-video-btn");
  if (downloadBtn) {
    // Video availability is checked via analysis data
    if (analysisR && analysisR.metadata && analysisR.metadata.detector === "ball_tracking_yolo") {
      // Check if video_path is available in analyses list
      fetch("/analyses").then(r => r.json()).then(analyses => {
        const ballAnalysis = analyses.find(a => a.analysis_id === ballAnalysisId);
        if (ballAnalysis && ballAnalysis.video_path) {
          state.ballVideoPath = ballAnalysis.video_path;
          downloadBtn.hidden = false;
        }
      }).catch(() => {});
    }
  }

  renderAll();
}

async function deleteVideo(v) {
  if (!confirm(`Delete "${v.filename}" and all its segments / exports?`)) return;
  await fetch(`/videos/${v.video_id}`, { method: "DELETE" });
  await refreshLibrary();
}

async function openCalibration(v) {
  state.calibrationVideoId = v.video_id;
  state.calibrationPoints = v.court_calibration ? [...v.court_calibration.points] : [];
  const urlR = await fetch(`/video-file/${v.video_id}`).then((r) => r.json());
  $("calibration-video").src = urlR.url;
  showOnly("calibration-section");
  setupCalibrationCanvas();
}

let calibrationCanvas, calibrationCtx;
function setupCalibrationCanvas() {
  calibrationCanvas = $("calibration-canvas");
  calibrationCtx = calibrationCanvas.getContext("2d");
  const video = $("calibration-video");
  const resize = () => {
    const rect = video.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    calibrationCanvas.width = Math.max(1, rect.width * dpr);
    calibrationCanvas.height = Math.max(1, rect.height * dpr);
    calibrationCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawCalibration();
  };
  resize();
  video.addEventListener("loadedmetadata", resize, { once: true });
  if (!calibrationCanvas.dataset.bound) {
    calibrationCanvas.addEventListener("click", (e) => {
      if (state.calibrationPoints.length >= 4) return;
      const rect = calibrationCanvas.getBoundingClientRect();
      state.calibrationPoints.push({
        x: (e.clientX - rect.left) / rect.width,
        y: (e.clientY - rect.top) / rect.height,
      });
      drawCalibration();
    });
    calibrationCanvas.dataset.bound = "1";
  }
}

function drawCalibration() {
  if (!calibrationCtx) return;
  const canvas = $("calibration-canvas");
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  calibrationCtx.clearRect(0, 0, w, h);
  const pts = state.calibrationPoints;
  if (!pts.length) return;
  calibrationCtx.strokeStyle = "#f2cc60";
  calibrationCtx.fillStyle = "rgba(242, 204, 96, 0.18)";
  calibrationCtx.lineWidth = 2;
  calibrationCtx.beginPath();
  pts.forEach((p, i) => {
    const x = p.x * w, y = p.y * h;
    if (i === 0) calibrationCtx.moveTo(x, y);
    else calibrationCtx.lineTo(x, y);
  });
  if (pts.length === 4) calibrationCtx.closePath();
  calibrationCtx.stroke();
  if (pts.length === 4) calibrationCtx.fill();
  pts.forEach((p, i) => {
    const x = p.x * w, y = p.y * h;
    calibrationCtx.fillStyle = "#cf222e";
    calibrationCtx.beginPath();
    calibrationCtx.arc(x, y, 5, 0, Math.PI * 2);
    calibrationCtx.fill();
    calibrationCtx.fillStyle = "white";
    calibrationCtx.font = "12px sans-serif";
    calibrationCtx.fillText(String(i + 1), x + 7, y - 7);
  });
}

$("reset-calibration-btn").addEventListener("click", () => {
  state.calibrationPoints = [];
  drawCalibration();
});

$("save-calibration-btn").addEventListener("click", async () => {
  if (state.calibrationPoints.length !== 4) {
    alert("Please click exactly 4 court corners.");
    return;
  }
  const r = await fetch(`/media/${state.calibrationVideoId}/court-calibration`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ points: state.calibrationPoints }),
  });
  if (!r.ok) {
    alert("Could not save calibration: " + (await r.text()));
    return;
  }
  await refreshLibrary();
});

$("close-calibration-btn").addEventListener("click", refreshLibrary);

$("show-upload-btn").addEventListener("click", () => {
  showOnly("upload-section");
  $("cancel-upload-btn").hidden = false;
});
$("cancel-upload-btn").addEventListener("click", async () => {
  await refreshLibrary();
});

// Click-help popover ------------------------------------------------------
// Single floating popover positioned beside whichever .help-dot was clicked.
// Closed by: clicking the same dot again, clicking outside, or pressing Esc.
function showHelpPopover(button) {
  const key = button.dataset.helpKey;
  const text = HELP_TEXT[key];
  const popover = $("help-popover");
  if (!text || !popover) return;
  popover.textContent = text;
  popover.hidden = false;
  // Position below + slightly right of the button, then nudge back into view.
  const rect = button.getBoundingClientRect();
  const top = rect.bottom + window.scrollY + 6;
  let left = rect.left + window.scrollX;
  popover.style.top = `${top}px`;
  popover.style.left = `${left}px`;
  // After it's measured, clamp to viewport.
  const popRect = popover.getBoundingClientRect();
  const overflowRight = popRect.right - (window.innerWidth - 8);
  if (overflowRight > 0) {
    left = Math.max(8, left - overflowRight);
    popover.style.left = `${left}px`;
  }
  popover.dataset.activeKey = key;
}
function hideHelpPopover() {
  const popover = $("help-popover");
  if (!popover) return;
  popover.hidden = true;
  popover.dataset.activeKey = "";
}
document.addEventListener("click", (e) => {
  const dot = e.target.closest(".help-dot");
  const popover = $("help-popover");
  if (dot) {
    e.preventDefault();
    e.stopPropagation();
    if (popover && popover.dataset.activeKey === dot.dataset.helpKey && !popover.hidden) {
      hideHelpPopover();
    } else {
      showHelpPopover(dot);
    }
    return;
  }
  if (popover && !popover.hidden && !popover.contains(e.target)) {
    hideHelpPopover();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideHelpPopover();
});

function bindRangeControls() {
  const startText = $("start-range-start");
  const endText = $("start-range-end");
  const startSlider = $("start-range-start-slider");
  const endSlider = $("start-range-end-slider");
  startSlider.addEventListener("input", () => {
    const end = parseFloat(endSlider.value || "0");
    if (parseFloat(startSlider.value) >= end) startSlider.value = Math.max(0, end - 0.5);
    startText.value = fmtPrecise(parseFloat(startSlider.value || "0"));
    startText.dataset.touched = "1";
  });
  endSlider.addEventListener("input", () => {
    const start = parseFloat(startSlider.value || "0");
    if (parseFloat(endSlider.value) <= start) endSlider.value = start + 0.5;
    endText.value = fmtPrecise(parseFloat(endSlider.value || "0"));
    endText.dataset.touched = "1";
  });
  startText.addEventListener("change", () => {
    const parsed = parseTime(startText.value);
    if (parsed !== null) startSlider.value = parsed;
    startText.dataset.touched = "1";
  });
  endText.addEventListener("change", () => {
    const parsed = parseTime(endText.value);
    if (parsed !== null) endSlider.value = parsed;
    endText.dataset.touched = "1";
  });
}
bindRangeControls();

// Upload ------------------------------------------------------------------
$("file-input").addEventListener("change", () => {
  $("upload-btn").disabled = !$("file-input").files.length;
});

$("upload-btn").addEventListener("click", async () => {
  const file = $("file-input").files[0];
  if (!file) return;

  // Skip upload if we already have a row for this filename.
  try {
    const videos = await fetch("/media").then((r) => r.json());
    const match = videos.find((v) => v.filename === file.name);
    if (match) {
      const reuse = confirm(
        `"${file.name}" is already in the media pool. Reuse it without re-uploading?`,
      );
      if (reuse) {
        await refreshLibrary();
        return;
      }
    }
  } catch (_) {
    // fall through to normal upload
  }

  $("upload-btn").disabled = true;
  $("upload-progress").hidden = false;

  try {
    const data = await uploadWithProgress(file);
    state.videoId = data.video_id;
    state.duration = data.duration_s;
    await refreshLibrary();
  } catch (e) {
    alert("Upload failed: " + e.message);
    $("upload-btn").disabled = false;
  }
});

// Initial load: show library if any videos exist, otherwise upload card.
refreshLibrary().catch((e) => console.error("refreshLibrary failed", e));

function uploadWithProgress(file) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        $("progress-bar").value = pct;
        $("progress-label").textContent = pct + "%";
      }
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(xhr.responseText || `HTTP ${xhr.status}`));
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Network error")));
    xhr.open("POST", "/upload");
    xhr.send(fd);
  });
}

// Status polling ----------------------------------------------------------
async function pollStatus() {
  const labels = {
    pending: "Queued",
    analyzing: "Analyzing video for in-play segments...",
    done: "Done",
    error: "Error",
  };
  while (true) {
    const r = await fetch(`/status/${state.videoId}`);
    const data = await r.json();
    $("status-text").textContent = labels[data.status] || data.status;
    if (data.progress_message) {
      $("status-sub").textContent = data.progress_message;
    }
    if (typeof data.progress_percent === "number") {
      $("analysis-progress").value = data.progress_percent;
    }
    if (data.status === "done") {
      await loadEditor();
      return;
    }
    if (data.status === "error") {
      $("status-text").textContent = "Analysis failed: " + (data.error_msg || "");
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

async function pollAnalyses() {
  if (state.pollingAnalyses) return;
  state.pollingAnalyses = true;
  while (state.pollingAnalyses) {
    const analyses = await fetch("/analyses").then((r) => r.json());
    state.analyses = analyses;
    renderAnalyses(analyses);
    if (!state.pollingAnalyses) break;
    const active = analyses.some((a) => a.status === "pending" || a.status === "analyzing");
    if (!active) {
      state.pollingAnalyses = false;
      if (!$("editor-section").hidden) break;
      await refreshLibrary();
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

// Editor ------------------------------------------------------------------
async function openAnalysis(a) {
  state.videoId = a.video_id;
  state.analysisId = a.analysis_id;
  state.duration = a.duration_s || 0;
  $("editor-title").textContent = `${a.filename} • ${a.algorithm}`;

  // For ball_tracking_yolo analysis, use the rendered video (with trajectory + speed overlays)
  if (a.algorithm === "ball_tracking_yolo") {
    state.ballAnalysisId = a.analysis_id;
  } else {
    state.ballAnalysisId = null;
  }

  await loadEditor();
}

$("back-to-library-btn").addEventListener("click", async () => {
  $("player").pause();
  await refreshLibrary();
});

async function loadEditor() {
  $("analyses-section").hidden = true;
  state.pollingAnalyses = false;  // Stop any ongoing analysis polling

  // For ball_tracking_yolo, use the rendered ball trajectory video instead of original
  let videoUrl;
  if (state.ballAnalysisId) {
    videoUrl = `/analysis/${state.ballAnalysisId}/ball-video`;
  } else {
    const urlR = await fetch(`/video-file/${state.videoId}`).then((r) => r.json());
    videoUrl = urlR.url;
  }

  const [segR, analysisR] = await Promise.all([
    fetch(`/analysis-segments/${state.analysisId}`).then((r) => r.json()),
    fetch(`/analysis/${state.analysisId}`).then((r) => r.ok ? r.json() : null),
  ]);
  state.duration = segR.duration_s;
  state.segments = segR.segments.map((s) => ({ ...s }));
  state.videoUrl = videoUrl;
  state.analysis = analysisR;
  state.poseData = null;
  if (analysisR && analysisR.metadata && analysisR.metadata.detector === "pose_skeleton_yolo") {
    state.poseData = await fetch(`/pose-data/${state.analysisId}`).then((r) => r.ok ? r.json() : null);
  }
  state.knobs = analysisR ? { ...analysisR.knobs } : null;
  state.defaultKnobs = analysisR ? { ...analysisR.defaults } : null;
  state.expandedSegments = new Set();

  $("player").src = state.videoUrl;
  showOnly("editor-section");
  renderKnobs();
  renderAnalysisConfigs();
  renderAll();
  renderPosePanel();
  setupCanvas();
  setupPlayer();
  loadBallTrajectory();
}

function renderAll() {
  drawTimeline();
  renderTable();
  renderSummary();
  renderAnalysisSummary();
  renderPosePanel();
}

function renderSummary() {
  const onSegs = state.segments.filter((s) => s.is_on);
  const onTotal = onSegs.reduce((a, s) => a + (s.end_s - s.start_s), 0);
  $("summary").textContent =
    `${onSegs.length} of ${state.segments.length} ON • ` +
    `${fmt(onTotal)} of ${fmt(state.duration)} (${state.duration > 0 ? Math.round((onTotal / state.duration) * 100) : 0}%)`;
}

function renderTable() {
  const tbody = $("segments-tbody");
  tbody.innerHTML = "";
  state.segments.forEach((seg, i) => {
    const tr = document.createElement("tr");
    if (!seg.is_on) tr.classList.add("off");

    const idxTd = document.createElement("td");
    idxTd.textContent = i + 1;

    const onTd = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = seg.is_on;
    cb.addEventListener("change", () => {
      seg.is_on = cb.checked;
      renderAll();
    });
    onTd.appendChild(cb);

    const startTd = numericCell(seg, "start_s", i);
    const endTd = numericCell(seg, "end_s", i);

    const durTd = document.createElement("td");
    durTd.textContent = fmt(seg.end_s - seg.start_s);

    const scoreTd = document.createElement("td");
    scoreTd.className = "score-cell";
    scoreTd.textContent = formatScore(seg);

    const samplesTd = document.createElement("td");
    samplesTd.textContent = typeof seg.sample_count === "number" ? seg.sample_count : "—";

    const stageTd = document.createElement("td");
    stageTd.textContent = seg.decision_stage || "manual";

    const actTd = document.createElement("td");
    const statsBtn = document.createElement("button");
    statsBtn.textContent = state.expandedSegments.has(i) ? "Stats ▴" : "Stats ▾";
    statsBtn.disabled = !(seg.samples && seg.samples.length);
    statsBtn.addEventListener("click", () => {
      if (state.expandedSegments.has(i)) state.expandedSegments.delete(i);
      else state.expandedSegments.add(i);
      renderTable();
    });
    const playBtn = document.createElement("button");
    playBtn.textContent = "▶ Preview";
    playBtn.addEventListener("click", () => playSegment(i));
    const delBtn = document.createElement("button");
    delBtn.textContent = "✕";
    delBtn.title = "Delete";
    delBtn.style.marginLeft = "6px";
    delBtn.addEventListener("click", () => {
      state.segments.splice(i, 1);
      renderAll();
    });
    actTd.appendChild(statsBtn);
    actTd.appendChild(playBtn);
    actTd.appendChild(delBtn);

    tr.append(idxTd, onTd, startTd, endTd, durTd, scoreTd, samplesTd, stageTd, actTd);
    tbody.appendChild(tr);
    if (state.expandedSegments.has(i)) {
      tbody.appendChild(renderSampleRow(seg));
    }
  });
}

function formatScore(seg) {
  if (typeof seg.avg_score !== "number") return "—";
  const min = typeof seg.min_score === "number" ? seg.min_score.toFixed(3) : "—";
  const avg = seg.avg_score.toFixed(3);
  const max = typeof seg.max_score === "number" ? seg.max_score.toFixed(3) : "—";
  return `${avg} (${min}-${max})`;
}

function renderSampleRow(seg) {
  const tr = document.createElement("tr");
  tr.className = "sample-row";
  const td = document.createElement("td");
  td.colSpan = 9;
  const table = document.createElement("table");
  table.className = "sample-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th>Time</th>
        <th>Foreground</th>
        <th>Smoothed</th>
        <th>Threshold</th>
        <th>Final</th>
      </tr>
    </thead>
  `;
  const body = document.createElement("tbody");
  for (const sample of (seg.samples || [])) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${fmtPrecise(sample.time_s)}</td>
      <td>${Number(sample.foreground_ratio).toFixed(4)}</td>
      <td>${Number(sample.smoothed_score).toFixed(4)}</td>
      <td>${sample.threshold_result ? "ON" : "OFF"}</td>
      <td>${seg.is_on ? "ON" : "OFF"}</td>
    `;
    body.appendChild(row);
  }
  table.appendChild(body);
  td.appendChild(table);
  tr.appendChild(td);
  return tr;
}

function numericCell(seg, field, idx) {
  const td = document.createElement("td");
  const input = document.createElement("input");
  input.type = "text";
  input.value = fmtPrecise(seg[field]);
  input.addEventListener("change", () => {
    const parsed = parseTime(input.value);
    if (parsed === null || parsed < 0 || parsed > state.duration) {
      input.value = fmtPrecise(seg[field]);
      return;
    }
    seg[field] = parsed;
    if (seg.end_s <= seg.start_s) {
      seg.end_s = Math.min(state.duration, seg.start_s + 0.1);
    }
    renderAll();
  });
  td.appendChild(input);
  return td;
}

function parseTime(str) {
  // Accepts "M:SS.s" or seconds.
  if (str.includes(":")) {
    const [m, s] = str.split(":");
    const mn = parseFloat(m), sn = parseFloat(s);
    if (isNaN(mn) || isNaN(sn)) return null;
    return mn * 60 + sn;
  }
  const n = parseFloat(str);
  return isNaN(n) ? null : n;
}

// Detector experiment -----------------------------------------------------
let knobTimer = null;
let renderingKnobs = false;

const KNOB_IDS = {
  diff_threshold: "knob-diff-threshold",
  motion_threshold: "knob-motion-threshold",
  merge_gap_s: "knob-merge-gap",
  min_segment_s: "knob-min-segment",
  segment_padding_s: "knob-segment-padding",
  sample_fps: "knob-sample-fps",
  median_bg_samples: "knob-median-bg-samples",
  court_weight: "knob-court-weight",
  outside_weight: "knob-outside-weight",
  near_camera_weight: "knob-near-camera-weight",
  enable_merge_gap: "enable-merge-gap",
  enable_min_segment: "enable-min-segment",
  enable_padding: "enable-padding",
};

function renderKnobs() {
  renderingKnobs = true;
  const knobs = state.knobs || state.defaultKnobs;
  const isPose = state.analysis && state.analysis.metadata && state.analysis.metadata.detector === "pose_skeleton_yolo";
  const disabled = isPose || !knobs || !(state.analysis && state.analysis.metadata);
  for (const [key, id] of Object.entries(KNOB_IDS)) {
    const el = $(id);
    if (!el) continue;
    if (el.type === "checkbox") {
      el.checked = !!(knobs && knobs[key]);
      el.disabled = disabled;
    } else {
      el.value = knobs && knobs[key] !== undefined ? knobs[key] : "";
      el.disabled = disabled || key === "sample_fps" || key === "median_bg_samples" ||
        key === "court_weight" || key === "outside_weight" || key === "near_camera_weight";
    }
  }
  renderingKnobs = false;
}

function readKnobs() {
  const base = { ...(state.knobs || state.defaultKnobs || {}) };
  base.diff_threshold = parseInt($("knob-diff-threshold").value || base.diff_threshold || 25, 10);
  base.motion_threshold = parseFloat($("knob-motion-threshold").value || base.motion_threshold || 0.02);
  base.merge_gap_s = parseFloat($("knob-merge-gap").value || base.merge_gap_s || 0);
  base.min_segment_s = parseFloat($("knob-min-segment").value || base.min_segment_s || 0);
  base.segment_padding_s = parseFloat($("knob-segment-padding").value || base.segment_padding_s || 0);
  base.enable_merge_gap = $("enable-merge-gap").checked;
  base.enable_min_segment = $("enable-min-segment").checked;
  base.enable_padding = $("enable-padding").checked;
  base.court_weight = parseFloat($("knob-court-weight").value || base.court_weight || 1);
  base.outside_weight = parseFloat($("knob-outside-weight").value || base.outside_weight || 0.15);
  base.near_camera_weight = parseFloat($("knob-near-camera-weight").value || base.near_camera_weight || 0);
  return base;
}

function renderAnalysisSummary() {
  const el = $("analysis-summary");
  if (!el) return;
  if (!(state.analysis && state.analysis.metadata)) {
    el.textContent = "No cached detector artifact for this analysis. Start a new analysis run to enable instant knob tuning.";
    return;
  }
  const s = state.analysis.summary || {};
  const changed = typeof s.changed_segment_count === "number" ? ` • ${s.changed_segment_count} changed` : "";
  el.textContent =
    `${s.on_count || 0} ON • ${fmt(s.on_duration_s || 0)} kept ` +
    `(${Math.round(s.on_percent || 0)}%) • ${s.sample_count || 0} cached samples${changed}`;
}

function renderAnalysisConfigs() {
  const root = $("analysis-configs");
  if (!root) return;
  root.innerHTML = "";
  const config = {
    ...(state.analysis && state.analysis.knobs ? state.analysis.knobs : {}),
    ...(state.analysis && state.analysis.summary && state.analysis.summary.config ? state.analysis.summary.config : {}),
  };
  if (state.poseData && state.poseData.metadata) {
    Object.assign(config, {
      pose_model: state.poseData.metadata.model_name,
      pose_conf: state.poseData.metadata.conf_threshold,
      pose_imgsz: state.poseData.metadata.image_size,
      sample_fps: state.poseData.metadata.sample_fps,
      range_start_s: state.poseData.metadata.range_start_s,
      range_end_s: state.poseData.metadata.range_end_s,
    });
  }
  // Filter pills to only those keys the detector actually uses.
  // Unknown detector → fall back to showing everything (forward compatible).
  const detector =
    (state.analysis && state.analysis.detector) ||
    (state.analysis && state.analysis.algorithm) ||
    config.detector;
  const allowed = DETECTOR_CONFIGS[detector];
  const entries = Object.entries(config)
    .filter(([_, v]) => v !== undefined && v !== null)
    .filter(([k]) => !allowed || allowed.includes(k));
  if (!entries.length) {
    root.textContent = "No saved config.";
    return;
  }
  for (const [key, value] of entries) {
    const pill = document.createElement("span");
    pill.className = "config-pill";
    pill.textContent = `${key}: ${typeof value === "number" ? Number(value).toFixed(key.endsWith("_s") ? 1 : 3).replace(/\.?0+$/, "") : value}`;
    if (HELP_TEXT[key]) pill.title = HELP_TEXT[key];
    root.appendChild(pill);
  }
}

function renderPosePanel() {
  const panel = $("pose-panel");
  if (!panel) return;
  if (!state.poseData) {
    panel.hidden = true;
    drawPoseOverlay();
    return;
  }
  panel.hidden = false;
  const summary = state.poseData.summary || {};
  $("pose-summary").textContent =
    `${summary.sample_count || 0} sampled frames • ` +
    `${summary.frames_with_poses || 0} with poses • ` +
    `${Number(summary.avg_detections_per_frame || 0).toFixed(2)} people/frame • ` +
    `avg keypoint confidence ${summary.avg_keypoint_confidence == null ? "—" : Number(summary.avg_keypoint_confidence).toFixed(2)}`;
  renderPoseCurrent();
}

function nearestPoseFrame(t) {
  const frames = state.poseData ? state.poseData.frames || [] : [];
  if (!frames.length) return null;
  let best = frames[0];
  let bestDist = Math.abs(best.time_s - t);
  for (const frame of frames) {
    const dist = Math.abs(frame.time_s - t);
    if (dist < bestDist) {
      best = frame;
      bestDist = dist;
    }
  }
  return best;
}

function renderPoseCurrent() {
  if (!state.poseData) return;
  const player = $("player");
  const t = player ? player.currentTime || 0 : 0;
  const nearest = nearestPoseFrame(t);
  $("pose-current").textContent = nearest
    ? `Nearest pose frame: ${fmtPrecise(nearest.time_s)} • ${nearest.detections.length} people`
    : "No pose frames.";
  renderPosePersonDetails(nearest);
  const tbody = $("pose-frames-tbody");
  tbody.innerHTML = "";
  const frames = state.poseData.frames || [];
  const nearby = frames
    .map((f) => ({ ...f, dist: Math.abs(f.time_s - t) }))
    .sort((a, b) => a.dist - b.dist)
    .slice(0, 12)
    .sort((a, b) => a.time_s - b.time_s);
  for (const frame of nearby) {
    const row = document.createElement("tr");
    const confs = frame.detections.map((d) => d.confidence || 0);
    const kp = [];
    frame.detections.forEach((d) => (d.keypoints || []).forEach((p) => kp.push(p.confidence || 0)));
    row.innerHTML = `
      <td>${fmtPrecise(frame.time_s)}</td>
      <td>${frame.detections.length}</td>
      <td>${confs.length ? Math.max(...confs).toFixed(2) : "—"}</td>
      <td>${kp.length ? (kp.reduce((a, b) => a + b, 0) / kp.length).toFixed(2) : "—"}</td>
    `;
    tbody.appendChild(row);
  }
  drawPoseOverlay();
}

const KEYPOINT_NAMES = [
  "nose", "left_eye", "right_eye", "left_ear", "right_ear",
  "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
  "left_wrist", "right_wrist", "left_hip", "right_hip",
  "left_knee", "right_knee", "left_ankle", "right_ankle",
];

function renderPosePersonDetails(frame) {
  const root = $("pose-person-details");
  root.innerHTML = "";
  if (!frame || !frame.detections.length) {
    root.textContent = "No person skeletons detected for the nearest sampled frame.";
    return;
  }
  frame.detections.forEach((det, idx) => {
    const box = det.box || {};
    const visible = (det.keypoints || []).filter((p) => (p.confidence || 0) >= 0.2);
    const card = document.createElement("div");
    card.className = "pose-person-card";
    const title = document.createElement("div");
    title.innerHTML =
      `<strong>Person ${idx + 1}</strong> ` +
      `box conf ${Number(det.confidence || 0).toFixed(2)} • ` +
      `visible keypoints ${visible.length}/${(det.keypoints || []).length} • ` +
      `box [${fmtNorm(box.x1)}, ${fmtNorm(box.y1)} → ${fmtNorm(box.x2)}, ${fmtNorm(box.y2)}]`;
    card.appendChild(title);

    const table = document.createElement("table");
    table.className = "sample-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th>Keypoint</th>
          <th>x</th>
          <th>y</th>
          <th>Conf</th>
        </tr>
      </thead>
    `;
    const body = document.createElement("tbody");
    for (const kp of det.keypoints || []) {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${KEYPOINT_NAMES[kp.index] || `kp_${kp.index}`}</td>
        <td>${fmtNorm(kp.x)}</td>
        <td>${fmtNorm(kp.y)}</td>
        <td>${Number(kp.confidence || 0).toFixed(2)}</td>
      `;
      body.appendChild(row);
    }
    table.appendChild(body);
    card.appendChild(table);
    root.appendChild(card);
  });
}

function fmtNorm(v) {
  return typeof v === "number" && isFinite(v) ? v.toFixed(3) : "—";
}

function schedulePreview() {
  if (
    renderingKnobs ||
    !(state.analysis && state.analysis.metadata) ||
    state.analysis.metadata.detector === "pose_skeleton_yolo"
  ) return;
  clearTimeout(knobTimer);
  knobTimer = setTimeout(applyPreviewKnobs, 250);
}

async function applyPreviewKnobs() {
  const knobs = readKnobs();
  try {
    const r = await fetch(`/analysis/${state.analysisId}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ knobs }),
    });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    state.knobs = { ...data.knobs };
    state.segments = data.segments.map((s) => ({ ...s }));
    state.analysis = {
      ...(state.analysis || {}),
      knobs: data.knobs,
      summary: data.summary,
    };
    state.expandedSegments = new Set();
    renderKnobs();
    renderAll();
  } catch (e) {
    $("analysis-summary").textContent = "Preview failed: " + e.message;
  }
}

for (const id of Object.values(KNOB_IDS)) {
  const el = $(id);
  if (el && !el.disabled) {
    el.addEventListener("input", schedulePreview);
    el.addEventListener("change", schedulePreview);
  }
}

$("reset-knobs-btn").addEventListener("click", () => {
  if (!state.defaultKnobs) return;
  state.knobs = { ...state.defaultKnobs };
  renderKnobs();
  applyPreviewKnobs();
});

// Timeline canvas ---------------------------------------------------------
let canvas, ctx;
function setupCanvas() {
  canvas = $("timeline");
  // Match canvas internal pixel size to its CSS width.
  const ro = new ResizeObserver(() => {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    canvas.width = w * dpr;
    canvas.height = 56 * dpr;
    ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawTimeline();
  });
  ro.observe(canvas);

  canvas.addEventListener("click", (e) => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = (x / rect.width) * state.duration;
    // Find segment under cursor.
    const idx = state.segments.findIndex((s) => t >= s.start_s && t <= s.end_s);
    if (idx >= 0 && e.shiftKey) {
      // Shift+click: toggle on/off
      state.segments[idx].is_on = !state.segments[idx].is_on;
      renderAll();
    } else {
      // Plain click: seek video to that time
      const player = $("player");
      player.currentTime = t;
      state.playMode = "free";
    }
  });
}

function drawTimeline() {
  if (!ctx) return;
  const w = canvas.clientWidth;
  const h = 56;
  ctx.clearRect(0, 0, w, h);

  // Background.
  ctx.fillStyle = "#eaeef2";
  ctx.fillRect(0, 0, w, h);

  if (state.duration <= 0) return;

  // Draw segments.
  for (const seg of state.segments) {
    const x = (seg.start_s / state.duration) * w;
    const segW = ((seg.end_s - seg.start_s) / state.duration) * w;
    ctx.fillStyle = seg.is_on ? "#1a7f37" : "#b1bac4";
    ctx.fillRect(x, 8, segW, h - 16);
  }

  // Tick marks every minute.
  ctx.fillStyle = "#57606a";
  ctx.font = "10px sans-serif";
  for (let m = 0; m * 60 <= state.duration; m++) {
    const x = ((m * 60) / state.duration) * w;
    ctx.fillRect(x, 0, 1, 4);
    if (m % 1 === 0) ctx.fillText(`${m}m`, x + 2, h - 2);
  }

  // Playhead.
  const player = $("player");
  if (player && isFinite(player.currentTime)) {
    const x = (player.currentTime / state.duration) * w;
    ctx.fillStyle = "#cf222e";
    ctx.fillRect(x - 1, 0, 2, h);
  }
}

// Player sync -------------------------------------------------------------
function setupPlayer() {
  const player = $("player");
  player.addEventListener("timeupdate", () => {
    drawTimeline();
    renderPoseCurrent();
    drawBallTrail();
    $("player-time").textContent = `${fmt(player.currentTime)} / ${fmt(state.duration)}`;

    if (state.playMode === "segment" && state.segmentEndS !== null) {
      if (player.currentTime >= state.segmentEndS) {
        player.pause();
        state.playMode = "free";
        state.segmentEndS = null;
      }
    } else if (state.playMode === "all_on" && state.segmentEndS !== null) {
      if (player.currentTime >= state.segmentEndS) {
        playNextOnSegment();
      }
    }
  });
}

const POSE_EDGES = [
  [5, 7], [7, 9], [6, 8], [8, 10], [5, 6],
  [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
  [0, 1], [0, 2], [1, 3], [2, 4],
];

function drawPoseOverlay() {
  const canvas = $("pose-overlay");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const video = $("player");
  const rect = video.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * dpr);
  canvas.height = Math.max(1, rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  if (!state.poseData || !$("pose-overlay-toggle").checked) return;
  const frame = nearestPoseFrame(video.currentTime || 0);
  if (!frame || Math.abs(frame.time_s - (video.currentTime || 0)) > 1.0) return;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  ctx.lineWidth = 2;
  for (const det of frame.detections || []) {
    const pts = det.keypoints || [];
    ctx.strokeStyle = "#f2cc60";
    for (const [a, b] of POSE_EDGES) {
      const pa = pts[a], pb = pts[b];
      if (!pa || !pb || pa.confidence < 0.2 || pb.confidence < 0.2) continue;
      ctx.beginPath();
      ctx.moveTo(pa.x * w, pa.y * h);
      ctx.lineTo(pb.x * w, pb.y * h);
      ctx.stroke();
    }
    ctx.fillStyle = "#cf222e";
    for (const p of pts) {
      if (p.confidence < 0.2) continue;
      ctx.beginPath();
      ctx.arc(p.x * w, p.y * h, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

$("pose-overlay-toggle").addEventListener("change", drawPoseOverlay);

// Ball Trail --------------------------------------------------------------

async function loadBallTrajectory() {
  if (!state.analysisId) {
    console.warn("[BallTrail] No analysisId, skipping load");
    return;
  }
  try {
    const data = await fetch(`/ball-trajectory/${state.analysisId}`).then(r => r.json());
    console.log("[BallTrail] Loaded trajectory data:", data);
    state.ballTrajectory = data.trajectory || [];
    console.log("[BallTrail] Ball trajectory points:", state.ballTrajectory.length);
    const panel = $("ball-trail-panel");
    const summary = $("ball-trail-summary");
    if (panel) panel.hidden = false;
    if (summary && data.summary) {
      summary.textContent = `${data.summary.detected_frames || 0} detections / ${data.summary.total_samples || 0} samples (${(data.summary.detection_rate || 0).toFixed(1)}%)`;
    }
  } catch (e) {
    console.warn("No ball trajectory data:", e.message);
    state.ballTrajectory = [];
    const panel = $("ball-trail-panel");
    if (panel) panel.hidden = true;
  }
}

function drawBallTrail() {
  const canvas = $("ball-trail-canvas");
  if (!canvas) {
    console.log("[BallTrail] Canvas not found");
    return;
  }
  const toggle = $("ball-trail-toggle");
  if (!toggle?.checked) {
    console.log("[BallTrail] Toggle not checked");
    return;
  }

  const ctx = canvas.getContext("2d");
  const video = $("player");
  if (!video) {
    console.log("[BallTrail] Video not found");
    return;
  }
  
  const rect = video.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;

  // Resize canvas to match video
  const newW = Math.max(1, rect.width * dpr);
  const newH = Math.max(1, rect.height * dpr);
  if (canvas.width !== newW || canvas.height !== newH) {
    canvas.width = newW;
    canvas.height = newH;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);

  console.log("[BallTrail] drawBallTrail called, trajectory length:", state.ballTrajectory.length);

  if (!state.ballTrajectory.length) {
    console.log("[BallTrail] No trajectory data loaded, analysisId:", state.analysisId);
    return;
  }

  // 计算视频在canvas中的实际显示区域（考虑object-fit: contain）
  const canvasW = canvas.clientWidth;
  const canvasH = canvas.clientHeight;
  const videoW = video.videoWidth || 1920;
  const videoH = video.videoHeight || 1080;
  // 添加时间补偿来对齐轨迹（video.currentTime 有轻微延迟）
  const timeOffset = 2 / 30; // 约0.067秒 = 2帧 @ 30fps
  const currentTime = Math.min((video.currentTime || 0) + timeOffset, state.duration);

  // 计算视频显示尺寸（保持宽高比）
  const canvasRatio = canvasW / canvasH;
  const videoRatio = videoW / videoH;
  let displayW, displayH, offsetX, offsetY;

  if (videoRatio > canvasRatio) {
    // 视频更宽，以宽度为准
    displayW = canvasW;
    displayH = canvasW / videoRatio;
    offsetX = 0;
    offsetY = (canvasH - displayH) / 2;
  } else {
    // 视频更高，以高度为准
    displayH = canvasH;
    displayW = canvasH * videoRatio;
    offsetX = (canvasW - displayW) / 2;
    offsetY = 0;
  }

  console.log("[BallTrail] Drawing at time:", currentTime, "canvas:", canvasW, "x", canvasH, "display:", displayW, "x", displayH, "offset:", offsetX, offsetY);

  // Get all detected points up to current time
  const allDetectedPoints = state.ballTrajectory
    .filter(p => p.x !== null && p.time_s <= currentTime)
    .sort((a, b) => a.time_s - b.time_s);

  if (allDetectedPoints.length === 0) {
    // Show "no detection" indicator
    ctx.fillStyle = "rgba(100, 100, 100, 0.5)";
    ctx.font = "14px sans-serif";
    ctx.fillText("No ball detected", 20, 30);
    return;
  }

  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  // 拖尾效果：使用固定数量点 + 插值平滑
  const trailDuration = 0.5; // 拖尾持续时间（秒）
  const maxTrailPoints = 15; // 固定显示点数

  // 筛选出在拖尾时间范围内的点
  let trailPoints = allDetectedPoints
    .filter(p => (currentTime - p.time_s) <= trailDuration)
    .slice(-maxTrailPoints);

  // 线性插值：添加当前时间的预测点使运动更平滑
  if (trailPoints.length >= 2) {
    const latest = trailPoints[trailPoints.length - 1];
    const prev = trailPoints[trailPoints.length - 2];
    const gap = latest.time_s - prev.time_s;

    if (gap > 0) {
      const t = (currentTime - latest.time_s) / gap;
      if (t > 0 && t <= 1) {
        // 在最新点和预测点之间插值
        const interpX = latest.x + (latest.x - prev.x) * t * 0.5;
        const interpY = latest.y + (latest.y - prev.y) * t * 0.5;
        trailPoints.push({
          time_s: currentTime,
          x: interpX,
          y: interpY
        });
      }
    }
  }

  if (trailPoints.length >= 2) {
    // 绘制连续轨迹线（光带效果）
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    for (let i = 1; i < trailPoints.length; i++) {
      const p1 = trailPoints[i - 1];
      const p2 = trailPoints[i];
      const age = currentTime - p2.time_s;
      const progress = Math.max(0, 1 - age / trailDuration); // 0-1，1是最新

      // 计算渐变：头部亮，尾部淡
      const alpha = progress * 0.9;
      const lineWidth = 2 + progress * 6; // 2-8px

      const x1 = offsetX + p1.x * displayW;
      const y1 = offsetY + p1.y * displayH;
      const x2 = offsetX + p2.x * displayW;
      const y2 = offsetY + p2.y * displayH;

      // 外发光
      ctx.save();
      ctx.shadowBlur = 15;
      ctx.shadowColor = `rgba(255, 200, 0, ${alpha * 0.8})`;
      ctx.strokeStyle = `rgba(255, 220, 50, ${alpha})`;
      ctx.lineWidth = lineWidth;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
      ctx.restore();
    }

    // 当前点高亮（使用插值后的位置）
    const currentPoint = trailPoints[trailPoints.length - 1];
    const bx = offsetX + currentPoint.x * displayW;
    const by = offsetY + currentPoint.y * displayH;

    // 大光晕
    const gradient = ctx.createRadialGradient(bx, by, 0, bx, by, 25);
    gradient.addColorStop(0, 'rgba(255, 255, 150, 0.9)');
    gradient.addColorStop(0.3, 'rgba(255, 220, 50, 0.6)');
    gradient.addColorStop(1, 'rgba(255, 150, 0, 0)');
    ctx.beginPath();
    ctx.arc(bx, by, 25, 0, Math.PI * 2);
    ctx.fillStyle = gradient;
    ctx.fill();

    // 中心亮点
    ctx.beginPath();
    ctx.arc(bx, by, 5, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255, 255, 255, 1)';
    ctx.fill();
  }
}

$("ball-trail-toggle").addEventListener("change", () => {
  if (!$("ball-trail-toggle").checked) {
    const canvas = $("ball-trail-canvas");
    if (canvas) {
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  } else {
    // When checked, trigger a draw
    console.log("[BallTrail] Toggle checked, drawing trail");
    drawBallTrail();
  }
});

function playSegment(i) {
  const seg = state.segments[i];
  const player = $("player");
  player.currentTime = seg.start_s;
  state.playMode = "segment";
  state.segmentEndS = seg.end_s;
  player.play();
}

function playNextOnSegment() {
  const player = $("player");
  if (state.allOnQueue.length === 0) {
    player.pause();
    state.playMode = "free";
    state.segmentEndS = null;
    return;
  }
  const seg = state.allOnQueue.shift();
  player.currentTime = seg.start_s;
  state.segmentEndS = seg.end_s;
  player.play();
}

$("play-all-on").addEventListener("click", () => {
  state.allOnQueue = state.segments.filter((s) => s.is_on).map((s) => ({ ...s }));
  state.playMode = "all_on";
  playNextOnSegment();
});

// Add segment at current time --------------------------------------------
$("add-segment-btn").addEventListener("click", () => {
  const t = $("player").currentTime || 0;
  const newSeg = {
    start_s: Math.max(0, t),
    end_s: Math.min(state.duration, t + 5),
    is_on: true,
    source: "manual",
    decision_stage: "manual",
  };
  state.segments.push(newSeg);
  state.segments.sort((a, b) => a.start_s - b.start_s);
  renderAll();
});

// Export ------------------------------------------------------------------
$("export-btn").addEventListener("click", async () => {
  const onSegs = state.segments.filter((s) => s.is_on);
  if (onSegs.length === 0) {
    alert("Select at least one segment to export.");
    return;
  }
  $("export-btn").disabled = true;
  $("export-section").hidden = false;
  $("download-link").hidden = true;
  $("export-text").textContent = "Stitching segments...";
  $("export-spinner").hidden = false;

  const r = await fetch(`/export/${state.videoId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segments: state.segments }),
  });
  const data = await r.json();
  pollExport(data.export_id);
});

async function pollExport(exportId) {
  while (true) {
    const r = await fetch(`/export-status/${exportId}`);
    const data = await r.json();
    if (data.status === "done") {
      $("export-text").textContent = `Done — ${fmt(data.duration_s)} of footage`;
      $("export-spinner").hidden = true;
      $("download-link").href = `/download/${exportId}`;
      $("download-link").textContent = "Download Video";
      $("download-link").hidden = false;
      $("export-btn").disabled = false;
      return;
    }
    if (data.status === "error") {
      $("export-text").textContent = "Export failed: " + (data.error_msg || "");
      $("export-spinner").hidden = true;
      $("export-btn").disabled = false;
      return;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

// Test hooks — exposes internals to tests in tests/test_frontend_render.js.
// Has no effect in production beyond setting a few read-only references on window.
if (typeof window !== "undefined") {
  window.DETECTOR_CONFIGS = DETECTOR_CONFIGS;
  window.HELP_TEXT = HELP_TEXT;
  window.state = state;
  window.renderAnalysisConfigs = renderAnalysisConfigs;
  window.showHelpPopover = showHelpPopover;
  window.hideHelpPopover = hideHelpPopover;
}

// Download ball trajectory video button
$("download-ball-video-btn")?.addEventListener("click", () => {
  if (!state.analysisId) {
    alert("No analysis selected");
    return;
  }
  // Open video download in new tab
  window.open(`/analysis/${state.analysisId}/ball-video`, "_blank");
});
