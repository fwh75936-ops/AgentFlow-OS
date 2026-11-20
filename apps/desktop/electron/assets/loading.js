// Loading gate: poll the main process for the backend's real state.
// "ready" is handled by the main process (it swaps in the real UI);
// here we only render starting / failed honestly.
(function () {
  var statusEl = document.getElementById("status");
  var spinner = document.getElementById("spinner");
  var box = document.getElementById("error-box");
  var errText = document.getElementById("error-text");
  var errLog = document.getElementById("error-log");
  var retry = document.getElementById("retry");

  retry.addEventListener("click", function () {
    box.style.display = "none";
    retry.style.display = "none";
    spinner.style.display = "block";
    statusEl.textContent = "正在重新启动后端服务…";
    if (window.agentflow && window.agentflow.retryBackend) {
      window.agentflow.retryBackend();
    }
  });

  function poll() {
    if (!window.agentflow || !window.agentflow.backendStatus) return;
    window.agentflow.backendStatus().then(function (s) {
      if (!s) return;
      if (s.status === "failed") {
        spinner.style.display = "none";
        statusEl.textContent = "后端服务没有启动起来，应用暂时无法使用。";
        errText.textContent = s.error || "未知错误";
        errLog.textContent = (s.log || []).join("\n");
        box.style.display = "block";
        retry.style.display = "block";
      } else if (s.status === "starting" || s.status === "idle") {
        statusEl.textContent = "正在启动后端服务…";
      }
    }).catch(function () { /* next poll retries */ });
  }
  poll();
  setInterval(poll, 700);
})();
