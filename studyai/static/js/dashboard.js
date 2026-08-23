(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const elements = Object.fromEntries([
    "audio-file", "drop-zone", "file-name", "upload-button", "lecture-url", "url-button", "upload-status",
    "progress-panel", "progress-percent", "progress-bar", "stage-message",
    "segment-progress", "retry-button", "results-panel", "result-content", "copy-button",
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
          body: JSON.stringify({ filename: file.name, total_size: file.size }),
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
      const queued = await request(`/api/uploads/${upload.upload_id}/complete`, { method: "POST" });
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
      const queued = await request("/api/uploads/url", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
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
    elements["result-content"].textContent = results[selectedResult] || "";
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
  function formatBytes(bytes) { if (!bytes) return "0 B"; const units = ["B", "KB", "MB", "GB"]; const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3); return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`; }
  reconnectLatest();
})();
