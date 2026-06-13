(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
      return;
    }
    fn();
  }

  function qs(root, selector) {
    return root.querySelector(selector);
  }

  function escapeHtml(text) {
    return (text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderInline(text) {
    return escapeHtml(text || "")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
  }

  function renderRichText(text) {
    const source = (text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const lines = source.split("\n");
    const blocks = [];
    let index = 0;

    while (index < lines.length) {
      const rawLine = lines[index];
      const line = rawLine.trim();
      if (!line) {
        index += 1;
        continue;
      }
      if (line.startsWith("```")) {
        const language = escapeHtml(line.slice(3).trim());
        const codeLines = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().startsWith("```")) {
          codeLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) {
          index += 1;
        }
        const languageAttr = language ? ` data-language="${language}"` : "";
        blocks.push(`<pre class="chat-code-block"><code${languageAttr}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        continue;
      }
      if (/^#{1,3}\s+/.test(line)) {
        const level = Math.min(line.match(/^#+/)[0].length + 1, 4);
        blocks.push(`<h${level}>${renderInline(line.replace(/^#{1,3}\s+/, ""))}</h${level}>`);
        index += 1;
        continue;
      }
      if (/^>\s+/.test(line)) {
        const quoteLines = [];
        while (index < lines.length && /^>\s+/.test(lines[index].trim())) {
          quoteLines.push(lines[index].trim().replace(/^>\s?/, ""));
          index += 1;
        }
        blocks.push(`<blockquote>${renderInline(quoteLines.join(" "))}</blockquote>`);
        continue;
      }
      if (/^[-*]\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
          items.push(`<li>${renderInline(lines[index].trim().replace(/^[-*]\s+/, ""))}</li>`);
          index += 1;
        }
        blocks.push(`<ul>${items.join("")}</ul>`);
        continue;
      }
      if (/^\d+\.\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^\d+\.\s+/.test(lines[index].trim())) {
          items.push(`<li>${renderInline(lines[index].trim().replace(/^\d+\.\s+/, ""))}</li>`);
          index += 1;
        }
        blocks.push(`<ol>${items.join("")}</ol>`);
        continue;
      }
      const paragraph = [line];
      index += 1;
      while (index < lines.length) {
        const nextLine = lines[index].trim();
        if (!nextLine || nextLine.startsWith("```") || /^(#{1,3}|[-*]|\d+\.|>)\s+/.test(nextLine)) {
          break;
        }
        paragraph.push(nextLine);
        index += 1;
      }
      blocks.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
    }

    return blocks.join("") || "<p></p>";
  }

  function createChip(text, className) {
    const chip = document.createElement("span");
    chip.className = className;
    chip.textContent = text;
    return chip;
  }

  function createMessageCard(role, title, subtitle, body, status, chips) {
    const article = document.createElement("article");
    article.className = `cockpit-message-card message-card ${role || "assistant"} ${status || ""}`.trim();
    const head = document.createElement("div");
    head.className = "cockpit-message-head";

    const copy = document.createElement("div");
    const titleEl = document.createElement("div");
    titleEl.className = "cockpit-message-title";
    titleEl.textContent = title || "Assistant";
    const subtitleEl = document.createElement("div");
    subtitleEl.className = "cockpit-message-subtitle";
    subtitleEl.textContent = subtitle || "";
    copy.appendChild(titleEl);
    copy.appendChild(subtitleEl);

    const chipRow = document.createElement("div");
    chipRow.className = "chip-row";
    (chips || []).forEach((chip) => {
      chipRow.appendChild(createChip(chip.text, chip.className || "path-chip"));
    });

    head.appendChild(copy);
    head.appendChild(chipRow);

    const bodyEl = document.createElement("div");
    bodyEl.className = "cockpit-message-body message-body";
    bodyEl.dataset.chatRichText = "1";
    bodyEl.dataset.rawContent = body || "";
    bodyEl.innerHTML = renderRichText(body || "");

    const foot = document.createElement("div");
    foot.className = "cockpit-message-foot";

    article.appendChild(head);
    article.appendChild(bodyEl);
    article.appendChild(foot);

    return article;
  }

  function setBanner(banner, title, copy, active) {
    if (!banner) {
      return;
    }
    banner.classList.toggle("is-active", !!active);
    banner.innerHTML = "";
    const titleEl = document.createElement("div");
    titleEl.className = "empty-title";
    titleEl.textContent = title;
    const copyEl = document.createElement("div");
    copyEl.className = "empty-copy";
    copyEl.textContent = copy;
    banner.appendChild(titleEl);
    banner.appendChild(copyEl);
  }

  function updateFoot(card, items) {
    const foot = qs(card, ".cockpit-message-foot, .message-foot");
    foot.innerHTML = "";
    items.forEach((item) => {
      foot.appendChild(createChip(item.text, item.className || "path-chip"));
    });
  }

  function updateHeader(card, title, subtitle, chips) {
    const titleEl = qs(card, ".cockpit-message-title, .message-title");
    const subtitleEl = qs(card, ".cockpit-message-subtitle, .message-subtitle");
    const chipRow = qs(card, ".chip-row");
    if (titleEl) {
      titleEl.textContent = title;
    }
    if (subtitleEl) {
      subtitleEl.textContent = subtitle || "";
    }
    if (chipRow) {
      chipRow.innerHTML = "";
      (chips || []).forEach((chip) => {
        chipRow.appendChild(createChip(chip.text, chip.className || "path-chip"));
      });
    }
  }

  function setCardState(card, role, status) {
    card.className = `cockpit-message-card message-card ${role || "assistant"} ${status || ""}`.trim();
  }

  function setMessageBody(card, text) {
    const body = qs(card, ".cockpit-message-body, .message-body");
    if (!body) {
      return;
    }
    body.dataset.rawContent = text || "";
    body.innerHTML = renderRichText(text || "");
  }

  function enableForm(form, disabled) {
    form.querySelectorAll("input, textarea, select, button").forEach((node) => {
      node.disabled = !!disabled;
    });
  }

  async function persistLayoutPreference(key, value) {
    try {
      await fetch("/preferences/layout", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          key,
          value,
        }),
      });
    } catch (error) {
      // Best effort: the UI stays functional even if persistence fails.
    }
  }

  function parseSseBlock(block) {
    const lines = block.split("\n");
    const data = [];
    for (const line of lines) {
      if (line.startsWith("data:")) {
        data.push(line.slice(5).trimStart());
      }
    }
    if (!data.length) {
      return null;
    }
    try {
      return JSON.parse(data.join("\n"));
    } catch (error) {
      return null;
    }
  }

  async function readEventStream(response, onEvent) {
    if (!response.body || !window.TextDecoder) {
      const text = await response.text();
      text.split("\n\n").forEach((block) => {
        const event = parseSseBlock(block);
        if (event) {
          onEvent(event);
        }
      });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseSseBlock(block);
        if (event) {
          onEvent(event);
        }
        boundary = buffer.indexOf("\n\n");
      }
    }

    if (buffer.trim()) {
      const event = parseSseBlock(buffer);
      if (event) {
        onEvent(event);
      }
    }
  }

  function initChatForm(form) {
    if (!form || form.dataset.chatFormInit === "1") {
      return;
    }
    const banner = document.getElementById("chat-stream-banner");
    let messageList = document.getElementById("chat-message-list");
    const textarea = qs(form, "textarea[name='content']");
    const lane = qs(form, "select[name='lane_override']");
    const mode = qs(form, "select[name='mode']");
    const activeModelSelect = () => Array.from(form.querySelectorAll("select[name='model_name']")).find((node) => !node.disabled) || null;
    const laneButtons = Array.from(form.querySelectorAll("[data-lane-choice]"));
    const laneMore = form.querySelector(".lane-more");
    const laneLabel = form.querySelector("[data-current-lane-label]");
    const laneChip = document.querySelector("[data-current-lane-chip]");
    const modeButtons = Array.from(form.querySelectorAll("[data-mode-choice]"));
    const modeMore = form.querySelector(".mode-more");
    const modeLabel = form.querySelector("[data-current-mode-label]");
    const modeChip = document.querySelector("[data-current-mode-chip]");
    const workflowCard = document.querySelector("[data-workflow-card]");
    const workflowDismiss = workflowCard ? workflowCard.querySelector("[data-workflow-dismiss]") : null;
    const topbarLaneLabel = document.querySelector("[data-topbar-lane-label]");
    const codexModelSelect = document.getElementById("chat-model-name");
    const groqModelSelect = document.getElementById("groq-model-name");
    if (!banner || !textarea || !lane || !mode) {
      return;
    }
    form.dataset.chatFormInit = "1";

    const ensureMessageList = () => {
      if (messageList) {
        return messageList;
      }
      messageList = document.getElementById("chat-message-list");
      if (messageList) {
        return messageList;
      }
      const emptyState = document.querySelector(".cockpit-empty-state");
      if (emptyState && emptyState.parentElement) {
        const list = document.createElement("div");
        list.id = "chat-message-list";
        list.className = "cockpit-message-list";
        emptyState.parentElement.insertBefore(list, emptyState);
        emptyState.remove();
        messageList = list;
        return messageList;
      }
      return null;
    };

    const workflowKindFor = (laneValue, modeValue) => {
      if (laneValue === "codex_lb" || modeValue === "execute") {
        return "codex";
      }
      if (laneValue === "manual_claude" || modeValue === "plan") {
        return "manual";
      }
      return "";
    };

    const syncWorkflowCard = (laneValue, modeValue, forceVisible) => {
      if (!workflowCard) {
        return;
      }
      const kind = workflowKindFor(laneValue, modeValue);
      workflowCard.dataset.workflowKind = kind || workflowCard.dataset.workflowKind || "manual";
      const shouldShow = !!kind && (forceVisible || workflowCard.dataset.workflowVisible === "1" || workflowCard.dataset.workflowReveal === "1");
      workflowCard.hidden = !shouldShow;
      workflowCard.dataset.workflowVisible = shouldShow ? "1" : "0";
    };

    const readableLabel = (value) => value ? value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()) : "Auto";

    const syncChatLinks = () => {
      const currentLane = lane.value || "auto";
      const currentMode = mode.value || "ask";
      document.querySelectorAll("a[href]").forEach((link) => {
        const rawHref = link.getAttribute("href") || "";
        if (!rawHref.includes("/chat/")) {
          return;
        }
        try {
          const url = new URL(link.href, window.location.origin);
          url.searchParams.set("lane_override", currentLane);
          url.searchParams.set("mode", currentMode);
          link.setAttribute("href", `${url.pathname}${url.search}${url.hash}`);
        } catch (error) {
          // Ignore malformed hrefs; this is a progressive enhancement.
        }
      });
    };

    const syncLaneSurface = (value) => {
      const readableLane = readableLabel(value);
      if (laneLabel) {
        laneLabel.textContent = readableLane;
      }
      if (laneChip) {
        laneChip.textContent = value || "auto";
      }
      if (topbarLaneLabel) {
        topbarLaneLabel.textContent = readableLane;
      }
      laneButtons.forEach((button) => {
        button.classList.toggle("is-active", button.getAttribute("data-lane-choice") === value);
      });
      if (laneMore && laneMore.open && !["auto", "ollama_local", "lmstudio_local", "codex_lb", "manual_claude"].includes(value)) {
        laneMore.open = false;
      }
      syncChatLinks();
    };

    const syncModeSurface = (value) => {
      const readableMode = readableLabel(value || "ask");
      modeButtons.forEach((button) => {
        button.classList.toggle("is-active", button.getAttribute("data-mode-choice") === value);
      });
      if (modeLabel) {
        modeLabel.textContent = readableMode;
      }
      if (modeChip) {
        modeChip.textContent = readableMode;
      }
      if (modeMore && modeMore.open && ["ask", "plan", "execute"].includes(value)) {
        modeMore.open = false;
      }
      syncChatLinks();
    };

    const syncModelPickers = (value) => {
      const activeLane = value || "auto";
      if (codexModelSelect) {
        codexModelSelect.disabled = activeLane === "groq_cloud";
      }
      if (groqModelSelect) {
        groqModelSelect.disabled = activeLane !== "groq_cloud";
      }
    };

    const modelValueForLane = (laneValue) => {
      if (laneValue === "groq_cloud") {
        return groqModelSelect ? groqModelSelect.value : "";
      }
      if (laneValue === "codex_lb") {
        return codexModelSelect ? codexModelSelect.value : "";
      }
      return activeModelSelect() ? activeModelSelect().value : "";
    };

    syncLaneSurface(lane.value);
    syncModeSurface(mode.value);
    syncModelPickers(lane.value);
    syncWorkflowCard(lane.value, mode.value, workflowCard ? workflowCard.dataset.workflowVisible === "1" : false);
    window.setTimeout(syncChatLinks, 0);

    laneButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const value = button.getAttribute("data-lane-choice");
        if (!value) {
          return;
        }
      lane.value = value;
      lane.dispatchEvent(new Event("change", { bubbles: true }));
      if (laneMore) {
        laneMore.open = false;
      }
        if (workflowCard) {
          workflowCard.dataset.workflowReveal = workflowKindFor(lane.value, mode.value) ? "1" : "0";
        }
        syncLaneSurface(value);
        syncModelPickers(value);
        syncWorkflowCard(lane.value, mode.value);
      });
    });

    lane.addEventListener("change", () => {
      if (workflowCard) {
        workflowCard.dataset.workflowReveal = workflowKindFor(lane.value, mode.value) ? "1" : "0";
      }
      syncLaneSurface(lane.value);
      syncModelPickers(lane.value);
      syncWorkflowCard(lane.value, mode.value);
    });

    modeButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const value = button.getAttribute("data-mode-choice");
        if (!value) {
          return;
        }
        mode.value = value;
        mode.dispatchEvent(new Event("change", { bubbles: true }));
        if (modeMore) {
          modeMore.open = false;
        }
        if (workflowCard) {
          workflowCard.dataset.workflowReveal = workflowKindFor(lane.value, mode.value) ? "1" : "0";
        }
        syncModeSurface(value);
        syncWorkflowCard(lane.value, mode.value);
      });
    });

    mode.addEventListener("change", () => {
      if (workflowCard) {
        workflowCard.dataset.workflowReveal = workflowKindFor(lane.value, mode.value) ? "1" : "0";
      }
      syncModeSurface(mode.value);
      syncWorkflowCard(lane.value, mode.value);
    });

    if (workflowDismiss) {
      workflowDismiss.addEventListener("click", () => {
        workflowCard.hidden = true;
        workflowCard.dataset.workflowVisible = "0";
      });
    }

    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (form.requestSubmit) {
          form.requestSubmit();
        } else {
          form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
        }
      }
    });

    form.addEventListener("submit", async (event) => {
      if (lane.value === "codex_lb" && mode.value === "execute") {
        return;
      }

      event.preventDefault();
      const prompt = textarea.value.trim();
      if (!prompt) {
        setBanner(banner, "Waiting", "Type a prompt before sending a local turn.", false);
        return;
      }
      const activeMessageList = ensureMessageList();
      if (!activeMessageList) {
        setBanner(banner, "Error", "Chat surface unavailable on this page.", false);
        return;
      }

      const userCard = createMessageCard(
        "user",
        "User",
        "optimistic local note",
        prompt,
        "final",
        [
          { text: "draft", className: "route-chip" },
          { text: lane.value || "auto", className: "path-chip" },
          { text: mode.value || "ask", className: "thread-chip" },
        ],
      );
      activeMessageList.appendChild(userCard);

      const assistantCard = createMessageCard(
        "assistant",
        "Assistant",
        "streaming locally",
        "Streaming...",
        "pending",
        [
          { text: "local", className: "route-chip" },
          { text: "pending", className: "thread-chip" },
          { text: mode.value || "ask", className: "path-chip" },
        ],
      );
      activeMessageList.appendChild(assistantCard);
      assistantCard.scrollIntoView({ block: "end", behavior: "smooth" });

      setBanner(banner, "Streaming", `Local turn started via ${lane.value || "auto"}.`, true);
      const selectedModelName = modelValueForLane(lane.value);
      enableForm(form, true);

      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
            body: JSON.stringify({
              content: prompt,
              lane: lane.value,
              lane_override: lane.value,
              mode: mode.value,
              model_name: selectedModelName,
            }),
          });

        if (!response.ok) {
          let detail = `HTTP ${response.status}`;
          try {
            const payload = await response.json();
            if (payload && payload.detail) {
              detail = payload.detail;
            }
          } catch (error) {
            const fallback = await response.text();
            if (fallback) {
              detail = fallback;
            }
          }
          setBanner(banner, "Error", detail, false);
          setCardState(assistantCard, "assistant", "error");
          updateHeader(
            assistantCard,
            "Assistant error",
            "the local lane returned a failure",
            [
              { text: "error", className: "status-chip bad" },
              { text: "local", className: "route-chip" },
            ],
          );
          setMessageBody(assistantCard, detail);
          updateFoot(assistantCard, [
            { text: "status error" },
          ]);
          return;
        }

        let finalEvent = null;
        await readEventStream(response, (evt) => {
          finalEvent = evt;
          if (evt.event === "status") {
            setBanner(banner, "Streaming", evt.detail || "Local draft is streaming.", true);
            updateHeader(
              assistantCard,
              "Assistant",
              "streaming locally",
              [
                { text: evt.lane || "local", className: "route-chip" },
                { text: evt.model_name || "draft", className: "thread-chip" },
              ],
            );
            return;
          }
          if (evt.event === "delta") {
            const body = qs(assistantCard, ".cockpit-message-body, .message-body");
            const nextText = `${(body.dataset.rawContent || "").replace(/Streaming\.\.\.$/, "")}${evt.delta}`.trimStart();
            setMessageBody(assistantCard, nextText);
            return;
          }
          if (evt.event === "unavailable") {
            setBanner(banner, "Unavailable", evt.detail || "Local runner unavailable.", false);
            setCardState(assistantCard, "system", "error");
            updateHeader(
              assistantCard,
              "Unavailable",
              "local runner offline",
              [
                { text: "unavailable", className: "status-chip bad" },
                { text: evt.lane || "local-unavailable", className: "route-chip" },
              ],
            );
            setMessageBody(assistantCard, evt.detail || "Local runner unavailable.");
            updateFoot(assistantCard, [
              { text: "status unavailable" },
            ]);
            return;
          }
          if (evt.event === "error") {
            setBanner(banner, "Error", evt.detail || "Local chat failed.", false);
            setCardState(assistantCard, "assistant", "error");
            updateHeader(
              assistantCard,
              "Assistant error",
              "the local lane returned a failure",
              [
                { text: evt.error_kind || "error", className: "status-chip bad" },
                { text: "local", className: "route-chip" },
              ],
            );
            setMessageBody(assistantCard, evt.detail || "Local chat failed.");
            updateFoot(assistantCard, [
              { text: "status error" },
            ]);
          }
          if (evt.event === "complete") {
            setBanner(banner, "Done", "Draft response saved locally.", false);
            setCardState(assistantCard, "assistant", "final");
            updateHeader(
              assistantCard,
              "Assistant",
              `session ${evt.conversation_id} · ${evt.endpoint_class || "local"}`,
              [
                { text: evt.endpoint_class || "local", className: "route-chip" },
                { text: evt.model_name || "draft", className: "thread-chip" },
              ],
            );
            setMessageBody(assistantCard, evt.text || (qs(assistantCard, ".cockpit-message-body, .message-body")?.dataset.rawContent || ""));
            updateFoot(assistantCard, [
              { text: "status draft" },
              { text: `route ${evt.route_decision_id || "n/a"}` },
              { text: `msg ${evt.assistant_message_id || "n/a"}` },
            ]);
            textarea.value = "";
          }
        });

        if (finalEvent && finalEvent.event === "unavailable") {
          return;
        }
      } catch (error) {
        const detail = error && error.message ? error.message : "Local chat failed.";
        setBanner(banner, "Error", detail, false);
        setCardState(assistantCard, "assistant", "error");
        updateHeader(
          assistantCard,
          "Assistant error",
          "streaming aborted",
          [
            { text: "error", className: "status-chip bad" },
            { text: "local", className: "route-chip" },
          ],
        );
        setMessageBody(assistantCard, detail);
        updateFoot(assistantCard, [
          { text: "status error" },
        ]);
      } finally {
        enableForm(form, false);
      }
    });
  }

  function initLayoutPersistence() {
    const drawerTargets = {
      memory: "context-card-memory",
      task: "context-card-task",
      route: "context-card-route",
      status: "context-card-status",
      claude: "manual-claude-lane",
      codex: "codex-execution-lane",
    };

    const scrollDrawerTarget = () => {
      const drawer = document.querySelector(".context-drawer");
      const drawerToggle = document.getElementById("context-drawer-toggle");
      if (!drawer || !drawerToggle || !drawerToggle.checked) {
        return;
      }
      const section = drawer.getAttribute("data-active-section") || "memory";
      const targetId = drawerTargets[section] || drawerTargets.memory;
      const target = document.getElementById(targetId);
      if (target && target.scrollIntoView) {
        window.requestAnimationFrame(() => {
          target.scrollIntoView({ block: "start", behavior: "smooth" });
        });
      }
    };

    document.querySelectorAll("[data-layout-pref]").forEach((node) => {
      const key = node.getAttribute("data-layout-pref");
      if (!key) {
        return;
      }
      const persistFromNode = () => {
        const value = node.type === "checkbox"
          ? (node.checked
            ? (node.getAttribute("data-layout-pref-true-value") || "1")
            : (node.getAttribute("data-layout-pref-false-value") || "0"))
          : (node.value || "");
        persistLayoutPreference(key, value);
      };
      node.addEventListener("change", persistFromNode);
    });

    const drawerToggle = document.getElementById("context-drawer-toggle");
    if (drawerToggle) {
      drawerToggle.addEventListener("change", scrollDrawerTarget);
    }

    document.querySelectorAll("[data-drawer-section]").forEach((node) => {
      const section = node.getAttribute("data-drawer-section");
      if (!section) {
        return;
      }
      node.addEventListener("click", () => {
        persistLayoutPreference("context_drawer_section", section);
        const drawer = document.querySelector(".context-drawer");
        if (drawer) {
          drawer.setAttribute("data-active-section", section);
        }
        const drawerToggle = document.getElementById("context-drawer-toggle");
        if (drawerToggle && !drawerToggle.checked) {
          drawerToggle.checked = true;
          drawerToggle.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });
  }

  function initTopbarControls() {
    const laneTargetButtons = Array.from(document.querySelectorAll("[data-topbar-lane-target]"));
    if (!laneTargetButtons.length) {
      return;
    }
    const laneSelect = document.querySelector("select[name='lane_override']");
    if (!laneSelect) {
      return;
    }
    laneTargetButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const targetLane = button.getAttribute("data-topbar-lane-target");
        if (!targetLane) {
          return;
        }
        laneSelect.value = targetLane;
        laneSelect.dispatchEvent(new Event("change", { bubbles: true }));
      });
    });
  }

  function initCommandPalette() {
    const palette = document.querySelector("[data-command-palette]");
    if (!palette) {
      return;
    }

    const openButtons = Array.from(document.querySelectorAll("[data-command-palette-open]"));
    const closeButtons = Array.from(palette.querySelectorAll("[data-command-palette-close]"));
    const query = palette.querySelector("[data-command-palette-query]");
    const list = palette.querySelector("[data-command-palette-list]");
    const createForm = palette.querySelector("[data-command-create-form]");
    const projectSlug = palette.getAttribute("data-project-slug") || "";
    const items = Array.from(palette.querySelectorAll("[data-command-item]"));
    let activeIndex = -1;

    const visibleItems = () => items.filter((item) => !item.hidden);

    const setActive = (index) => {
      const visible = visibleItems();
      if (!visible.length) {
        activeIndex = -1;
        items.forEach((item) => item.classList.remove("is-active"));
        return;
      }
      activeIndex = ((index % visible.length) + visible.length) % visible.length;
      items.forEach((item) => item.classList.remove("is-active"));
      const activeItem = visible[activeIndex];
      if (activeItem) {
        activeItem.classList.add("is-active");
        if (list && activeItem.scrollIntoView) {
          activeItem.scrollIntoView({ block: "nearest" });
        }
      }
    };

    const syncFilter = () => {
      const value = (query && query.value ? query.value.trim().toLowerCase() : "");
      items.forEach((item) => {
        const label = (item.getAttribute("data-command-label") || "").toLowerCase();
        const tags = (item.getAttribute("data-command-tags") || "").toLowerCase();
        const text = (item.textContent || "").toLowerCase();
        const matches = !value || label.includes(value) || tags.includes(value) || text.includes(value);
        item.hidden = !matches;
      });
      setActive(0);
    };

    const close = () => {
      palette.hidden = true;
      document.body.classList.remove("command-palette-open");
    };

    const open = () => {
      palette.hidden = false;
      document.body.classList.add("command-palette-open");
      syncFilter();
      if (query) {
        query.focus();
        query.select();
      }
    };

    const runCreateTask = () => {
      if (!createForm) {
        return;
      }
      const queryValue = query && query.value ? query.value.trim() : "";
      const titleInput = createForm.querySelector("input[name='title']");
      const goalInput = createForm.querySelector("input[name='goal']");
      if (titleInput) {
        titleInput.value = queryValue || "Command palette task";
      }
      if (goalInput) {
        goalInput.value = queryValue || "Created from the command palette.";
      }
      close();
      if (createForm.requestSubmit) {
        createForm.requestSubmit();
      } else {
        createForm.submit();
      }
    };

    const runActive = () => {
      const visible = visibleItems();
      const activeItem = visible[activeIndex] || visible[0];
      if (!activeItem) {
        return;
      }
      if (activeItem.getAttribute("data-command-kind") === "create-task") {
        runCreateTask();
        return;
      }
      close();
      if (activeItem.tagName === "A" && activeItem.href) {
        window.location.href = activeItem.href;
        return;
      }
      if (activeItem.type === "submit" || activeItem.tagName === "BUTTON") {
        activeItem.click();
        return;
      }
      activeItem.click();
    };

    openButtons.forEach((button) => {
      button.addEventListener("click", open);
    });

    closeButtons.forEach((button) => {
      button.addEventListener("click", close);
    });

    if (query) {
      query.addEventListener("input", syncFilter);
      query.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setActive(activeIndex + 1);
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setActive(activeIndex - 1);
          return;
        }
        if (event.key === "Enter") {
          event.preventDefault();
          runActive();
        }
      });
    }

    document.addEventListener("keydown", (event) => {
      const shortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k";
      if (shortcut) {
        event.preventDefault();
        if (palette.hidden) {
          open();
        } else {
          close();
        }
        return;
      }
      if (palette.hidden) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key === "ArrowDown" && event.target !== query) {
        event.preventDefault();
        setActive(activeIndex + 1);
        return;
      }
      if (event.key === "ArrowUp" && event.target !== query) {
        event.preventDefault();
        setActive(activeIndex - 1);
        return;
      }
      if (event.key === "Enter" && event.target !== query) {
        event.preventDefault();
        runActive();
      }
    });

    syncFilter();
    close();
  }

  function bindChatForms(root) {
    (root || document).querySelectorAll("[data-chat-form]").forEach((form) => initChatForm(form));
    (root || document).querySelectorAll("[data-chat-rich-text='1']").forEach((node) => {
      if (!(node instanceof Element)) {
        return;
      }
      const raw = node.getAttribute("data-raw-content");
      if (raw !== null) {
        node.innerHTML = renderRichText(raw);
      }
    });
  }

  function watchChatForms() {
    if (typeof MutationObserver !== "function") {
      return;
    }
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (!(node instanceof Element)) {
            return;
          }
          if (node.matches && node.matches("[data-chat-form]")) {
            initChatForm(node);
            return;
          }
          if (node.querySelectorAll) {
            bindChatForms(node);
          }
        });
      });
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  ready(() => {
    bindChatForms(document);
    window.setTimeout(() => bindChatForms(document), 0);
    window.setTimeout(() => bindChatForms(document), 250);
    watchChatForms();
    initTopbarControls();
    initCommandPalette();
    initLayoutPersistence();
    window.__wrChatInit = "ready";
  });
})();
