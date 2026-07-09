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
    return `${mbps >= 100 ? mbps.toFixed(1) : mbps.toFixed(2)} Mbps`;
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) {
      return "-";
    }
    return `${seconds.toFixed(2)}초`;
  }

  function buildUrl(baseUrl, sizeMb) {
    const url = new URL(baseUrl, window.location.href);
    url.searchParams.set("size_mb", String(sizeMb));
    url.searchParams.set("_", String(Date.now()));
    return url.toString();
  }

  function initNetworkCheck() {
    const root = document.querySelector("[data-network-check]");
    if (!root) {
      return;
    }

    const sizeSelect = root.querySelector("[data-network-size]");
    const actionButtons = root.querySelectorAll("[data-check-action]");
    const statusText = root.querySelector("[data-network-status]");
    const progressBar = root.querySelector("[data-progress-bar]");
    const progressText = root.querySelector("[data-progress-text]");
    const currentSpeed = root.querySelector("[data-current-speed]");
    const summary = root.querySelector("[data-summary]");
    const resultList = root.querySelector("[data-result-list]");

    let running = false;

    function setRunning(nextRunning) {
      running = nextRunning;
      actionButtons.forEach((button) => {
        button.disabled = nextRunning;
      });
      sizeSelect.disabled = nextRunning;
    }

    function setStatus(message) {
      statusText.textContent = message;
    }

    function resetProgress(message) {
      setStatus(message);
      progressBar.style.width = "0%";
      progressText.textContent = "0%";
      currentSpeed.textContent = "-";
      summary.textContent = "-";
    }

    function updateProgress(bytesDone, totalBytes, startedAt) {
      const elapsedSeconds = Math.max((performance.now() - startedAt) / 1000, 0.001);
      const percent = Math.min(100, (bytesDone / totalBytes) * 100);
      const mbps = (bytesDone * 8) / elapsedSeconds / 1_000_000;
      progressBar.style.width = `${percent.toFixed(1)}%`;
      progressText.textContent = `${percent.toFixed(1)}%`;
      currentSpeed.textContent = formatSpeed(mbps);
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

    function createUploadStream(totalBytes, startedAt) {
      if (!window.ReadableStream) {
        throw new Error("현재 브라우저는 스트리밍 업로드를 지원하지 않습니다.");
      }

      const chunk = new Uint8Array(CHUNK_SIZE);
      for (let index = 0; index < chunk.length; index += 1) {
        chunk[index] = index % 251;
      }

      let sentBytes = 0;
      return new ReadableStream({
        pull(controller) {
          const remaining = totalBytes - sentBytes;
          if (remaining <= 0) {
            controller.close();
            return;
          }

          const nextSize = Math.min(CHUNK_SIZE, remaining);
          const nextChunk = nextSize === CHUNK_SIZE ? chunk : chunk.subarray(0, nextSize);
          controller.enqueue(nextChunk);
          sentBytes += nextSize;
          updateProgress(sentBytes, totalBytes, startedAt);
        },
      });
    }

    async function runDownload(sizeMb) {
      const totalBytes = sizeMb * MB;
      const startedAt = performance.now();
      let receivedBytes = 0;

      resetProgress("다운로드 측정 중");
      const response = await fetch(buildUrl(root.dataset.downloadUrl, sizeMb), {
        cache: "no-store",
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || "다운로드 측정 요청이 실패했습니다.");
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
        receivedBytes += value.byteLength;
        updateProgress(receivedBytes, totalBytes, startedAt);
      }

      return completeResult("다운로드", receivedBytes, totalBytes, startedAt);
    }

    async function runUpload(sizeMb) {
      const totalBytes = sizeMb * MB;
      const startedAt = performance.now();

      resetProgress("업로드 측정 중");
      const response = await fetch(buildUrl(root.dataset.uploadUrl, sizeMb), {
        method: "POST",
        body: createUploadStream(totalBytes, startedAt),
        cache: "no-store",
        duplex: "half",
        headers: {
          "Content-Type": "application/octet-stream",
        },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || "업로드 측정 요청이 실패했습니다.");
      }

      return completeResult("업로드", totalBytes, totalBytes, startedAt);
    }

    async function runAction(action) {
      if (running) {
        return;
      }

      const sizeMb = Number.parseInt(sizeSelect.value, 10);
      const results = [];
      setRunning(true);

      try {
        if (action === "upload" || action === "full") {
          results.push(await runUpload(sizeMb));
        }
        if (action === "download" || action === "full") {
          results.push(await runDownload(sizeMb));
        }
        setStatus("완료");
        renderResults(results);
      } catch (error) {
        setStatus("실패");
        summary.textContent = error.message || "측정 중 오류가 발생했습니다.";
      } finally {
        setRunning(false);
      }
    }

    actionButtons.forEach((button) => {
      button.addEventListener("click", () => {
        runAction(button.dataset.checkAction);
      });
    });
  }

  initTabs();
  initNetworkCheck();
})();
