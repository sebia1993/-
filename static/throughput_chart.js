(function (global) {
  const DEFAULT_COLOR = "#246b54";
  const GRID_COLOR = "#d8ddd3";
  const TEXT_COLOR = "#626b5f";
  const AVERAGE_COLOR = "#70766f";

  function finiteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function formatMbps(value) {
    const number = finiteNumber(value, 0);
    return number.toFixed(number >= 100 ? 1 : 2);
  }

  function niceMaximum(value) {
    const safeValue = Math.max(10, finiteNumber(value, 10) * 1.04);
    const magnitude = 10 ** Math.floor(Math.log10(safeValue));
    const normalized = safeValue / magnitude;
    const step = [1, 1.5, 2, 2.5, 5, 7.5, 10].find((candidate) => normalized <= candidate) || 10;
    return step * magnitude;
  }

  function normalizePoints(values) {
    if (!Array.isArray(values)) return [];
    return values
      .map((item, position) => {
        const point = typeof item === "object" && item !== null ? item : { mbps: item };
        const mbps = Number(point.mbps);
        if (!Number.isFinite(mbps) || mbps < 0) return null;
        return {
          index: Math.max(1, Math.round(finiteNumber(point.index, position + 1))),
          mbps,
        };
      })
      .filter(Boolean);
  }

  function tickIndexes(points) {
    if (!points.length) return [];
    const last = points[points.length - 1].index;
    const middle = points[Math.floor((points.length - 1) / 2)].index;
    const requested = points.length <= 12
      ? [points[0].index, middle, last]
      : [points[0].index, 10, 20, last];
    return [...new Set(requested)].filter((index) => points.some((point) => point.index === index));
  }

  function create(canvas, options) {
    if (!canvas || typeof canvas.getContext !== "function") return null;
    const settings = options || {};
    const color = settings.color || DEFAULT_COLOR;
    const fillColor = settings.fillColor || "rgba(36, 107, 84, 0.10)";
    const label = settings.label || "전송";
    const tooltip = canvas.parentElement.querySelector("[data-chart-tooltip]");
    const context = canvas.getContext("2d");
    let points = [];
    let averageOverride = null;
    let activeIndex = -1;
    let resizeFrame = 0;
    let geometry = null;

    function hideTooltip() {
      activeIndex = -1;
      if (tooltip) tooltip.hidden = true;
      draw();
    }

    function chartCoordinates(width, height, maximum) {
      const padding = {
        top: 24,
        right: width < 520 ? 12 : 18,
        bottom: 36,
        left: width < 520 ? 58 : 56,
      };
      const plotWidth = Math.max(1, width - padding.left - padding.right);
      const plotHeight = Math.max(1, height - padding.top - padding.bottom);
      return {
        padding,
        plotWidth,
        plotHeight,
        maximum,
        pointX(position) {
          if (points.length <= 1) return padding.left + plotWidth / 2;
          return padding.left + (plotWidth * position) / (points.length - 1);
        },
        pointY(value) {
          return padding.top + plotHeight - (plotHeight * value) / maximum;
        },
      };
    }

    function drawLabel(text, x, y, align) {
      context.save();
      context.font = '600 11px "Segoe UI", Arial, sans-serif';
      const metrics = context.measureText(text);
      const width = metrics.width + 10;
      const height = 20;
      let left = align === "right" ? x - width : x;
      left = Math.max(2, Math.min(left, geometry.width - width - 2));
      const top = Math.max(2, Math.min(y - height / 2, geometry.height - height - 2));
      context.fillStyle = "rgba(255, 255, 255, 0.94)";
      context.strokeStyle = "#c8d0ca";
      context.lineWidth = 1;
      context.beginPath();
      context.roundRect(left, top, width, height, 4);
      context.fill();
      context.stroke();
      context.fillStyle = "#27362f";
      context.textBaseline = "middle";
      context.fillText(text, left + 5, top + height / 2);
      context.restore();
    }

    function draw() {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const ratio = global.devicePixelRatio || 1;
      const width = Math.max(Math.floor(rect.width), 280);
      const height = Math.max(Math.floor(rect.height), 210);
      const pixelWidth = Math.floor(width * ratio);
      const pixelHeight = Math.floor(height * ratio);
      if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      if (!points.length) {
        geometry = null;
        return;
      }

      const intervalAverage = points.reduce((total, point) => total + point.mbps, 0) / points.length;
      const average = Number.isFinite(averageOverride) ? averageOverride : intervalAverage;
      const maximum = niceMaximum(Math.max(average, ...points.map((point) => point.mbps)));
      const minimumValue = Math.min(...points.map((point) => point.mbps));
      const maximumValue = Math.max(...points.map((point) => point.mbps));
      const minimumIndex = points.findIndex((point) => point.mbps === minimumValue);
      const maximumIndex = points.findIndex((point) => point.mbps === maximumValue);
      geometry = { ...chartCoordinates(width, height, maximum), width, height };
      const { padding, plotWidth, plotHeight, pointX, pointY } = geometry;

      context.font = '12px "Segoe UI", Arial, sans-serif';
      context.lineWidth = 1;
      context.textBaseline = "middle";
      for (let line = 0; line <= 4; line += 1) {
        const y = padding.top + (plotHeight * line) / 4;
        context.strokeStyle = GRID_COLOR;
        context.beginPath();
        context.moveTo(padding.left, y);
        context.lineTo(width - padding.right, y);
        context.stroke();
        context.fillStyle = TEXT_COLOR;
        context.textAlign = "right";
        context.fillText(formatMbps(maximum * (1 - line / 4)), padding.left - 8, y);
      }

      context.textAlign = "center";
      tickIndexes(points).forEach((tickIndex) => {
        const position = points.findIndex((point) => point.index === tickIndex);
        context.fillStyle = TEXT_COLOR;
        context.fillText(`${tickIndex}초`, pointX(position), height - 14);
      });
      context.textAlign = "left";
      context.fillStyle = TEXT_COLOR;
      context.fillText("Mbps", 6, 12);

      context.beginPath();
      context.moveTo(pointX(0), padding.top + plotHeight);
      points.forEach((point, index) => context.lineTo(pointX(index), pointY(point.mbps)));
      context.lineTo(pointX(points.length - 1), padding.top + plotHeight);
      context.closePath();
      context.fillStyle = fillColor;
      context.fill();

      const averageY = pointY(average);
      context.save();
      context.setLineDash([6, 5]);
      context.strokeStyle = AVERAGE_COLOR;
      context.lineWidth = 1.5;
      context.beginPath();
      context.moveTo(padding.left, averageY);
      context.lineTo(width - padding.right, averageY);
      context.stroke();
      context.restore();

      context.strokeStyle = color;
      context.lineWidth = 2.5;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.beginPath();
      points.forEach((point, index) => {
        const x = pointX(index);
        const y = pointY(point.mbps);
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();

      points.forEach((point, index) => {
        const highlighted = index === minimumIndex || index === maximumIndex || index === activeIndex;
        context.beginPath();
        context.arc(pointX(index), pointY(point.mbps), highlighted ? 4.5 : 2.2, 0, Math.PI * 2);
        context.fillStyle = highlighted ? "#ffffff" : color;
        context.fill();
        context.strokeStyle = color;
        context.lineWidth = highlighted ? 2 : 1;
        context.stroke();
      });

      const minimumOnRight = minimumIndex >= points.length / 2;
      const maximumOnRight = maximumIndex >= points.length / 2;
      context.save();
      context.font = '600 11px "Segoe UI", Arial, sans-serif';
      context.fillStyle = AVERAGE_COLOR;
      context.textBaseline = "middle";
      context.textAlign = maximumOnRight ? "left" : "right";
      context.fillText(
        `평균 ${formatMbps(average)}`,
        maximumOnRight ? padding.left : width - padding.right,
        11
      );
      context.restore();
      drawLabel(
        `최저 ${formatMbps(minimumValue)}`,
        pointX(minimumIndex) + (minimumOnRight ? -6 : 6),
        pointY(minimumValue) + 18,
        minimumOnRight ? "right" : "left"
      );
      drawLabel(
        `최고 ${formatMbps(maximumValue)}`,
        pointX(maximumIndex) + (maximumOnRight ? -6 : 6),
        pointY(maximumValue) - 18,
        maximumOnRight ? "right" : "left"
      );

      if (activeIndex >= 0 && activeIndex < points.length) {
        const point = points[activeIndex];
        const x = pointX(activeIndex);
        const y = pointY(point.mbps);
        context.save();
        context.setLineDash([3, 4]);
        context.strokeStyle = "rgba(39, 54, 47, 0.55)";
        context.beginPath();
        context.moveTo(x, padding.top);
        context.lineTo(x, padding.top + plotHeight);
        context.stroke();
        context.restore();
        context.beginPath();
        context.arc(x, y, 5.5, 0, Math.PI * 2);
        context.fillStyle = "#ffffff";
        context.fill();
        context.strokeStyle = color;
        context.lineWidth = 2.5;
        context.stroke();
      }

      canvas.setAttribute(
        "aria-label",
        `${label} 속도 변화. 평균 ${formatMbps(average)} Mbps, 최저 ${formatMbps(minimumValue)} Mbps, 최고 ${formatMbps(maximumValue)} Mbps.`
      );
    }

    function showTooltip(index) {
      if (!tooltip || !geometry || index < 0 || index >= points.length) return;
      activeIndex = index;
      const point = points[index];
      tooltip.textContent = `${point.index}초 · ${formatMbps(point.mbps)} Mbps · ${formatMbps(point.mbps / 8)} MB/s`;
      tooltip.hidden = false;
      draw();
      const x = geometry.pointX(index);
      const y = geometry.pointY(point.mbps);
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      tooltip.style.left = `${Math.max(6, Math.min(x - width / 2, geometry.width - width - 6))}px`;
      tooltip.style.top = `${Math.max(6, y - height - 14)}px`;
    }

    function pointIndexFromEvent(event) {
      if (!geometry || !points.length) return -1;
      const rect = canvas.getBoundingClientRect();
      const clientX = event.clientX;
      if (!Number.isFinite(clientX)) return -1;
      const relativeX = Math.max(geometry.padding.left, Math.min(clientX - rect.left, rect.width - geometry.padding.right));
      const fraction = geometry.plotWidth > 0 ? (relativeX - geometry.padding.left) / geometry.plotWidth : 0;
      return Math.max(0, Math.min(points.length - 1, Math.round(fraction * (points.length - 1))));
    }

    canvas.addEventListener("pointermove", (event) => showTooltip(pointIndexFromEvent(event)));
    canvas.addEventListener("pointerdown", (event) => showTooltip(pointIndexFromEvent(event)));
    canvas.addEventListener("pointerleave", () => {
      if (document.activeElement !== canvas) hideTooltip();
    });
    canvas.addEventListener("focus", () => showTooltip(activeIndex >= 0 ? activeIndex : 0));
    canvas.addEventListener("blur", hideTooltip);
    canvas.addEventListener("keydown", (event) => {
      if (!points.length || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") activeIndex = 0;
      else if (event.key === "End") activeIndex = points.length - 1;
      else if (event.key === "ArrowLeft") activeIndex = Math.max(0, activeIndex - 1);
      else activeIndex = Math.min(points.length - 1, activeIndex + 1);
      showTooltip(activeIndex);
    });

    const resizeObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(() => {
        global.cancelAnimationFrame(resizeFrame);
        resizeFrame = global.requestAnimationFrame(draw);
      })
      : null;
    if (resizeObserver) resizeObserver.observe(canvas);
    else global.addEventListener("resize", draw);

    return {
      draw,
      reset() {
        points = [];
        averageOverride = null;
        hideTooltip();
        context.clearRect(0, 0, canvas.width, canvas.height);
      },
      setData(values, summary) {
        points = normalizePoints(values);
        const configuredAverage = Number(summary && summary.averageMbps);
        averageOverride = Number.isFinite(configuredAverage) && configuredAverage >= 0 ? configuredAverage : null;
        activeIndex = -1;
        if (tooltip) tooltip.hidden = true;
        global.requestAnimationFrame(draw);
      },
    };
  }

  global.InternalUploadThroughputChart = { create };
})(window);
