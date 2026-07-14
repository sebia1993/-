(function () {
  const MB = 1024 * 1024;
  const MIN_UPLOAD_CHUNK = 256 * 1024;
  const TARGET_CHUNK_SECONDS = 0.25;
  const UPLOAD_CHUNK_STEP = 64 * 1024;
  const SERIES_COLORS = {
    upload: "#246b54",
    download: "#c15f2e",
  };

  function buildUrl(baseUrl, suffix) {
    const normalized = baseUrl.replace(/\/$/, "");
    const url = new URL(`${normalized}${suffix || ""}`, window.location.href);
    url.searchParams.set("_", `${Date.now()}-${Math.random()}`);
    return url.toString();
  }

  function formatSpeed(mbps) {
    if (!Number.isFinite(mbps) || mbps < 0) {
      return "-";
    }
    const digits = mbps >= 100 ? 1 : 2;
    return `${mbps.toFixed(digits)} Mbps / ${(mbps / 8).toFixed(digits)} MB/s`;
  }

  function median(values) {
    if (!values.length) {
      return 0;
    }
    const sorted = [...values].sort((left, right) => left - right);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  }

  function wait(milliseconds, signal) {
    return new Promise((resolve, reject) => {
      if (signal && signal.aborted) {
        reject(new Error("측정이 취소되었습니다."));
        return;
      }
      const abortHandler = () => {
        window.clearTimeout(timer);
        reject(new Error("측정이 취소되었습니다."));
      };
      const timer = window.setTimeout(() => {
        if (signal) {
          signal.removeEventListener("abort", abortHandler);
        }
        resolve();
      }, milliseconds);
      if (signal) {
        signal.addEventListener("abort", abortHandler, { once: true });
      }
    });
  }

  async function fetchJson(url, options, label) {
    let response;
    try {
      response = await fetch(url, options);
    } catch (error) {
      if (error.name === "AbortError") {
        throw new Error("측정이 취소되었습니다.");
      }
      throw new Error(`${label} 요청에 실패했습니다. (${error.message})`);
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `${label} 요청이 실패했습니다.`);
    }
    return payload;
  }

  function initSustainedCheck() {
    const root = document.querySelector("[data-network-check]");
    if (!root || !root.dataset.sustainedSessionUrl) {
      return;
    }

    const modeButtons = root.querySelectorAll("[data-measurement-mode]");
    const controlPanels = root.querySelectorAll("[data-measurement-control]");
    const resultPanels = root.querySelectorAll("[data-measurement-result]");
    const durationSelect = root.querySelector("[data-sustained-duration]");
    const streamButtons = root.querySelectorAll("[data-sustained-stream]");
    const actionButtons = root.querySelectorAll("[data-sustained-action]");
    const cancelButton = root.querySelector("[data-sustained-cancel]");
    const statusText = root.querySelector("[data-sustained-status]");
    const phaseText = root.querySelector("[data-sustained-phase]");
    const progressBar = root.querySelector("[data-sustained-progress-bar]");
    const latencyText = root.querySelector("[data-sustained-latency]");
    const averageText = root.querySelector("[data-sustained-average]");
    const currentText = root.querySelector("[data-sustained-current]");
    const rangeText = root.querySelector("[data-sustained-range]");
    const medianText = root.querySelector("[data-sustained-median]");
    const variabilityText = root.querySelector("[data-sustained-variability]");
    const resultList = root.querySelector("[data-sustained-result-list]");
    const excelLink = root.querySelector("[data-sustained-excel]");
    const chart = root.querySelector("[data-sustained-chart]");

    let selectedStreams = 1;
    let running = false;
    let activeController = null;
    let activeSessionId = "";
    let cancellationRequested = false;
    let latencySamples = [];
    let clientResults = {};
    const graphSeries = { upload: [], download: [] };

    function setMeasurementMode(mode) {
      if (running || root.dataset.simpleRunning === "true" || root.dataset.probeRunning === "true") {
        return;
      }
      modeButtons.forEach((button) => {
        const active = button.dataset.measurementMode === mode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
      controlPanels.forEach((panel) => {
        panel.hidden = panel.dataset.measurementControl !== mode;
      });
      resultPanels.forEach((panel) => {
        panel.hidden = panel.dataset.measurementResult !== mode;
      });
      if (mode === "sustained") {
        window.requestAnimationFrame(drawChart);
      }
    }

    function setRunning(nextRunning) {
      running = nextRunning;
      root.dataset.sustainedRunning = nextRunning ? "true" : "";
      actionButtons.forEach((button) => {
        button.disabled = nextRunning;
      });
      streamButtons.forEach((button) => {
        button.disabled = nextRunning;
      });
      modeButtons.forEach((button) => {
        button.disabled = nextRunning;
      });
      durationSelect.disabled = nextRunning;
      cancelButton.disabled = !nextRunning;
      cancelButton.hidden = !nextRunning;
    }

    function resetResult() {
      statusText.textContent = "준비";
      phaseText.textContent = "HTTP 응답시간 측정 준비";
      progressBar.style.width = "0%";
      latencyText.textContent = "-";
      averageText.textContent = "-";
      currentText.textContent = "-";
      rangeText.textContent = "-";
      medianText.textContent = "-";
      variabilityText.textContent = "-";
      resultList.innerHTML = "";
      excelLink.hidden = true;
      excelLink.removeAttribute("href");
      latencySamples = [];
      clientResults = {};
      graphSeries.upload = [];
      graphSeries.download = [];
      drawChart();
    }

    function setPhase(label, elapsedSeconds, durationSeconds) {
      phaseText.textContent = label;
      const percent = durationSeconds > 0 ? Math.min(100, (elapsedSeconds / durationSeconds) * 100) : 0;
      progressBar.style.width = `${percent.toFixed(1)}%`;
    }

    async function measureLatency(signal) {
      statusText.textContent = "응답시간 측정 중";
      phaseText.textContent = "HTTP 응답시간 확인";
      const samples = [];
      for (let index = 0; index < 6; index += 1) {
        const startedAt = performance.now();
        await fetchJson(
          buildUrl(root.dataset.sustainedLatencyUrl),
          { cache: "no-store", signal },
          "HTTP 응답시간"
        );
        const elapsed = performance.now() - startedAt;
        if (index > 0) {
          samples.push(elapsed);
        }
      }
      latencyText.textContent = `${median(samples).toFixed(2)} ms`;
      return samples;
    }

    async function beginPhase(direction, phase, signal) {
      return fetchJson(
        buildUrl(root.dataset.sustainedSessionUrl, `/${encodeURIComponent(activeSessionId)}/phase`),
        {
          method: "POST",
          cache: "no-store",
          signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ direction, phase }),
        },
        "측정 단계 시작"
      );
    }

    async function getStatus(signal) {
      return fetchJson(
        buildUrl(root.dataset.sustainedSessionUrl, `/${encodeURIComponent(activeSessionId)}/status`),
        { cache: "no-store", signal },
        "측정 상태"
      );
    }

    function appendLiveSample(direction, byteDelta, elapsedSeconds) {
      if (byteDelta < 0 || elapsedSeconds <= 0) {
        return;
      }
      const mbps = (byteDelta * 8) / elapsedSeconds / 1_000_000;
      graphSeries[direction].push({
        index: graphSeries[direction].length + 1,
        bytes_transferred: byteDelta,
        mbps,
      });
      currentText.textContent = formatSpeed(mbps);
      drawChart();
    }

    async function runUploadPhase(phase, chunkSize, signal) {
      const phaseInfo = await beginPhase("upload", phase, signal);
      const durationSeconds = Number(phaseInfo.duration_seconds);
      const startedAt = performance.now();
      const deadline = startedAt + durationSeconds * 1000;
      const body = new Uint8Array(chunkSize);
      const phaseLabel = phase === "warmup" ? "업로드 워밍" : "업로드 본 측정";
      let workersFinished = false;
      let previousBytes = 0;
      let previousSampleAt = startedAt;

      async function worker(streamId) {
        while (performance.now() < deadline) {
          await fetchJson(
            buildUrl(
              root.dataset.sustainedSessionUrl,
              `/${encodeURIComponent(activeSessionId)}/upload/${streamId}`
            ),
            {
              method: "POST",
              body,
              cache: "no-store",
              signal,
              headers: { "Content-Type": "application/octet-stream" },
            },
            `업로드 HTTP 연결 ${streamId + 1}`
          );
        }
      }

      const workers = Promise.all(Array.from({ length: selectedStreams }, (_, index) => worker(index))).finally(() => {
        workersFinished = true;
      });

      while (!workersFinished) {
        await Promise.race([workers, wait(500, signal)]);
        const status = await getStatus(signal);
        const now = performance.now();
        const elapsed = Math.min((now - startedAt) / 1000, durationSeconds);
        setPhase(`${phaseLabel} ${elapsed.toFixed(1)} / ${durationSeconds.toFixed(0)}초`, elapsed, durationSeconds);
        if (phase === "measure" && now - previousSampleAt >= 900) {
          const phaseBytes = Number(status.phase_bytes);
          appendLiveSample("upload", phaseBytes - previousBytes, (now - previousSampleAt) / 1000);
          previousBytes = phaseBytes;
          previousSampleAt = now;
          averageText.textContent = formatSpeed((phaseBytes * 8) / Math.max(elapsed, 0.001) / 1_000_000);
        }
      }
      await workers;
      const finalStatus = await getStatus(signal);
      const endedAt = performance.now();
      if (phase === "measure" && endedAt - previousSampleAt >= 250) {
        appendLiveSample(
          "upload",
          Number(finalStatus.phase_bytes) - previousBytes,
          (endedAt - previousSampleAt) / 1000
        );
      }
      setPhase(`${phaseLabel} 완료`, durationSeconds, durationSeconds);
      return {
        bytes_transferred: Number(finalStatus.phase_bytes),
        actual_duration_seconds: durationSeconds,
        intervals: graphSeries.upload.map((point) => ({ bytes_transferred: point.bytes_transferred })),
      };
    }

    async function runDownloadPhase(phase, signal) {
      const phaseInfo = await beginPhase("download", phase, signal);
      const durationSeconds = Number(phaseInfo.duration_seconds);
      const startedAt = performance.now();
      const phaseLabel = phase === "warmup" ? "다운로드 워밍" : "다운로드 본 측정";
      let totalBytes = 0;
      let previousBytes = 0;
      let previousSampleAt = startedAt;
      if (phase === "measure") {
        clientResults.download = {
          bytes_transferred: 0,
          actual_duration_seconds: 0.001,
          intervals: [],
        };
      }

      async function worker(streamId) {
        const response = await fetch(
          buildUrl(
            root.dataset.sustainedSessionUrl,
            `/${encodeURIComponent(activeSessionId)}/download/${streamId}`
          ),
          { cache: "no-store", signal }
        );
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.error || `다운로드 HTTP 연결 ${streamId + 1}이 실패했습니다.`);
        }
        if (!response.body) {
          throw new Error("현재 브라우저는 스트리밍 다운로드를 지원하지 않습니다.");
        }
        const reader = response.body.getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }
          totalBytes += value.byteLength;
          const now = performance.now();
          const elapsed = Math.min((now - startedAt) / 1000, durationSeconds);
          if (phase === "measure") {
            clientResults.download.bytes_transferred = totalBytes;
            clientResults.download.actual_duration_seconds = Math.max(elapsed, 0.001);
          }
          setPhase(`${phaseLabel} ${elapsed.toFixed(1)} / ${durationSeconds.toFixed(0)}초`, elapsed, durationSeconds);
          if (phase === "measure" && now - previousSampleAt >= 900) {
            appendLiveSample("download", totalBytes - previousBytes, (now - previousSampleAt) / 1000);
            clientResults.download.intervals = graphSeries.download.map((point) => ({
              bytes_transferred: point.bytes_transferred,
            }));
            previousBytes = totalBytes;
            previousSampleAt = now;
            averageText.textContent = formatSpeed((totalBytes * 8) / Math.max(elapsed, 0.001) / 1_000_000);
          }
        }
      }

      await Promise.all(Array.from({ length: selectedStreams }, (_, index) => worker(index)));
      const endedAt = performance.now();
      if (phase === "measure" && endedAt - previousSampleAt >= 250) {
        appendLiveSample("download", totalBytes - previousBytes, (endedAt - previousSampleAt) / 1000);
      }
      setPhase(`${phaseLabel} 완료`, durationSeconds, durationSeconds);
      const completedResult = {
        bytes_transferred: totalBytes,
        actual_duration_seconds: Math.max((endedAt - startedAt) / 1000, 0.001),
        intervals: graphSeries.download.map((point) => ({ bytes_transferred: point.bytes_transferred })),
      };
      if (phase === "measure") {
        clientResults.download = completedResult;
      }
      return completedResult;
    }

    function chooseUploadChunk(warmupBytes, warmupSeconds, maxChunkBytes) {
      const estimatedPerStreamBytesPerSecond = warmupBytes / Math.max(warmupSeconds, 0.001) / selectedStreams;
      const targetBytes = estimatedPerStreamBytesPerSecond * TARGET_CHUNK_SECONDS;
      const clamped = Math.max(MIN_UPLOAD_CHUNK, Math.min(maxChunkBytes, targetBytes));
      return Math.max(MIN_UPLOAD_CHUNK, Math.round(clamped / UPLOAD_CHUNK_STEP) * UPLOAD_CHUNK_STEP);
    }

    async function runDirection(direction, session, signal) {
      if (direction === "upload") {
        const warmup = await runUploadPhase("warmup", MB, signal);
        const chunkSize = chooseUploadChunk(
          warmup.bytes_transferred,
          Number(session.warmup_seconds),
          Number(session.max_upload_chunk_bytes)
        );
        const measured = await runUploadPhase("measure", chunkSize, signal);
        clientResults.upload = measured;
        return;
      }
      await runDownloadPhase("warmup", signal);
      clientResults.download = await runDownloadPhase("measure", signal);
    }

    function renderCompletedResult(result) {
      resultList.innerHTML = "";
      Object.entries(result.directions || {}).forEach(([direction, summary]) => {
        const item = document.createElement("div");
        item.className = "result-item";
        const label = direction === "upload" ? "업로드" : "다운로드";
        item.textContent = `${label}: ${formatSpeed(Number(summary.average_mbps))} · 중앙 ${formatSpeed(Number(summary.median_mbps))} · 변동 ${Number(summary.variability_percent).toFixed(1)}%`;
        resultList.appendChild(item);
      });
      const summaries = Object.values(result.directions || {});
      if (summaries.length) {
        const averageValues = summaries.map((summary) => Number(summary.average_mbps));
        const minimumValues = summaries.map((summary) => Number(summary.min_mbps));
        const maximumValues = summaries.map((summary) => Number(summary.max_mbps));
        const medianValues = summaries.map((summary) => Number(summary.median_mbps));
        const variabilityValues = summaries.map((summary) => Number(summary.variability_percent));
        averageText.textContent = averageValues.map(formatSpeed).join(" / ");
        rangeText.textContent = `${formatSpeed(Math.min(...minimumValues))} / ${formatSpeed(Math.max(...maximumValues))}`;
        medianText.textContent = medianValues.map(formatSpeed).join(" / ");
        variabilityText.textContent = `${Math.max(...variabilityValues).toFixed(1)}%`;
      }
      if (
        result.http_latency &&
        result.http_latency.median_ms !== null &&
        Number.isFinite(Number(result.http_latency.median_ms))
      ) {
        latencyText.textContent = `${Number(result.http_latency.median_ms).toFixed(2)} ms`;
      }
      if (result.excel_url) {
        excelLink.href = new URL(result.excel_url, window.location.href).toString();
        excelLink.hidden = false;
        excelLink.setAttribute("download", "");
      }
      progressBar.style.width = "100%";
      drawChart();
    }

    async function finalizeSession(path, payload) {
      return fetchJson(
        buildUrl(root.dataset.sustainedSessionUrl, `/${encodeURIComponent(activeSessionId)}/${path}`),
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        "측정 결과 저장"
      );
    }

    async function runAction(direction) {
      if (running) {
        return;
      }
      const durationSeconds = Number.parseInt(durationSelect.value, 10);
      if (
        (durationSeconds === 30 || selectedStreams === 4) &&
        !window.confirm(`${durationSeconds}초 또는 ${selectedStreams}개 HTTP 연결 측정은 사내망과 서버 PC에 부하를 줄 수 있습니다. 계속할까요?`)
      ) {
        return;
      }

      resetResult();
      setRunning(true);
      activeController = new AbortController();
      cancellationRequested = false;

      try {
        latencySamples = await measureLatency(activeController.signal);
        const session = await fetchJson(
          buildUrl(root.dataset.sustainedSessionUrl),
          {
            method: "POST",
            cache: "no-store",
            signal: activeController.signal,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              direction,
              duration_seconds: durationSeconds,
              stream_count: selectedStreams,
            }),
          },
          "지속 측정 시작"
        );
        activeSessionId = session.session_id;
        statusText.textContent = "측정 중";

        if (direction === "upload" || direction === "full") {
          await runDirection("upload", session, activeController.signal);
        }
        if (direction === "download" || direction === "full") {
          await runDirection("download", session, activeController.signal);
        }

        const completed = await finalizeSession("complete", {
          status: "success",
          latency_samples_ms: latencySamples,
          results: clientResults,
        });
        statusText.textContent = "완료";
        phaseText.textContent = "HTTP 지속 측정 완료";
        renderCompletedResult(completed);
      } catch (error) {
        let savedResult = null;
        if (activeSessionId) {
          try {
            savedResult = await finalizeSession(cancellationRequested ? "cancel" : "complete", {
              status: cancellationRequested ? "cancelled" : "failure",
              error: error.message || "측정 중 오류가 발생했습니다.",
              latency_samples_ms: latencySamples,
              results: clientResults,
            });
          } catch (_) {
            savedResult = null;
          }
        }
        statusText.textContent = cancellationRequested ? "취소됨" : "실패";
        phaseText.textContent = error.message || "측정 중 오류가 발생했습니다.";
        if (savedResult) {
          renderCompletedResult(savedResult);
        }
      } finally {
        activeSessionId = "";
        activeController = null;
        setRunning(false);
      }
    }

    function drawChart() {
      if (!chart || chart.hidden || chart.clientWidth <= 0) {
        return;
      }
      const context = chart.getContext("2d");
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(chart.clientWidth, 320);
      const height = Math.max(chart.clientHeight, 230);
      if (chart.width !== Math.round(width * ratio) || chart.height !== Math.round(height * ratio)) {
        chart.width = Math.round(width * ratio);
        chart.height = Math.round(height * ratio);
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      const padding = { top: 18, right: 16, bottom: 30, left: 54 };
      const plotWidth = width - padding.left - padding.right;
      const plotHeight = height - padding.top - padding.bottom;
      const points = [...graphSeries.upload, ...graphSeries.download];
      const maximum = Math.max(10, ...points.map((point) => point.mbps));
      const maximumCount = Math.max(1, graphSeries.upload.length, graphSeries.download.length);

      context.strokeStyle = "#d8ddd3";
      context.fillStyle = "#626b5f";
      context.font = "12px Segoe UI, Arial, sans-serif";
      context.lineWidth = 1;
      for (let row = 0; row <= 4; row += 1) {
        const y = padding.top + (plotHeight * row) / 4;
        context.beginPath();
        context.moveTo(padding.left, y);
        context.lineTo(width - padding.right, y);
        context.stroke();
        const value = maximum * (1 - row / 4);
        context.fillText(`${value.toFixed(value >= 100 ? 0 : 1)}`, 6, y + 4);
      }
      context.fillText("Mbps", 6, 12);
      context.fillText("1초 구간", width - 64, height - 8);

      Object.entries(graphSeries).forEach(([direction, series]) => {
        if (!series.length) {
          return;
        }
        context.strokeStyle = SERIES_COLORS[direction];
        context.lineWidth = 2.5;
        context.beginPath();
        series.forEach((point, index) => {
          const x = padding.left + (plotWidth * index) / Math.max(maximumCount - 1, 1);
          const y = padding.top + plotHeight - (plotHeight * point.mbps) / maximum;
          if (index === 0) {
            context.moveTo(x, y);
          } else {
            context.lineTo(x, y);
          }
        });
        context.stroke();
      });
    }

    modeButtons.forEach((button) => {
      button.addEventListener("click", () => setMeasurementMode(button.dataset.measurementMode));
    });
    streamButtons.forEach((button) => {
      button.addEventListener("click", () => {
        if (running) {
          return;
        }
        selectedStreams = Number.parseInt(button.dataset.sustainedStream, 10);
        streamButtons.forEach((item) => {
          const active = item === button;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
        });
      });
    });
    actionButtons.forEach((button) => {
      button.addEventListener("click", () => runAction(button.dataset.sustainedAction));
    });
    cancelButton.addEventListener("click", () => {
      cancellationRequested = true;
      if (activeController) {
        activeController.abort();
      }
    });
    window.addEventListener("resize", drawChart);
    setMeasurementMode("simple");
  }

  initSustainedCheck();
})();
