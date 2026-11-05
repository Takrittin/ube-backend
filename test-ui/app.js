"use strict";
const $ = id => document.getElementById(id);
const state = { ai: null, file: null, image: null, result: null, busy: false };
let imageURL = null;

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "notice error" : "notice";
  $("notice").hidden = !message;
}

function controls() {
  $("photo").disabled = state.busy;
  $("clear").disabled = state.busy;
  $("check-ai").disabled = state.busy;
  $("detect").disabled = state.busy || !state.file || !state.ai?.configured;
  $("detect").textContent = state.busy ? "Working…" : "Run real detection →";
}

async function run(task) {
  if (state.busy) return;
  state.busy = true; notice(""); controls();
  try { await task(); }
  catch (error) { notice(error.message || "Request failed.", true); }
  finally { state.busy = false; controls(); }
}

async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, { ...options, credentials: "omit", cache: "no-store", redirect: "error" }); }
  catch { throw new Error("Cannot reach the backend. Check that Docker is running."); }
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { detail: "Unexpected backend response." }; }
  if (path.endsWith("/detect")) {
    $("request-log").textContent = JSON.stringify({ status: response.status, response: data.ai_result || data }, null, 2);
  }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join("; ") : data.detail;
    throw new Error(`${response.status}: ${detail || "Request failed"}`);
  }
  return data;
}

async function refreshAI() {
  state.ai = null;
  try {
    state.ai = await api("/api/v1/model-test/status");
    $("provider-badge").textContent = state.ai.configured ? "Real Roboflow · configured" : "API key / model setup needed";
    $("provider-badge").className = `badge ${state.ai.configured ? "good" : "warning"}`;
    $("model-id").textContent = state.ai.model_id;
    $("model-message").textContent = state.ai.message;
    $("model-threshold").textContent = `Threshold: ${Math.round(state.ai.confidence_threshold * 100)}% · Pallet labels: ${state.ai.target_classes.join(", ")}`;
    $("connection").textContent = "Backend online";
    $("connection-dot").className = "status-dot online";
  } catch (error) {
    $("provider-badge").textContent = "Model test unavailable";
    $("provider-badge").className = "badge warning";
    $("model-message").textContent = error.message;
    $("connection").textContent = "Check backend configuration";
    throw error;
  }
}

function resetResults() {
  state.result = null;
  $("results").hidden = true; $("results-empty").hidden = false;
  $("test-state").textContent = state.file ? "Ready to test" : "Choose a photo";
  $("test-state").className = "badge neutral";
  $("request-log").textContent = "No detection requests yet.";
  draw();
}

function clear() {
  state.file = null; state.image = null;
  if (imageURL) URL.revokeObjectURL(imageURL);
  imageURL = null;
  $("photo").value = "";
  $("file-name").textContent = "JPEG, PNG or WEBP · up to 10 MiB / 25 MP";
  $("preview").hidden = true; $("empty-image").hidden = false;
  resetResults(); controls();
}

async function showImage(source) {
  const image = new Image(); image.src = source;
  await image.decode();
  state.image = image;
  $("preview").hidden = false; $("empty-image").hidden = true;
  draw();
}

function draw() {
  if (!state.image) return;
  const canvas = $("preview"), ctx = canvas.getContext("2d");
  const scale = Math.min(1, 1600 / state.image.naturalWidth, 1600 / state.image.naturalHeight);
  canvas.width = Math.round(state.image.naturalWidth * scale);
  canvas.height = Math.round(state.image.naturalHeight * scale);
  ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
  const m = state.result?.measurements;
  if (!m?.image) return;
  const sx = canvas.width / m.image.width, sy = canvas.height / m.image.height;
  ctx.font = "600 14px system-ui";
  for (const box of m.predictions) {
    const x = Math.max(0, (box.x - box.width / 2) * sx), y = Math.max(0, (box.y - box.height / 2) * sy);
    const right = Math.min(canvas.width, (box.x + box.width / 2) * sx), bottom = Math.min(canvas.height, (box.y + box.height / 2) * sy);
    if (right <= x || bottom <= y) continue;
    const color = box.is_pallet ? "#139b63" : "#3c68c3";
    ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.strokeRect(x, y, right - x, bottom - y);
    const label = `${box.class_name} ${Math.round(box.confidence * 100)}%`;
    const width = Math.min(canvas.width, ctx.measureText(label).width + 14);
    const lx = Math.max(0, Math.min(x, canvas.width - width)), ly = Math.max(0, y - 25);
    ctx.fillStyle = color; ctx.fillRect(lx, ly, width, 25);
    ctx.fillStyle = "white"; ctx.fillText(label, lx + 7, ly + 17, width - 10);
  }
}

function renderResult(result) {
  const m = result.measurements;
  state.result = result;
  $("results").hidden = false; $("results-empty").hidden = true;
  $("pallet-result").textContent = m.pallet_detected ? `Detected · ${m.pallet_count}` : "Not detected";
  $("pallet-confidence").textContent = m.pallet_detected ? `${(result.confidence * 100).toFixed(1)}%` : "—";
  $("object-count").textContent = String(m.predictions.length);
  $("result-note").textContent = m.pallet_detected ? "Your model identified a target pallet label. Review its boxes to check whether they cover the correct object." : "Roboflow responded successfully, but returned no target pallet above the threshold. Check the photo and model labels; other detections are shown below.";
  $("result-model").textContent = `Model: ${result.model_version} · Detection threshold: ${Math.round(m.confidence_threshold * 100)}%`;
  const rows = m.predictions.map(p => {
    const row = document.createElement("tr");
    for (const value of [p.class_name, `${(p.confidence * 100).toFixed(1)}%`, `${Math.round(p.x)}, ${Math.round(p.y)} / ${Math.round(p.width)} × ${Math.round(p.height)}`]) {
      const td = document.createElement("td"); td.textContent = value; row.append(td);
    }
    return row;
  });
  if (!rows.length) {
    const row = document.createElement("tr"), td = document.createElement("td");
    td.colSpan = 3; td.textContent = "No detections above the threshold."; row.append(td); rows.push(row);
  }
  $("detections").replaceChildren(...rows);
  $("test-state").textContent = "Real inference completed";
  $("test-state").className = "badge good";
  draw();
}

$("photo").addEventListener("change", () => {
  const file = $("photo").files[0];
  run(async () => {
    clear();
    if (!file) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new Error("Choose a JPEG, PNG or WEBP photo.");
    if (file.size > 10 * 1024 * 1024) throw new Error("Photo must not exceed 10 MiB.");
    try {
      imageURL = URL.createObjectURL(file);
      await showImage(imageURL);
      if (state.image.naturalWidth * state.image.naturalHeight > 25_000_000) throw new Error("Photo must not exceed 25 megapixels.");
    } catch (error) { clear(); throw new Error(error.message || "Cannot decode this photo."); }
    state.file = file;
    $("file-name").textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MiB`;
    $("test-state").textContent = "Ready to test";
  });
});
$("clear").addEventListener("click", () => { clear(); notice(""); });
$("check-ai").addEventListener("click", () => run(refreshAI));
$("detect").addEventListener("click", () => run(async () => {
  if (!state.file || !state.ai?.configured) throw new Error("Configure the server API key and choose a photo first.");
  resetResults();
  const form = new FormData(); form.append("file", state.file);
  $("test-state").textContent = "Calling Roboflow…";
  notice("Sending this photo to Roboflow Cloud. The request can take up to 90 seconds…");
  let data;
  try { data = await api("/api/v1/model-test/detect", { method: "POST", body: form }); }
  catch (error) { $("test-state").textContent = "Inference failed"; throw error; }
  await showImage(data.image_data_url);
  renderResult(data.ai_result);
  notice("Real model response received. Nothing was saved to inspection history.");
}));

controls();
run(refreshAI);
