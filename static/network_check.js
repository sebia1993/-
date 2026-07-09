(function () {
  const MB = 1024 * 1024;
  const CHUNK_SIZE = MB;

  function initTabs() {
    const buttons = document.querySelectorAll("[data-tab-button]");
    const panels = document.querySelectorAll("[data-tab-panel]");

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const targetId = button.dataset.tabButton;
        buttons.forEach((item) => {
          item.classList.toggle("is-active", item === button);
        });
        panels.forEach((panel) => {
          const active = panel.id === targetId;
          panel.classList.toggle("is-active", active);
          panel.hidden = !active;
        });
      });
    });
  }

  function formatSpeed(mbps) {
    if (!Number.isFinite(mbps) || mbps <= 0) {
      return "-";
    }
    const mbpsText = mbps >= 100 ? mbps.toFixed(1) : mbps.toFixed(2);
    const megaBytesPerSecond = mbps / 8;
    const mbText = megaBytesPerSecond >= 100 ? megaBytesPerSecond.toFixed(1) : megaBytesPerSecond.toFixed(2);
    return `${mbpsText} Mbps / ${mbText} MB/s`;
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) {
      return "-";
    }
    return `${seconds.toFixed(2)}초`;
  }

  function buildUrl(baseUrl, params) {
    const url = new URL(baseUrl, window.location.href);
    Object.entries(params || {}).forEach(([key, value]) => {
      url.searchParams.set(key, String(value));
    });
    url.searchParams.set("_", `${Date.now()}-${Math.random()}`);
    return url.toString();
  }

  function initNetworkCheck() {
    const root = document.querySelector("[data-network-check]");
    if (!root) {
      return;
    }

    const sizeSelect = root.querySelector("[data-network-size]");
    const actionButtons = root.querySelectorAll("[data-check-action]");
    const cancelButton = root.querySelector("[data-cancel-check]");
    const statusText = root.querySelector("[data-network-status]");
    const progressBar = root.querySelector("[data-progress-bar]");
    const progressText = root.querySelector("[data-progress-text]");
    const averageSpeed = root.querySelector("[data-average-speed]");
    const intervalSpeed = root.querySelector("[data-interval-speed]");
    const summary = root.querySelector("[data-summary]");
    const resultList = root.querySelector("[data-result-list]");

    let running = false;
    let activeController = null;
    let lastProgressBytes = 0;
    let lastProgressAt = 0;

    function setRunning(nextRunning) {
      running = nextRunning;
      actionButtons.forEach((button) => {
        button.disabled = nextRunning;
      });
      cancelButton.disabled = !nextRunning;
      cancelButton.hidden = !nextRunning;
      sizeSelect.disabled = nextRunning;
    }

    function setStatus(message) {
      statusText.textContent = message;
    }

    function resetProgress(message) {
      setStatus(message);
      progressBar.style.width = "0%";
      progressText.textContent = "0%";
      averageSpeed.textContent = "-";
      intervalSpeed.textContent = "-";
      summary.textContent = "-";
      lastProgressBytes = 0;
      lastProgressAt = performance.now();
    }

    function updateProgress(bytesDone, totalBytes, startedAt) {
      const now = performance.now();
      const elapsedSeconds = Math.max((now - startedAt) / 1000, 0.001);
      const percent = Math.min(100, (bytesDone / totalBytes) * 100);
      const averageMbps = (bytesDone * 8) / elapsedSeconds / 1_000_000;
      const intervalSeconds = Math.max((now - lastProgressAt) / 1000, 0.001);
      const intervalBytes = Math.max(0, bytesDone - lastProgressBytes);
      const intervalMbps = (intervalBytes * 8) / intervalSeconds / 1_000_000;
      progressBar.style.width = `${percent.toFixed(1)}%`;
      progressText.textContent = `${percent.toFixed(1)}%`;
      averageSpeed.textContent = formatSpeed(averageMbps);
      intervalSpeed.textContent = formatSpeed(intervalMbps);
      lastProgressBytes = bytesDone;
      lastProgressAt = now;
    }

    function completeResult(label, bytesDone, totalBytes, startedAt) {
      const elapsedSeconds = Math.max((performance.now() - startedAt) / 1000, 0.001);
      const mbps = (bytesDone * 8) / elapsedSeconds / 1_000_000;
      updateProgress(bytesDone, totalBytes, startedAt);
      return {
        label,
        sizeMb: totalBytes / MB,
        bytesDone,
        elapsedSeconds,
        mbps,
      };
    }

    function renderResults(results) {
      resultList.innerHTML = "";
      results.forEach((result) => {
        const item = document.createElement("div");
        item.className = "result-item";
        item.textContent = `${result.label}: ${formatSpeed(result.mbps)} · ${formatDuration(result.elapsedSeconds)} · ${result.sizeMb}MB`;
        resultList.appendChild(item);
      });
      summary.textContent = results
        .map((result) => `${result.label} ${formatSpeed(result.mbps)}`)
        .join(" / ");
    }

    function makeUploadChunk() {
      const chunk = new Uint8Array(CHUNK_SIZE);
      for (let index = 0; index < chunk.length; index += 1) {
        chunk[index] = index % 251;
      }
      return chunk;
    }

    async function fetchJson(url, options, label) {
      let response;
      try {
        response = await fetch(url, options);
      } catch (error) {
        if (error.name === "AbortError") {
          throw new Error("측정이 취소되었습니다.");
        }
        throw new Error(`${label} 요청에 실패했습니다. 서버 주소, 방화벽, 브라우저 연결을 확인하세요. (${error.message})`);
      }

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || `${label} 요청이 실패했습니다.`);
      }
      return payload;
    }

    async function runDownload(sizeMb, signal) {
      const totalBytes = sizeMb * MB;
      const startedAt = performance.now();
      let receivedBytes = 0;

      resetProgress("다운로드 측정 중");
      let response;
      try {
        response = await fetch(buildUrl(root.dataset.downloadUrl, { size_mb: sizeMb }), {
          cache: "no-store",
          signal,
        });
      } catch (error) {
        if (error.name === "AbortError") {
          throw new Error("측정이 취소되었습니다.");
        }
        throw new Error(`다운로드 측정 요청에 실패했습니다. 서버 주소, 방화벽, 브라우저 연결을 확인하세요. (${error.message})`);
      }
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || "다운로드 측정 요청이 실패했습니다.");
      }
      if (!response.body) {
        throw new Error("현재 브라우저는 스트리밍 다운로드를 지원하지 않습니다.");
      }

      const reader = response.body.getReader();
      while (true) {
        let result;
        try {
          result = await reader.read();
        } catch (error) {
          if (error.name === "AbortError") {
            throw new Error("측정이 취소되었습니다.");
          }
          throw error;
        }
        const { done, value } = result;
        if (done) {
          break;
        }
        receivedBytes += value.byteLength;
        updateProgress(receivedBytes, totalBytes, startedAt);
      }

      return completeResult("다운로드", receivedBytes, totalBytes, startedAt);
    }

    async function runUpload(sizeMb, signal) {
      const totalBytes = sizeMb * MB;
      const startedAt = performance.now();
      const uploadBaseUrl = root.dataset.uploadUrl;
      const chunk = makeUploadChunk();
      let sessionId = "";
      let sentBytes = 0;

      resetProgress("업로드 측정 중");
      try {
        const started = await fetchJson(
          buildUrl(`${uploadBaseUrl}/start`, { size_mb: sizeMb }),
          { method: "POST", cache: "no-store", signal },
          "업로드 시작"
        );
        sessionId = started.session_id;

        while (sentBytes < totalBytes) {
          const remaining = totalBytes - sentBytes;
          const nextSize = Math.min(CHUNK_SIZE, remaining);
          const body = nextSize === CHUNK_SIZE ? chunk : chunk.subarray(0, nextSize);
          const chunkNumber = Math.floor(sentBytes / CHUNK_SIZE) + 1;
          const chunkResult = await fetchJson(
            buildUrl(`${uploadBaseUrl}/chunk/${encodeURIComponent(sessionId)}`),
            {
              method: "POST",
              body,
              cache: "no-store",
              signal,
              headers: {
                "Content-Type": "application/octet-stream",
              },
            },
            `업로드 ${chunkNumber}번째 조각`
          );
          sentBytes = Number(chunkResult.bytes_received);
          updateProgress(sentBytes, totalBytes, startedAt);
        }

        const finished = await fetchJson(
          buildUrl(`${uploadBaseUrl}/finish/${encodeURIComponent(sessionId)}`),
          { method: "POST", cache: "no-store", signal },
          "업로드 완료"
        );
        updateProgress(totalBytes, totalBytes, startedAt);
        return {
          label: "업로드",
          sizeMb,
          bytesDone: Number(finished.bytes_transferred),
          elapsedSeconds: Number(finished.duration_seconds),
          mbps: Number(finished.mbps),
        };
      } catch (error) {
        if (sessionId) {
          await fetch(buildUrl(`${uploadBaseUrl}/finish/${encodeURIComponent(sessionId)}`), {
            method: "POST",
            cache: "no-store",
          }).catch(() => {});
        }
        throw error;
      }
    }

    async function runAction(action) {
      if (running) {
        return;
      }

      const sizeMb = Number.parseInt(sizeSelect.value, 10);
      if (sizeMb === 1024 && !window.confirm("1024MB 측정은 사내망과 서버 PC에 부하를 줄 수 있습니다. 계속 진행할까요?")) {
        return;
      }

      const results = [];
      activeController = new AbortController();
      setRunning(true);

      try {
        if (action === "upload" || action === "full") {
          results.push(await runUpload(sizeMb, activeController.signal));
        }
        if (action === "download" || action === "full") {
          results.push(await runDownload(sizeMb, activeController.signal));
        }
        setStatus("완료");
        renderResults(results);
      } catch (error) {
        setStatus(error.message === "측정이 취소되었습니다." ? "취소됨" : "실패");
        summary.textContent = error.message || "측정 중 오류가 발생했습니다.";
      } finally {
        activeController = null;
        setRunning(false);
      }
    }

    actionButtons.forEach((button) => {
      button.addEventListener("click", () => {
        runAction(button.dataset.checkAction);
      });
    });

    cancelButton.addEventListener("click", () => {
      if (activeController) {
        activeController.abort();
      }
    });
  }

  initTabs();
  initNetworkCheck();
})();
