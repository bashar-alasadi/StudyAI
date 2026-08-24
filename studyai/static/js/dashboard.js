(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const elements = Object.fromEntries([
    "audio-file", "drop-zone", "file-name", "upload-button", "lecture-url", "url-button", "upload-status",
    "include-explanations", "verbatim-transcript", "explanation-tab",
    "progress-panel", "progress-percent", "progress-bar", "stage-message",
    "segment-progress", "retry-button", "results-panel", "result-content", "copy-button",
    "export-actions",
  ].map(id => [id, document.getElementById(id)]));
  const stageMessages = {
    uploading: "جارٍ رفع المحاضرة…", uploaded: "اكتمل الرفع.", queued: "المهمة في انتظار العامل…",
    downloading: "جارٍ تنزيل محتوى الرابط…",
    preparing_media: "جارٍ فحص الملف واستخراج الصوت…", segmenting: "جارٍ تقسيم المحاضرة…",
    transcribing: "جارٍ تفريغ أجزاء المحاضرة كاملة…", assembling: "جارٍ تجميع النص الكامل…",
    summarizing: "جارٍ إنشاء ملخص يغطي المحاضرة كاملة…",
    generating_questions: "جارٍ إنشاء أسئلة من كامل المحاضرة…",
    completed: "اكتملت المعالجة بنجاح.", failed: "توقفت المعالجة بسبب خطأ.",
  };
  let currentJobId = null;
  let results = {};
  let selectedResult = "transcript";
  let pollTimer = null;
  let youtubeApiPromise = null;
  const uploadStorageKey = "studyai-active-upload";

  elements["audio-file"].addEventListener("change", () => chooseFile(elements["audio-file"].files[0]));
  ["dragenter", "dragover"].forEach(name => elements["drop-zone"].addEventListener(name, event => {
    event.preventDefault(); elements["drop-zone"].classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach(name => elements["drop-zone"].addEventListener(name, event => {
    event.preventDefault(); elements["drop-zone"].classList.remove("dragging");
  }));
  elements["drop-zone"].addEventListener("drop", event => {
    if (event.dataTransfer.files.length) {
      elements["audio-file"].files = event.dataTransfer.files;
      chooseFile(elements["audio-file"].files[0]);
    }
  });
  elements["upload-button"].addEventListener("click", startUpload);
  elements["url-button"].addEventListener("click", startUrlImport);
  elements["lecture-url"].addEventListener("keydown", event => {
    if (event.key === "Enter") startUrlImport();
  });
  elements["retry-button"].addEventListener("click", retryJob);
  elements["copy-button"].addEventListener("click", async () => {
    await navigator.clipboard.writeText(results[selectedResult] || "");
    elements["copy-button"].textContent = "تم النسخ ✓";
    setTimeout(() => { elements["copy-button"].textContent = "نسخ"; }, 1500);
  });
  document.querySelectorAll(".result-tab").forEach(tab => tab.addEventListener("click", () => {
    selectedResult = tab.dataset.result;
    document.querySelectorAll(".result-tab").forEach(item => item.classList.toggle("active", item === tab));
    elements["result-content"].textContent = results[selectedResult] || "";
    updateExportActions();
  }));
  document.querySelectorAll(".export-button").forEach(button => button.addEventListener("click", () => {
    if (!currentJobId || !results[selectedResult]) return;
    window.location.assign(`/api/jobs/${currentJobId}/export/${selectedResult}.${button.dataset.format}`);
  }));
  document.querySelectorAll(".step").forEach(step => step.addEventListener("click", () => {
    document.getElementById(step.dataset.target).scrollIntoView({ behavior: "smooth" });
  }));

  async function startUpload() {
    const file = elements["audio-file"].files[0];
    if (!file) return showUploadStatus("اختر ملفًا أولًا.", true);
    setButtonLoading(true);
    try {
      let upload = await resumeUpload(file);
      if (!upload) {
        upload = await request("/api/uploads", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, total_size: file.size, mime_type: file.type || "" }),
        });
        localStorage.setItem(uploadStorageKey, JSON.stringify({
          upload_id: upload.upload_id, name: file.name, size: file.size,
          modified: file.lastModified,
        }));
      }
      for (let index = upload.received_chunks; index < upload.expected_chunks; index += 1) {
        const start = index * upload.chunk_size;
        const chunk = file.slice(start, Math.min(start + upload.chunk_size, file.size));
        await requestWithRetry(`/api/uploads/${upload.upload_id}/chunks/${index}`, {
          method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: chunk,
        });
        const percent = Math.round(((index + 1) / upload.expected_chunks) * 100);
        showUploadStatus(`جارٍ رفع المحاضرة… ${percent}%`);
      }
      const explain = elements["include-explanations"].checked ? "1" : "0";
      const verbatim = elements["verbatim-transcript"].checked ? "1" : "0";
      const queued = await request(`/api/uploads/${upload.upload_id}/complete?include_explanations=${explain}&verbatim_transcript=${verbatim}`, { method: "POST" });
      localStorage.removeItem(uploadStorageKey);
      currentJobId = queued.job_id;
      elements["progress-panel"].classList.remove("hidden");
      activateStep("progress-panel");
      await pollJob();
    } catch (error) {
      showUploadStatus(error.message, true);
    } finally {
      setButtonLoading(false);
    }
  }

  async function startUrlImport() {
    const url = elements["lecture-url"].value.trim();
    if (!url) return showUploadStatus("ألصق رابط المحاضرة أولًا.", true);
    setUrlLoading(true);
    showUploadStatus("جارٍ إضافة الرابط…");
    try {
      const durationSeconds = await getYouTubeDuration(url);
      const queued = await request("/api/uploads/url", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url,
          duration_seconds: durationSeconds,
          include_explanations: elements["include-explanations"].checked,
          verbatim_transcript: elements["verbatim-transcript"].checked,
        }),
      });
      currentJobId = queued.job_id;
      elements["progress-panel"].classList.remove("hidden");
      activateStep("progress-panel");
      await pollJob();
    } catch (error) {
      showUploadStatus(error.message, true);
    } finally {
      setUrlLoading(false);
    }
  }

  async function pollJob() {
    clearTimeout(pollTimer);
    if (!currentJobId) return;
    try {
      const job = await request(`/api/jobs/${currentJobId}`);
      renderProgress(job);
      if (job.status === "completed") return loadResult();
      if (job.status !== "failed" && job.status !== "cancelled") {
        pollTimer = setTimeout(pollJob, 2000);
      }
    } catch (error) {
      elements["stage-message"].textContent = `تعذر تحديث الحالة: ${error.message}`;
      pollTimer = setTimeout(pollJob, 5000);
    }
  }

  function renderProgress(job) {
    elements["progress-panel"].classList.remove("hidden");
    elements["progress-percent"].textContent = `${job.progress}%`;
    elements["progress-bar"].style.width = `${job.progress}%`;
    elements["stage-message"].textContent = job.error || stageMessages[job.stage] || "جارٍ العمل…";
    elements["segment-progress"].textContent = job.total_segments
      ? `اكتمل تفريغ ${job.completed_segments} من ${job.total_segments} أجزاء` : "";
    elements["retry-button"].classList.toggle("hidden", job.status !== "failed");
    activateStep(job.status === "completed" ? "results-panel" : "progress-panel");
  }

  async function loadResult() {
    results = await request(`/api/jobs/${currentJobId}/result`);
    elements["explanation-tab"].classList.toggle("hidden", !results.explanation);
    elements["result-content"].textContent = results[selectedResult] || "";
    updateExportActions();
    elements["results-panel"].classList.remove("hidden");
    activateStep("results-panel");
  }

  async function retryJob() {
    elements["retry-button"].disabled = true;
    try {
      await request(`/api/jobs/${currentJobId}/retry`, { method: "POST" });
      elements["retry-button"].classList.add("hidden"); await pollJob();
    } catch (error) { elements["stage-message"].textContent = error.message; }
    finally { elements["retry-button"].disabled = false; }
  }

  async function reconnectLatest() {
    try {
      const latest = await request("/api/jobs/latest");
      if (!latest.job_id) return;
      currentJobId = latest.job_id;
      if (latest.status === "completed") { renderProgress(latest); await loadResult(); }
      else { renderProgress(latest); await pollJob(); }
    } catch { /* A new account may not have jobs yet. */ }
  }

  async function request(url, options = {}) {
    options.headers = { ...(options.headers || {}), "X-CSRF-Token": csrf };
    const response = await fetch(url, options); let data;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) throw new Error(data.error || "تعذر إكمال الطلب.");
    return data;
  }

  function youtubeVideoId(value) {
    try {
      const parsed = new URL(value);
      const host = parsed.hostname.toLowerCase().replace(/^www\./, "");
      if (host === "youtu.be") return parsed.pathname.split("/").filter(Boolean)[0] || null;
      if (host.endsWith("youtube.com")) {
        if (parsed.searchParams.get("v")) return parsed.searchParams.get("v");
        const parts = parsed.pathname.split("/").filter(Boolean);
        if (["live", "shorts", "embed"].includes(parts[0])) return parts[1] || null;
      }
    } catch { /* The server will show the URL validation message. */ }
    return null;
  }

  function loadYouTubeApi() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    if (youtubeApiPromise) return youtubeApiPromise;
    youtubeApiPromise = new Promise((resolve, reject) => {
      const previous = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        if (typeof previous === "function") previous();
        resolve();
      };
      const script = document.createElement("script");
      script.src = "https://www.youtube.com/iframe_api";
      script.onerror = () => reject(new Error("YouTube API unavailable"));
      document.head.appendChild(script);
      setTimeout(() => reject(new Error("YouTube API timeout")), 10000);
    });
    return youtubeApiPromise;
  }

  async function getYouTubeDuration(url) {
    const videoId = youtubeVideoId(url);
    if (!videoId) return null;
    try { await loadYouTubeApi(); } catch { return null; }
    return await new Promise(resolve => {
      const holder = document.createElement("div");
      holder.id = `youtube-duration-${Date.now()}`;
      holder.style.cssText = "position:fixed;left:-9999px;width:1px;height:1px;overflow:hidden";
      document.body.appendChild(holder);
      let player;
      let settled = false;
      let durationPoll = null;
      let timeout = null;
      const finish = value => {
        if (settled) return;
        settled = true;
        if (durationPoll) clearInterval(durationPoll);
        if (timeout) clearTimeout(timeout);
        try { if (player) player.destroy(); } catch { holder.remove(); }
        resolve(Number.isFinite(value) && value > 0 ? value : null);
      };
      timeout = setTimeout(() => finish(null), 12000);
      player = new window.YT.Player(holder.id, {
        width: 1, height: 1, videoId,
        playerVars: { autoplay: 0, controls: 0, playsinline: 1 },
        events: {
          onReady: event => {
            event.target.cueVideoById(videoId);
            durationPoll = setInterval(() => {
              const duration = Number(event.target.getDuration());
              if (Number.isFinite(duration) && duration > 0) finish(duration);
            }, 250);
          },
          onError: () => finish(null),
        },
      });
    });
  }
  async function requestWithRetry(url, options, attempts = 5) {
    let lastError;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try { return await request(url, options); }
      catch (error) {
        lastError = error;
        if (attempt + 1 < attempts) await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** attempt));
      }
    }
    throw lastError;
  }
  async function resumeUpload(file) {
    try {
      const saved = JSON.parse(localStorage.getItem(uploadStorageKey) || "null");
      if (!saved || saved.name !== file.name || saved.size !== file.size || saved.modified !== file.lastModified) return null;
      return await request(`/api/uploads/${saved.upload_id}`);
    } catch {
      localStorage.removeItem(uploadStorageKey);
      return null;
    }
  }
  function chooseFile(file) { if (file) elements["file-name"].textContent = `${file.name} — ${formatBytes(file.size)}`; }
  function showUploadStatus(message, error = false) { elements["upload-status"].textContent = message; elements["upload-status"].classList.toggle("error", error); }
  function setButtonLoading(loading) { elements["upload-button"].disabled = loading; elements["upload-button"].textContent = loading ? "جارٍ الرفع…" : "رفع المحاضرة وبدء المعالجة"; }
  function setUrlLoading(loading) { elements["url-button"].disabled = loading; elements["url-button"].textContent = loading ? "جارٍ الإضافة…" : "تلخيص الرابط"; }
  function activateStep(target) { document.querySelectorAll(".step").forEach(step => step.classList.toggle("active", step.dataset.target === target)); }
  function updateExportActions() {
    elements["export-actions"].classList.toggle(
      "hidden", !currentJobId || !results[selectedResult],
    );
  }
  function formatBytes(bytes) { if (!bytes) return "0 B"; const units = ["B", "KB", "MB", "GB"]; const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3); return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`; }
  reconnectLatest();
})();
