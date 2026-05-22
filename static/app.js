// State -------------------------------------------------------------------
const state = {
  videoId: null,
  analysisId: null,
  duration: 0,
  media: [],
  analyses: [],
  pollingAnalyses: false,
  analysis: null,
  knobs: null,
  defaultKnobs: null,
  videoUrl: null,
  calibrationVideoId: null,
  calibrationPoints: [],
  ballTrajectory: [],
  ballAnalysisId: null,
  analysesEditMode: false, // Whether analyses table is in edit mode
  renderData: null,     // Full render data from artifact
  hitMoments: [],
  speedEvents: null,
  playerKeypoints: null,
  courtCoordinates: null,
  ballMiniCoords: [],
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
function fmtDateTime(ts) {
  if (!ts || !isFinite(ts)) return "—";
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
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
  
  // 编辑模式 UI 控制
  const isEdit = state.analysesEditMode;
  const selectTh = $("analyses-th-select");
  const selectAll = $("select-all-analyses");
  const batchBtn = $("batch-delete-btn");
  const editBtn = $("edit-analyses-btn");
  
  selectTh.hidden = !isEdit;
  if (selectAll) selectAll.checked = false;
  if (batchBtn) batchBtn.hidden = !isEdit;
  if (editBtn) editBtn.textContent = isEdit ? "Cancel" : "Edit";
  
  for (const a of analyses) {
    const tr = document.createElement("tr");
    const isDeletable = a.status !== "analyzing";
    
    // 编辑模式勾选框列
    const checkTd = document.createElement("td");
    checkTd.hidden = !isEdit;
    if (isEdit) {
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "analysis-select-cb";
      cb.dataset.analysisId = a.analysis_id;
      if (!isDeletable) cb.disabled = true;
      checkTd.appendChild(cb);
    }
    
    const fileTd = document.createElement("td");
    fileTd.textContent = a.filename;
    const dateTd = document.createElement("td");
    dateTd.textContent = a.created_at ? fmtDateTime(a.created_at) : "—";
    dateTd.title = a.created_at ? new Date(a.created_at * 1000).toLocaleString("zh-CN") : "";
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
    if (isEdit) {
      // 编辑模式下隐藏原有操作按钮
    } else {
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
    }
    const range = document.createElement("div");
    range.className = "muted";
    if (typeof a.range_start_s === "number" && typeof a.range_end_s === "number") {
      range.textContent = `${fmtPrecise(a.range_start_s)}-${fmtPrecise(a.range_end_s)}`;
      progressTd.appendChild(range);
    }
    tr.append(checkTd, fileTd, dateTd, algoTd, statusTd, progressTd, actTd);
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

// 分析记录编辑模式 ------------------------------------------------

function toggleAnalysesEditMode() {
  state.analysesEditMode = !state.analysesEditMode;
  renderAnalyses(state.analyses);
}

async function batchDeleteAnalyses() {
  const cbs = document.querySelectorAll(".analysis-select-cb:checked");
  if (!cbs.length) return;
  const ids = Array.from(cbs).map(cb => cb.dataset.analysisId);
  if (!confirm(`Delete ${ids.length} selected analysis record(s)?`)) return;
  let failed = 0;
  for (const id of ids) {
    const r = await fetch(`/analyses/${id}`, { method: "DELETE" });
    if (!r.ok) failed++;
  }
  if (failed) {
    alert(`${ids.length - failed} deleted, ${failed} failed.`);
  }
  state.analysesEditMode = false;
  await refreshLibrary();
}

$("edit-analyses-btn").addEventListener("click", toggleAnalysesEditMode);
$("batch-delete-btn").addEventListener("click", batchDeleteAnalyses);
$("select-all-analyses")?.addEventListener("change", (e) => {
  const checked = e.target.checked;
  document.querySelectorAll(".analysis-select-cb").forEach(cb => {
    if (!cb.disabled) cb.checked = checked;
  });
});

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
    court_anim: $("opt-court-anim").checked,
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
        sessionStorage.setItem("ballAnalysisVideoId", videoId);
        sessionStorage.setItem("ballAnalysisId", ballAnalysisId);
        
        const media = state.media.find(m => m.video_id === videoId);
        if (media) {
          state.videoId = videoId;
          state.analysisId = ballAnalysisId;
          state.duration = media.duration_s || 0;
          await loadEditor();
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



async function deleteVideo(v) {
  if (!confirm(`Delete "${v.filename}" and all its segments / exports?`)) return;
  await fetch(`/videos/${v.video_id}`, { method: "DELETE" });
  await refreshLibrary();
}

async function openCalibration(v) {
  state.calibrationVideoId = v.video_id;
  state.calibrationPoints = v.court_calibration ? [...v.court_calibration.points] : [];
  showOnly("calibration-section");
  setupCalibrationCanvas();
}

let calibrationCanvas, calibrationCtx;
function setupCalibrationCanvas() {
  calibrationCanvas = $("calibration-canvas");
  calibrationCtx = calibrationCanvas.getContext("2d");

  // 直接用 Image 对象加载视频帧，绘制到 canvas 上，消除坐标偏移
  const img = new Image();
  img.onload = () => {
    const maxW = Math.min(window.innerWidth - 40, img.naturalWidth);
    const maxH = Math.min(window.innerHeight * 0.6, img.naturalHeight);
    const imgRatio = img.naturalWidth / img.naturalHeight;
    let dispW, dispH;
    if (maxW / maxH > imgRatio) {
      dispH = maxH;
      dispW = maxH * imgRatio;
    } else {
      dispW = maxW;
      dispH = maxW / imgRatio;
    }

    const dpr = window.devicePixelRatio || 1;
    calibrationCanvas.style.width = `${dispW}px`;
    calibrationCanvas.style.height = `${dispH}px`;
    calibrationCanvas.width = Math.max(1, dispW * dpr);
    calibrationCanvas.height = Math.max(1, dispH * dpr);
    calibrationCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

    calibrationCanvas._calibImg = img;
    drawCalibration();
  };
  img.src = `/calibration-frame/${state.calibrationVideoId}`;

  if (!calibrationCanvas.dataset.bound) {
    calibrationCanvas.addEventListener("click", (e) => {
      if (state.calibrationPoints.length >= 4) return;
      state.calibrationPoints.push({
        x: e.offsetX / calibrationCanvas.clientWidth,
        y: e.offsetY / calibrationCanvas.clientHeight,
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

  // 先把视频帧画到 canvas 上（与点击坐标完全对齐）
  const img = canvas._calibImg;
  if (img) {
    calibrationCtx.drawImage(img, 0, 0, w, h);
  }

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
  // 绘制点编号 + 位置标签
  const labels = ["近侧左边", "近侧右边", "远侧右边", "远侧左边"];
  pts.forEach((p, i) => {
    const x = p.x * w, y = p.y * h;
    calibrationCtx.fillStyle = "#cf222e";
    calibrationCtx.beginPath();
    calibrationCtx.arc(x, y, 6, 0, Math.PI * 2);
    calibrationCtx.fill();
    calibrationCtx.fillStyle = "white";
    calibrationCtx.font = "bold 13px sans-serif";
    calibrationCtx.fillText(String(i + 1), x + 9, y - 9);
    // 位置标签
    calibrationCtx.font = "12px sans-serif";
    calibrationCtx.fillStyle = "rgba(0,0,0,0.75)";
    const tw = calibrationCtx.measureText(labels[i]).width;
    calibrationCtx.fillRect(x - tw / 2 + 9, y - 28, tw + 4, 16);
    calibrationCtx.fillStyle = "#fff";
    calibrationCtx.fillText(labels[i], x + 11, y - 16);
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
  // refreshLibrary() 已处理 section 显示，无需额外 showOnly
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
  if (!startText || !endText || !startSlider || !endSlider) return;
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
  state.ballAnalysisId = a.algorithm === "ball_tracking_yolo" ? a.analysis_id : null;
  await loadEditor();
}

$("back-to-library-btn").addEventListener("click", async () => {
  $("player").pause();
  await refreshLibrary();
});

async function loadEditor() {
  $("analyses-section").hidden = true;
  state.pollingAnalyses = false;

  const [urlR, analysisR] = await Promise.all([
    fetch(`/video-file/${state.videoId}`).then((r) => r.json()),
    fetch(`/analysis/${state.analysisId}`).then((r) => r.ok ? r.json() : null),
  ]);
  state.videoUrl = urlR.url;
  state.analysis = analysisR;
  state.knobs = analysisR ? { ...analysisR.knobs } : null;
  state.defaultKnobs = analysisR ? { ...analysisR.defaults } : null;

  $("player").src = state.videoUrl;
  showOnly("editor-section");
  renderAnalysisConfigs();
  setupPlayer();
  loadBallTrajectory();
  loadRenderData();
  // 每次加载编辑器时重置导出区状态，避免残留上个分析的导出链接
  $("export-section").hidden = true;
  $("download-link").hidden = true;
  $("export-btn").disabled = false;
  checkExistingExport();

  // Setup resize observer for ball trail canvas
  const ballCanvas = $("ball-trail-canvas");
  if (ballCanvas) {
    ballCanvas._resizeObserver?.disconnect();
    ballCanvas._resizeObserver = new ResizeObserver(() => {
      drawRenderOverlay();
    });
    ballCanvas._resizeObserver.observe(ballCanvas);
  }

  // Auto-enable ball trail display
  $("ball-trail-toggle").checked = true;

  // Wait for video to be ready before drawing
  const video = $("player");
  if (video.readyState >= 1) {
    drawRenderOverlay();
  } else {
    video.addEventListener("loadedmetadata", () => {
      drawRenderOverlay();
    }, { once: true });
  }
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

function renderAnalysisConfigs() {
  const root = $("analysis-configs");
  if (!root) return;
  root.innerHTML = "";
  const config = {
    ...(state.analysis && state.analysis.knobs ? state.analysis.knobs : {}),
    ...(state.analysis && state.analysis.summary && state.analysis.summary.config ? state.analysis.summary.config : {}),
  };
  // Filter pills to only those keys the detector actually uses.
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



// Player sync -------------------------------------------------------------
function setupPlayer() {
  const player = $("player");
  player.addEventListener("timeupdate", () => {
    drawRenderOverlay();
    $("player-time").textContent = `${fmt(player.currentTime)} / ${fmt(state.duration)}`;
  });
}

// Ball Trail --------------------------------------------------------------

async function loadRenderData() {
  if (!state.analysisId) {
    console.warn("[RenderData] No analysisId, skipping load");
    return;
  }
  try {
    const data = await fetch(`/analysis/${state.analysisId}/render-data`).then(r => r.json());
    state.renderData = data;
    state.hitMoments = data.hit_moments || [];
    state.speedEvents = data.speed_events || null;
    state.playerKeypoints = data.player_keypoints || null;
    state.courtCoordinates = data.court_coordinates || null;
    state.ballMiniCoords = data.ball_mini_coords || [];
    console.log("[RenderData] Loaded:", {
      hitMoments: state.hitMoments.length,
      ballSpeeds: state.speedEvents?.ball_speeds?.length || 0,
      playerSpeeds: state.speedEvents?.player_speeds?.length || 0,
      hasKeypoints: !!state.playerKeypoints,
    });
  } catch (e) {
    console.warn("[RenderData] Failed to load:", e.message);
    state.renderData = null;
  }
}

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

$("ball-trail-toggle").addEventListener("change", () => {
  if (!$("ball-trail-toggle").checked) {
    const canvas = $("ball-trail-canvas");
    if (canvas) {
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  } else {
    console.log("[BallTrail] Toggle checked, drawing trail");
    drawRenderOverlay();
  }
});

// Render Overlay — 在 canvas 上实时绘制所有效果层 --------------------

const COCO_EDGES = [
  [5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16],
];

function drawRenderOverlay() {
  const canvas = $("ball-trail-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const video = $("player");
  if (!video) return;

  const rect = video.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const newW = Math.max(1, rect.width * dpr);
  const newH = Math.max(1, rect.height * dpr);
  if (canvas.width !== newW || canvas.height !== newH) {
    canvas.width = newW;
    canvas.height = newH;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);

  const canvasW = canvas.clientWidth;
  const canvasH = canvas.clientHeight;
  const videoW = video.videoWidth || 1920;
  const videoH = video.videoHeight || 1080;

  // 计算视频显示区域（object-fit: contain）
  const videoRatio = videoW / videoH;
  const canvasRatio = canvasW / canvasH;
  let displayW, displayH, offsetX, offsetY;
  if (videoRatio > canvasRatio) {
    displayW = canvasW;
    displayH = canvasW / videoRatio;
    offsetX = 0;
    offsetY = (canvasH - displayH) / 2;
  } else {
    displayH = canvasH;
    displayW = canvasH * videoRatio;
    offsetX = (canvasW - displayW) / 2;
    offsetY = 0;
  }

  const timeOffset = 2 / 30; // 约0.067秒时间补偿
  const currentTime = Math.min((video.currentTime || 0) + timeOffset, state.duration);
  const fps = 30;

  // 0. 球场叠加层（半透明球场关键线）
  if ($("opt-court-anim")?.checked && state.courtCoordinates?.original_keypoints) {
    drawCourtOnCtx(ctx, displayW, displayH, offsetX, offsetY, videoW, videoH);
  }

  // 1. 球轨迹（原有 drawBallTrail 逻辑）
  if ($("ball-trail-toggle")?.checked) {
    drawBallTrailOnCtx(ctx, canvasW, canvasH, displayW, displayH, offsetX, offsetY, currentTime);
  }

  // 2. 击球火花效果
  const ballSpeedToggle = $("opt-ball-speed")?.checked !== false;
  if (ballSpeedToggle || $("opt-spark")?.checked !== false) {
    for (const hm of state.hitMoments) {
      const hitFrame = hm.frame_idx;
      const hitTime = hitFrame / fps;
      const elapsed = currentTime - hitTime;
      if (elapsed >= 0 && elapsed < 0.6) {
        const hx = offsetX + (hm.x_norm || 0) * displayW;
        const hy = offsetY + (hm.y_norm || 0) * displayH;
        const progress = elapsed / 0.6; // 0→1
        const alpha = 1 - progress;

        // 击球冲击波（扩散圆环）
        const burstRadius = 10 + progress * 40;
        const gradient = ctx.createRadialGradient(hx, hy, 0, hx, hy, burstRadius);
        gradient.addColorStop(0, `rgba(255, 255, 200, ${alpha * 0.6})`);
        gradient.addColorStop(0.3, `rgba(255, 200, 50, ${alpha * 0.3})`);
        gradient.addColorStop(1, `rgba(255, 150, 0, 0)`);
        ctx.beginPath();
        ctx.arc(hx, hy, burstRadius, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();

        // 菱形火花飞出
        const diamondCount = Math.floor(15 * (1 - progress * 0.5));
        for (let i = 0; i < diamondCount; i++) {
          const angle = (i / diamondCount) * Math.PI * 2 + progress * 2;
          const dist = progress * (20 + (i % 5) * 12);
          const dx = Math.cos(angle) * dist;
          const dy = Math.sin(angle) * dist;
          const dLen = 6 - progress * 3;
          const dWid = 2 - progress * 1;
          ctx.save();
          ctx.translate(hx + dx, hy + dy);
          ctx.rotate(angle);
          ctx.globalAlpha = Math.max(0, alpha * 0.8);
          ctx.fillStyle = `rgba(255, ${220 - progress * 100}, 50, ${alpha})`;
          ctx.beginPath();
          ctx.moveTo(dLen * 0.5, 0);
          ctx.lineTo(0, -dWid);
          ctx.lineTo(-dLen * 0.5, 0);
          ctx.lineTo(0, dWid);
          ctx.closePath();
          ctx.fill();
          ctx.restore();
        }
      }
    }
  }

  // 3. 球速文本
  if ($("opt-ball-speed")?.checked !== false && state.speedEvents) {
    for (const bs of state.speedEvents.ball_speeds || []) {
      const hitFrame = bs.frame_idx || 0;
      const hitTime = hitFrame / fps;
      const elapsed = currentTime - hitTime;
      if (elapsed >= 0 && elapsed < 0.8) {
        const progress = elapsed / 0.8;
        const alpha = 1 - progress;
        const sx = offsetX + (bs.x_norm || 0) * displayW;
        const sy = offsetY + (bs.y_norm || 0) * displayH - 30;
        const playerId = bs.player_id || bs.hitter_id || "";
        const speedVal = bs.speed_mps != null ? bs.speed_mps : (bs.speed_ms || 0);
        const label = playerId ? `P${playerId} ${speedVal.toFixed(1)} m/s` : `${speedVal.toFixed(1)} m/s`;

        ctx.save();
        ctx.globalAlpha = Math.max(0, alpha);
        ctx.font = "bold 18px sans-serif";
        ctx.textAlign = "center";
        // 外发光
        ctx.shadowBlur = 8;
        ctx.shadowColor = `rgba(0, 200, 255, ${alpha * 0.5})`;
        ctx.fillStyle = `rgba(0, 220, 255, ${alpha})`;
        ctx.fillText(label, sx, sy);
        ctx.restore();
      }
    }
  }

  // 4. 跑动速度连线
  if ($("opt-running-speed")?.checked !== false && state.speedEvents) {
    for (const ps of state.speedEvents.player_speeds || []) {
      const startFrame = ps.start_frame || ps.frame_idx || 0;
      const endFrame = ps.end_frame || (startFrame + fps);
      const startTime = startFrame / fps;
      const endTime = endFrame / fps;
      const currentFrame = currentTime * fps;

      if (currentTime >= startTime && currentTime <= endTime) {
        const progress = (currentTime - startTime) / (endTime - startTime);
        const alpha = Math.min(1, (1 - progress) * 1.5);

        const sx = offsetX + (ps.start_xn || 0) * displayW;
        const sy = offsetY + (ps.start_yn || 0) * displayH;
        const ex = offsetX + (ps.end_xn || 0) * displayW;
        const ey = offsetY + (ps.end_yn || 0) * displayH;

        // 动画延伸点
        const animX = sx + (ex - sx) * Math.min(1, progress * 1.5);
        const animY = sy + (ey - sy) * Math.min(1, progress * 1.5);

        // 连线
        ctx.save();
        ctx.globalAlpha = Math.max(0, alpha);
        ctx.strokeStyle = "rgba(255, 220, 50, 0.6)";
        ctx.lineWidth = 3;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(animX, animY);
        ctx.stroke();
        ctx.setLineDash([]);

        // 端点圆点（移动端）
        ctx.beginPath();
        ctx.arc(animX, animY, 4, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(255, 220, 50, 0.8)";
        ctx.fill();

        // 速度文本
        const speedVal = ps.speed_mps != null ? ps.speed_mps : (ps.speed_ms || 0);
        const label = `${speedVal.toFixed(1)} m/s`;
        ctx.font = "bold 14px sans-serif";
        ctx.textAlign = "center";
        ctx.fillStyle = "rgba(255, 220, 50, 0.9)";
        ctx.fillText(label, (sx + animX) / 2, (sy + animY) / 2 - 8);
        ctx.restore();
      }
    }
  }
}

// 球场叠加层绘制（供 drawRenderOverlay 调用）
function drawCourtOnCtx(ctx, displayW, displayH, offsetX, offsetY, videoW, videoH) {
  const kps = state.courtCoordinates.original_keypoints;
  if (!kps || kps.length < 28) return;

  // 将像素坐标转为归一化坐标，再映射到 canvas 显示区域
  const pts = [];
  for (let i = 0; i < 28; i += 2) {
    pts.push({
      x: offsetX + (kps[i] / videoW) * displayW,
      y: offsetY + (kps[i + 1] / videoH) * displayH,
    });
  }

  // 球场边缘定义（与后端 COURT_EDGES 一致）
  const edges = [
    [0, 2], [2, 3], [3, 1], [1, 0],            // 外围 doubles 矩形
    [0, 4], [4, 5], [5, 7], [7, 6],            // singles 线
    [6, 4],                                     // 上单打线
    [4, 8], [8, 9], [9, 11], [11, 10],         // 发球区
    [10, 12], [12, 13],                         // 球网
  ];

  ctx.save();
  // 外层光晕
  for (const [i1, i2] of edges) {
    const p1 = pts[i1], p2 = pts[i2];
    if (!p1 || !p2) continue;
    ctx.strokeStyle = "rgba(180, 220, 255, 0.25)";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.stroke();
  }
  // 内层线条
  for (const [i1, i2] of edges) {
    const p1 = pts[i1], p2 = pts[i2];
    if (!p1 || !p2) continue;
    ctx.strokeStyle = "rgba(220, 240, 255, 0.45)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.stroke();
  }
  // 关键点
  ctx.fillStyle = "rgba(220, 240, 255, 0.5)";
  for (const pt of pts) {
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, 3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

// 从 drawBallTrail 抽取的轨迹绘制逻辑（供 drawRenderOverlay 调用）
function drawBallTrailOnCtx(ctx, canvasW, canvasH, displayW, displayH, offsetX, offsetY, currentTime) {
  if (!state.ballTrajectory.length) return;

  const trailDuration = 0.5;
  const maxTrailPoints = 15;

  const allDetectedPoints = state.ballTrajectory
    .filter(p => p.x !== null && p.time_s <= currentTime)
    .sort((a, b) => a.time_s - b.time_s);

  if (allDetectedPoints.length === 0) return;

  let trailPoints = allDetectedPoints
    .filter(p => (currentTime - p.time_s) <= trailDuration)
    .slice(-maxTrailPoints);

  if (trailPoints.length >= 2) {
    const latest = trailPoints[trailPoints.length - 1];
    const prev = trailPoints[trailPoints.length - 2];
    const gap = latest.time_s - prev.time_s;
    if (gap > 0) {
      const t = (currentTime - latest.time_s) / gap;
      if (t > 0 && t <= 1) {
        const interpX = latest.x + (latest.x - prev.x) * t * 0.5;
        const interpY = latest.y + (latest.y - prev.y) * t * 0.5;
        trailPoints.push({ time_s: currentTime, x: interpX, y: interpY });
      }
    }
  }

  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  for (let i = 1; i < trailPoints.length; i++) {
    const p1 = trailPoints[i - 1];
    const p2 = trailPoints[i];
    const age = currentTime - p2.time_s;
    const progress = Math.max(0, 1 - age / trailDuration);

    const alpha = progress * 0.9;
    const lineWidth = 2 + progress * 6;
    const x1 = offsetX + p1.x * displayW;
    const y1 = offsetY + p1.y * displayH;
    const x2 = offsetX + p2.x * displayW;
    const y2 = offsetY + p2.y * displayH;

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

  const currentPoint = trailPoints[trailPoints.length - 1];
  const bx = offsetX + currentPoint.x * displayW;
  const by = offsetY + currentPoint.y * displayH;

  const gradient = ctx.createRadialGradient(bx, by, 0, bx, by, 25);
  gradient.addColorStop(0, 'rgba(255, 255, 150, 0.9)');
  gradient.addColorStop(0.3, 'rgba(255, 220, 50, 0.6)');
  gradient.addColorStop(1, 'rgba(255, 150, 0, 0)');
  ctx.beginPath();
  ctx.arc(bx, by, 25, 0, Math.PI * 2);
  ctx.fillStyle = gradient;
  ctx.fill();

  ctx.beginPath();
  ctx.arc(bx, by, 5, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(255, 255, 255, 1)';
  ctx.fill();
}

// Check existing export ---------------------------------------------------
async function checkExistingExport() {
  if (!state.videoId || !state.analysisId) return;
  try {
    const r = await fetch(`/exports-by-analysis/${state.analysisId}?video_id=${state.videoId}`);
    if (!r.ok) return;
    const exports = await r.json();
    if (!Array.isArray(exports)) return;
    const doneExport = exports.find(e => e.status === "done");
    if (doneExport) {
      $("export-section").hidden = false;
      $("export-btn").disabled = false;
      $("export-spinner").hidden = true;
      $("export-text").textContent = `Done — ${fmt(doneExport.duration_s || 0)} of footage`;
      $("download-link").href = `/download/${doneExport.export_id}`;
      $("download-link").textContent = "Download Video";
      $("download-link").hidden = false;
    }
  } catch (e) {
    console.warn("[Export] Failed to check existing exports:", e.message);
  }
}

// Export ------------------------------------------------------------------
$("export-btn").addEventListener("click", async () => {
  $("export-btn").disabled = true;
  $("export-section").hidden = false;
  $("download-link").hidden = true;
  $("export-text").textContent = "Rendering video with overlays...";
  $("export-spinner").hidden = false;

  // 导出全量视频范围
  const segments = [{
    start_s: 0,
    end_s: state.duration,
    is_on: true,
  }];

  const r = await fetch(`/export/${state.videoId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segments, analysis_id: state.analysisId }),
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


