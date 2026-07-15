(function () {
  const COLORS = { upload: "#246b54", download: "#c15f2e" };
  const NOT_MEASURED = "측정 안 함";
  const TELEMETRY_UNAVAILABLE = "운영체제에서 제공하지 않음";

  function formatSpeed(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return "-";
    return `${number.toFixed(1)} Mbps / ${(number / 8).toFixed(1)} MB/s`;
  }

  function formatBytes(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return "-";
    if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(1)} MB`;
    if (number >= 1024) return `${(number / 1024).toFixed(1)} KB`;
    return `${number.toFixed(0)} B`;
  }

  function directionLabel(direction) {
    return direction === "upload" ? "업로드" : "다운로드";
  }

  function directionPath(direction) {
    return direction === "upload" ? "측정 PC → 서버" : "서버 → 측정 PC";
  }

  function formatRtt(telemetry) {
    if (!telemetry || !telemetry.available) return TELEMETRY_UNAVAILABLE;
    const rtt = Number(telemetry.rtt_us) / 1000;
    const minimum = Number(telemetry.min_rtt_us) / 1000;
    if (!Number.isFinite(rtt) || !Number.isFinite(minimum)) return TELEMETRY_UNAVAILABLE;
    return `${rtt.toFixed(2)} ms (최소 ${minimum.toFixed(2)} ms)`;
  }

  function formatRetransmission(sender, telemetry) {
    if (!telemetry || !telemetry.available) return TELEMETRY_UNAVAILABLE;
    const retransmittedBytes = Number(telemetry.bytes_retrans);
    const sentBytes = Number(sender && sender.bytes);
    if (!Number.isFinite(retransmittedBytes) || retransmittedBytes < 0) return TELEMETRY_UNAVAILABLE;
    if (!Number.isFinite(sentBytes) || sentBytes <= 0) return formatBytes(retransmittedBytes);
    return `${formatBytes(retransmittedBytes)} (전체 송신량의 ${(retransmittedBytes / sentBytes * 100).toFixed(3)}%)`;
  }

  function formatDirectionValues(results, formatter) {
    return ["upload", "download"]
      .filter((direction) => results[direction])
      .map((direction) => `${directionLabel(direction)} ${formatter(results[direction])}`)
      .join(" · ") || NOT_MEASURED;
  }

  function formatDirectionDifference(results) {
    const upload = Number(results.upload && results.upload.receiver && results.upload.receiver.average_mbps);
    const download = Number(results.download && results.download.receiver && results.download.receiver.average_mbps);
    const maximum = Math.max(upload, download);
    if (!Number.isFinite(upload) || !Number.isFinite(download) || maximum <= 0) return "";
    return `업로드·다운로드 실제 수신 속도 차이: ${(Math.abs(upload - download) / maximum * 100).toFixed(3)}%`;
  }

  async function fetchJson(url, options, label) {
    let response;
    try {
      response = await fetch(url, { cache: "no-store", ...options });
    } catch (error) {
      throw new Error(`${label} 요청에 실패했습니다. (${error.message})`);
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `${label} 요청이 실패했습니다.`);
    return payload;
  }

  function initProbe() {
    const root = document.querySelector("[data-network-check]");
    if (!root || !root.dataset.probeStatusUrl) return;

    const serviceStatus = root.querySelector("[data-probe-service-status]");
    const packageLink = root.querySelector("[data-probe-client-package]");
    const packageAddress = root.querySelector("[data-probe-client-package-address]");
    const agentSelect = root.querySelector("[data-probe-agent]");
    const durationSelect = root.querySelector("[data-probe-duration]");
    const streamButtons = root.querySelectorAll("[data-probe-stream]");
    const actionButtons = root.querySelectorAll("[data-probe-action]");
    const cancelButton = root.querySelector("[data-probe-cancel]");
    const modeButtons = root.querySelectorAll("[data-measurement-mode]");
    const statusText = root.querySelector("[data-probe-status]");
    const phaseText = root.querySelector("[data-probe-phase]");
    const progressBar = root.querySelector("[data-probe-progress-bar]");
    const uploadText = root.querySelector("[data-probe-upload]");
    const downloadText = root.querySelector("[data-probe-download]");
    const rttText = root.querySelector("[data-probe-rtt]");
    const retransText = root.querySelector("[data-probe-retrans]");
    const conditionsText = root.querySelector("[data-probe-conditions]");
    const clientText = root.querySelector("[data-probe-client]");
    const resultList = root.querySelector("[data-probe-result-list]");
    const excelLink = root.querySelector("[data-probe-excel]");
    const criterionButtons = root.querySelectorAll("[data-http-criterion]");
    const chart = root.querySelector("[data-probe-chart]");

    let serviceAvailable = false;
    let running = false;
    let selectedStreams = 1;
    let activeSessionId = "";
    let graphSeries = { upload: [], download: [] };

    function setControlsEnabled() {
      const enabled = serviceAvailable && Boolean(agentSelect.value) && !running;
      agentSelect.disabled = !serviceAvailable || running;
      durationSelect.disabled = !enabled;
      streamButtons.forEach((button) => { button.disabled = !enabled; });
      actionButtons.forEach((button) => { button.disabled = !enabled; });
      modeButtons.forEach((button) => { button.disabled = running; });
      criterionButtons.forEach((button) => { button.disabled = running; });
      cancelButton.hidden = !running;
      cancelButton.disabled = !running;
      root.dataset.probeRunning = running ? "true" : "";
    }

    function resetResult() {
      statusText.textContent = "준비";
      phaseText.textContent = "TCP 전송 성능 측정 시작 대기";
      progressBar.style.width = "0%";
      uploadText.textContent = NOT_MEASURED;
      downloadText.textContent = NOT_MEASURED;
      rttText.textContent = "-";
      retransText.textContent = "-";
      conditionsText.textContent = "-";
      clientText.textContent = "-";
      resultList.innerHTML = "";
      excelLink.hidden = true;
      excelLink.removeAttribute("href");
      graphSeries = { upload: [], download: [] };
      drawChart();
    }

    function renderServiceStatus(payload) {
      serviceAvailable = Boolean(payload.available);
      serviceStatus.classList.toggle("warning", !serviceAvailable);
      serviceStatus.classList.toggle("success", serviceAvailable);
      if (!payload.enabled) {
        serviceStatus.textContent = "TCP 전송 성능 측정 비활성 · config.ini의 [network_probe] ENABLED=true 필요";
      } else if (!payload.available) {
        serviceStatus.textContent = payload.error || "TCP 측정 서버를 사용할 수 없습니다.";
      } else {
        serviceStatus.textContent = `TCP 측정 서버 정상 · 포트 ${payload.port}`;
      }
      const packageAvailable = Boolean(payload.client_package_available);
      packageLink.setAttribute("aria-disabled", packageAvailable ? "false" : "true");
      packageLink.classList.toggle("is-disabled", !packageAvailable);
      if (packageAvailable) {
        packageLink.href = payload.client_package_url || root.dataset.probeClientPackageUrl;
        packageAddress.textContent = `자동 연결 주소 · ${payload.client_package_server_url}`;
      } else {
        packageLink.removeAttribute("href");
        packageAddress.textContent = payload.client_package_error || "Windows 클라이언트 ZIP을 사용할 수 없습니다.";
      }
      setControlsEnabled();
    }

    async function refreshAgents() {
      try {
        const [status, agentsPayload] = await Promise.all([
          fetchJson(root.dataset.probeStatusUrl, {}, "TCP 서버 상태"),
          fetchJson(root.dataset.probeAgentsUrl, {}, "TCP 클라이언트 목록"),
        ]);
        renderServiceStatus(status);
        const previous = agentSelect.value;
        const agents = Array.isArray(agentsPayload.agents) ? agentsPayload.agents : [];
        agentSelect.innerHTML = "";
        if (!agents.length) {
          const option = document.createElement("option");
          option.value = "";
          option.textContent = "연결된 Windows 클라이언트 없음";
          agentSelect.appendChild(option);
        } else {
          agents.forEach((agent) => {
            const option = document.createElement("option");
            option.value = agent.agent_id;
            option.disabled = agent.status === "busy" && agent.agent_id !== previous;
            option.textContent = `${agent.hostname} · ${agent.client_ip}${agent.status === "busy" ? " · 측정 중" : ""}`;
            agentSelect.appendChild(option);
          });
          if (agents.some((agent) => agent.agent_id === previous)) agentSelect.value = previous;
        }
        setControlsEnabled();
      } catch (error) {
        serviceAvailable = false;
        serviceStatus.classList.remove("success");
        serviceStatus.classList.add("warning");
        serviceStatus.textContent = error.message;
        packageLink.removeAttribute("href");
        packageLink.setAttribute("aria-disabled", "true");
        packageLink.classList.add("is-disabled");
        packageAddress.textContent = error.message;
        setControlsEnabled();
      }
    }

    function stateLabel(payload) {
      const phase = payload.active_phase === "upload" ? "업로드" : payload.active_phase === "download" ? "다운로드" : "";
      return ({
        queued: "클라이언트 작업 대기",
        attaching: `${phase} TCP 연결 준비`,
        warmup: `${phase} 3초 워밍업`,
        running: `${phase} 본 측정`,
        awaiting_result: `${phase} 결과 집계`,
        completed: "TCP 전송 성능 측정 완료",
        cancelled: "TCP 전송 성능 측정 취소",
        failed: "TCP 전송 성능 측정 실패",
      })[payload.status] || payload.status;
    }

    function renderPhaseResult(direction, combined) {
      if (!combined || !combined.sender || !combined.receiver) return;
      const sender = combined.sender;
      const receiver = combined.receiver;
      const telemetry = sender.telemetry || {};
      const directionSpeedText = direction === "upload" ? uploadText : downloadText;
      directionSpeedText.textContent = formatSpeed(receiver.average_mbps);
      graphSeries[direction] = Array.isArray(receiver.intervals)
        ? receiver.intervals.map((item) => Number(item.mbps) || 0)
        : [];
      const item = document.createElement("div");
      item.className = "transfer-result";
      const title = document.createElement("h3");
      title.textContent = `${directionLabel(direction)} · ${directionPath(direction)}`;
      const details = document.createElement("dl");
      details.className = "transfer-result-details";
      [
        ["실제 수신 평균", formatSpeed(receiver.average_mbps)],
        ["1초 중앙값", formatSpeed(receiver.median_mbps)],
        ["1초 최소 속도", formatSpeed(receiver.min_mbps)],
        ["1초 최대 속도", formatSpeed(receiver.max_mbps)],
      ].forEach(([label, value]) => {
        const row = document.createElement("div");
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        term.textContent = label;
        description.textContent = value;
        row.append(term, description);
        details.appendChild(row);
      });
      item.append(title, details);
      resultList.appendChild(item);
    }

    function renderSession(payload) {
      statusText.textContent = "진행 중";
      phaseText.textContent = stateLabel(payload);
      progressBar.style.width = `${Number(payload.progress_percent || 0).toFixed(1)}%`;
      if (payload.agent) clientText.textContent = `${payload.agent.hostname} · ${payload.agent.client_ip}`;
      if (payload.requested) {
        conditionsText.textContent = `${Number(payload.requested.duration_seconds)}초 · TCP ${Number(payload.requested.stream_count)}개 스트림`;
      }
      resultList.innerHTML = "";
      graphSeries = { upload: [], download: [] };
      const results = payload.results || {};
      uploadText.textContent = NOT_MEASURED;
      downloadText.textContent = NOT_MEASURED;
      Object.entries(results).forEach(([direction, result]) => renderPhaseResult(direction, result));
      rttText.textContent = formatDirectionValues(
        results,
        (combined) => formatRtt((combined.sender || {}).telemetry)
      );
      retransText.textContent = formatDirectionValues(
        results,
        (combined) => formatRetransmission(combined.sender || {}, (combined.sender || {}).telemetry)
      );
      const difference = formatDirectionDifference(results);
      if (difference) {
        const comparison = document.createElement("p");
        comparison.className = "measurement-explanation probe-comparison";
        comparison.textContent = difference;
        resultList.appendChild(comparison);
      }
      drawChart();
      if (payload.error) phaseText.textContent = payload.error;
      if (payload.excel_url) {
        excelLink.href = payload.excel_url;
        excelLink.hidden = false;
        excelLink.setAttribute("download", "");
      }
      if (["completed", "cancelled", "failed"].includes(payload.status)) {
        statusText.textContent = payload.status === "completed" ? "완료" : payload.status === "cancelled" ? "취소" : "실패";
      }
    }

    async function pollSession() {
      while (running && activeSessionId) {
        const payload = await fetchJson(`${root.dataset.probeSessionsUrl}/${activeSessionId}`, {}, "TCP 측정 상태");
        renderSession(payload);
        if (["completed", "cancelled", "failed"].includes(payload.status)) {
          running = false;
          activeSessionId = "";
          setControlsEnabled();
          await refreshAgents();
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
    }

    async function startMeasurement(direction) {
      const durationSeconds = Number(durationSelect.value);
      if (!agentSelect.value) return;
      if ((durationSeconds === 30 || selectedStreams === 4 || direction === "full") &&
          !window.confirm("선택한 TCP 측정은 사내망 부하가 커질 수 있습니다. 시작할까요?")) return;
      resetResult();
      running = true;
      setControlsEnabled();
      statusText.textContent = "시작 중";
      try {
        const payload = await fetchJson(root.dataset.probeSessionsUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            agent_id: agentSelect.value,
            direction,
            duration_seconds: durationSeconds,
            stream_count: selectedStreams,
          }),
        }, "TCP 측정 시작");
        activeSessionId = payload.session_id;
        renderSession(payload);
        await pollSession();
      } catch (error) {
        running = false;
        activeSessionId = "";
        statusText.textContent = "실패";
        phaseText.textContent = error.message;
        setControlsEnabled();
      }
    }

    async function cancelMeasurement() {
      if (!activeSessionId) return;
      cancelButton.disabled = true;
      try {
        renderSession(await fetchJson(`${root.dataset.probeSessionsUrl}/${activeSessionId}/cancel`, {
          method: "POST",
        }, "TCP 측정 취소"));
      } catch (error) {
        phaseText.textContent = error.message;
      }
    }

    function drawChart() {
      if (!chart) return;
      const rect = chart.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(Math.floor(rect.width || 600), 320);
      const height = Math.max(Math.floor(rect.height || 280), 220);
      chart.width = Math.floor(width * ratio);
      chart.height = Math.floor(height * ratio);
      const context = chart.getContext("2d");
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      const padding = { left: 48, right: 18, top: 18, bottom: 32 };
      const plotWidth = width - padding.left - padding.right;
      const plotHeight = height - padding.top - padding.bottom;
      const values = [...graphSeries.upload, ...graphSeries.download];
      const maxValue = Math.max(10, ...values);
      context.strokeStyle = "#d8ddd3";
      context.fillStyle = "#626b5f";
      context.font = '12px "Segoe UI", sans-serif';
      for (let line = 0; line <= 4; line += 1) {
        const y = padding.top + (plotHeight * line) / 4;
        context.beginPath();
        context.moveTo(padding.left, y);
        context.lineTo(width - padding.right, y);
        context.stroke();
        context.fillText(`${Math.round(maxValue * (1 - line / 4))}`, 6, y + 4);
      }
      Object.entries(graphSeries).forEach(([direction, series]) => {
        if (!series.length) return;
        context.strokeStyle = COLORS[direction];
        context.lineWidth = 2;
        context.beginPath();
        series.forEach((value, index) => {
          const x = padding.left + (series.length === 1 ? 0 : (plotWidth * index) / (series.length - 1));
          const y = padding.top + plotHeight - (Number(value) / maxValue) * plotHeight;
          if (index === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.stroke();
      });
      context.fillText("Mbps", 6, 12);
      context.fillText("초", width - 24, height - 8);
    }

    streamButtons.forEach((button) => {
      button.addEventListener("click", () => {
        selectedStreams = Number(button.dataset.probeStream);
        streamButtons.forEach((item) => {
          const active = item === button;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
        });
      });
    });
    actionButtons.forEach((button) => button.addEventListener("click", () => startMeasurement(button.dataset.probeAction)));
    agentSelect.addEventListener("change", setControlsEnabled);
    packageLink.addEventListener("click", (event) => {
      if (packageLink.getAttribute("aria-disabled") === "true") event.preventDefault();
    });
    cancelButton.addEventListener("click", cancelMeasurement);
    window.addEventListener("resize", drawChart);
    window.setInterval(() => { if (!running) refreshAgents(); }, 3000);
    resetResult();
    refreshAgents();
  }

  document.addEventListener("DOMContentLoaded", initProbe);
})();
