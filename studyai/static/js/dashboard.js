(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const fileInput = document.querySelector("#audio-file");
  const dropZone = document.querySelector("#drop-zone");
  const fileName = document.querySelector("#file-name");
  const uploadButton = document.querySelector("#upload-button");
  const status = document.querySelector("#upload-status");
  const transcriptPanel = document.querySelector("#transcript-panel");
  const resultsPanel = document.querySelector("#results-panel");
  const lectureText = document.querySelector("#lecture-text");

  const chooseFile = (file) => { if (file) fileName.textContent = file.name; };
  fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
  ["dragenter", "dragover"].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.remove("dragging"); }));
  dropZone.addEventListener("drop", event => { if (event.dataTransfer.files.length) { fileInput.files = event.dataTransfer.files; chooseFile(fileInput.files[0]); } });

  uploadButton.addEventListener("click", async () => {
    const file = fileInput.files[0];
    if (!file) return showStatus("اختر ملفًا أولًا.", true);
    const form = new FormData(); form.append("audio", file);
    await withLoading(uploadButton, "جاري التحويل…", async () => {
      showStatus("يجري رفع المحاضرة وتحويلها. قد يستغرق ذلك عدة دقائق.");
      const data = await request("/api/transcriptions", { method: "POST", body: form });
      lectureText.value = data.text; transcriptPanel.classList.remove("hidden");
      showStatus("تم تحويل المحاضرة بنجاح."); activateStep("transcript-panel");
      transcriptPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  document.querySelector("#copy-button").addEventListener("click", async event => {
    if (!lectureText.value.trim()) return;
    await navigator.clipboard.writeText(lectureText.value);
    const original = event.currentTarget.textContent; event.currentTarget.textContent = "تم النسخ ✓";
    setTimeout(() => { event.currentTarget.textContent = original; }, 1500);
  });
  document.querySelector("#summary-button").addEventListener("click", event => runTextOperation(event.currentTarget, "/api/summaries", "ملخص المحاضرة"));
  document.querySelector("#questions-button").addEventListener("click", event => runTextOperation(event.currentTarget, "/api/questions", "أسئلة المراجعة"));
  document.querySelectorAll(".step").forEach(step => step.addEventListener("click", () => document.querySelector(`#${step.dataset.target}`).scrollIntoView({ behavior: "smooth" })));

  async function runTextOperation(button, url, title) {
    if (lectureText.value.trim().length < 20) return showStatus("أضف نص المحاضرة أولًا.", true);
    await withLoading(button, "جاري الإنشاء…", async () => {
      const data = await request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: lectureText.value }) });
      document.querySelector("#result-title").textContent = title;
      document.querySelector("#result-content").textContent = data.result;
      resultsPanel.classList.remove("hidden"); activateStep("results-panel");
      resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
  async function request(url, options) {
    options.headers = { ...(options.headers || {}), "X-CSRF-Token": csrf };
    const response = await fetch(url, options); let data;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) throw new Error(data.error || "تعذر إكمال الطلب.");
    return data;
  }
  async function withLoading(button, label, task) {
    const original = button.textContent; button.disabled = true; button.textContent = label;
    try { await task(); } catch (error) { showStatus(error.message, true); } finally { button.disabled = false; button.textContent = original; }
  }
  function showStatus(message, error = false) { status.textContent = message; status.classList.toggle("error", error); }
  function activateStep(target) { document.querySelectorAll(".step").forEach(step => step.classList.toggle("active", step.dataset.target === target)); }
})();
