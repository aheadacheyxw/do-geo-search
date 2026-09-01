import fs from "node:fs/promises";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const normalizeText = (value) => String(value || "").replace(/[\s\u00a0]+/g, "").trim();

async function readJson(path) {
  return JSON.parse(await fs.readFile(path, "utf8"));
}

async function readEvents(path) {
  try {
    return (await fs.readFile(path, "utf8"))
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

export async function createCollector({ tabs, runRoot, stageRoot }) {
  const manifest = await readJson(`${runRoot}/manifest.json`);
  const contextDefaults = manifest.measurement_context_defaults || {};
  const safeToken = (value) => String(value || "item").replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  const observationIdFor = (platformSlug, questionId, attempt) =>
    `obs-${safeToken(platformSlug)}-${safeToken(manifest.run_id)}-${safeToken(questionId)}-s${attempt}`;
  const eventsPath = `${runRoot}/control/events.jsonl`;
  const platformConfig = {
    DeepSeek: { slug: "deepseek", surface: "chat.deepseek.com", mode: "智能搜索", webSearch: "observed_enabled", answer: ".ds-markdown.ds-assistant-message-main-content" },
    豆包: { slug: "doubao", surface: "www.doubao.com/chat", mode: "默认", answer: '[data-plugin-identifier="block_type:10000"]' },
    千问: { slug: "qwen", surface: "www.qianwen.com", mode: "默认", answer: ".qk-markdown.qk-markdown-complete" },
    Kimi: { slug: "kimi", surface: "www.kimi.com", mode: "快速", answer: ".chat-content-item-assistant .markdown-container:not(.toolcall-content-text)" },
    腾讯元宝: { slug: "yuanbao", surface: "yuanbao.tencent.com", mode: "默认", answer: ".hyc-content-md-done" },
  };

  async function appendEvent(event) {
    await fs.appendFile(eventsPath, `${JSON.stringify(event)}\n`, "utf8");
  }

  async function nextReady(platform) {
    const now = Date.now();
    const events = await readEvents(eventsPath);
    const sent = events.filter((event) => event.event_type === "prompt_sent");
    const globalLast = sent.length ? Date.parse(sent.at(-1).at) : 0;
    const platformEvents = events.filter((event) => event.platform === platform);
    const platformSent = platformEvents.filter((event) => event.event_type === "prompt_sent");
    const platformLast = platformSent.length ? Date.parse(platformSent.at(-1).at) : 0;
    const completed = platformEvents.filter((event) => event.event_type === "observation_completed");
    let readyAt = Math.max(globalLast + 30_000, platformLast + 120_000);
    if (completed.length && completed.length % 3 === 0) {
      readyAt = Math.max(readyAt, Date.parse(completed.at(-1).at) + 600_000);
    }
    return { ready: now >= readyAt, readyAt: new Date(readyAt).toISOString(), waitMs: Math.max(0, readyAt - now), completedCount: completed.length };
  }

  async function closeTransient(platform, tab) {
    const candidates = [];
    if (platform === "豆包" || platform === "千问") candidates.push(tab.playwright.getByRole("button", { name: "关闭" }).filter({ visible: true }).last());
    if (platform === "腾讯元宝") candidates.push(tab.playwright.getByRole("img", { name: "关闭" }).filter({ visible: true }).last());
    for (const candidate of candidates) {
      try { if (await candidate.count()) await candidate.click(); } catch {}
    }
  }

  async function startNew(platform, tab) {
    await closeTransient(platform, tab);
    let control;
    if (platform === "DeepSeek") control = tab.playwright.getByText("开启新对话", { exact: true }).filter({ visible: true }).first();
    else if (platform === "豆包") control = tab.playwright.getByText("新对话", { exact: true }).filter({ visible: true }).first();
    else if (platform === "千问") control = tab.playwright.getByRole("button", { name: "新建对话" }).filter({ visible: true }).first();
    else if (platform === "Kimi") control = tab.playwright.getByRole("link", { name: /新建会话/ }).filter({ visible: true }).first();
    else control = tab.playwright.getByText("新对话", { exact: true }).filter({ visible: true }).last();
    if (!(await control.count())) throw new Error("new_session_control_missing");
    await control.click();
    await tab.playwright.waitForTimeout(900);
    await closeTransient(platform, tab);
  }

  async function inputFor(platform, tab) {
    if (platform === "DeepSeek") return tab.playwright.getByRole("textbox", { name: "给 DeepSeek 发送消息" }).filter({ visible: true }).last();
    const textbox = tab.playwright.getByRole("textbox").filter({ visible: true }).last();
    if (await textbox.count()) return textbox;
    return tab.playwright.locator('[contenteditable="true"]').filter({ visible: true }).last();
  }

  const answerFor = (platform, tab) => tab.playwright.locator(platformConfig[platform].answer).filter({ visible: true }).last();

  async function visibleQuestion(platform, tab, fallback) {
    let locator;
    if (platform === "DeepSeek") locator = tab.playwright.locator(".ds-message.d29f3d7d").filter({ visible: true }).last();
    else if (platform === "千问") locator = tab.playwright.locator(".message-card-wrap.question").filter({ visible: true }).last();
    else if (platform === "Kimi") locator = tab.playwright.locator(".segment-user .segment-content-box").filter({ visible: true }).last();
    else if (platform === "腾讯元宝") locator = tab.playwright.locator(".agent-chat__list__item--human .hyc-content-text").filter({ visible: true }).last();
    else locator = tab.playwright.getByText(fallback, { exact: true }).filter({ visible: true }).last();
    try { if (await locator.count()) return (await locator.innerText()).trim(); } catch {}
    return fallback;
  }

  async function terminalState(tab) {
    const dialog = tab.playwright.getByRole("dialog").filter({ visible: true }).last();
    try {
      if (await dialog.count()) {
        const text = await dialog.innerText();
        if (/验证码|安全验证|完成验证|滑块|访问过于频繁|操作频繁/.test(text)) return "captcha";
        if (/登录|扫码登录|手机号登录/.test(text)) return "auth_required";
      }
    } catch {}
    return null;
  }

  async function waitAnswer(platform, tab) {
    let lastText = "";
    let stable = 0;
    for (let index = 0; index < 48; index += 1) {
      const terminal = await terminalState(tab);
      if (terminal) throw new Error(terminal);
      const locator = answerFor(platform, tab);
      if (await locator.count()) {
        const text = (await locator.innerText()).trim();
        if (text.length >= 80 && text === lastText) stable += 1;
        else stable = 0;
        lastText = text;
        if ((platform === "千问" || platform === "腾讯元宝") && text.length >= 80) return locator;
        if (text.length >= 80 && stable >= 2) return locator;
      }
      await sleep(5_000);
    }
    throw new Error("answer_timeout");
  }

  async function expandAndSources(platform, tab, answerLocator) {
    let items = [];
    let panelHtml = "";
    try {
      if (platform === "DeepSeek") {
        const control = tab.playwright.getByText(/已阅读\s*\d+\s*个网页/).filter({ visible: true }).last();
        if (await control.count()) await control.click();
        await sleep(1_800);
        items = await tab.playwright.locator("a[href]").evaluateAll((anchors) => anchors.map((anchor) => {
          const rect = anchor.getBoundingClientRect();
          const title = anchor.querySelector(".search-view-card__title");
          const snippet = anchor.querySelector(".search-view-card__snippet");
          return rect.width > 0 && rect.height > 0 && title && snippet ? { url: anchor.href, title: (title.innerText || "").trim(), publisher: "", snippet: (snippet.innerText || "").trim(), html: anchor.outerHTML } : null;
        }).filter(Boolean));
        panelHtml = items.map((item) => item.html).join("\n");
      } else if (platform === "豆包") {
        const control = tab.playwright.getByText(/搜索\s*\d+\s*个关键词，参考\s*\d+\s*篇资料/).filter({ visible: true }).last();
        if (await control.count()) await control.click();
        await sleep(1_800);
        const panel = tab.playwright.locator('[data-plugin-identifier*="search_query_result_block"]').filter({ visible: true }).last();
        if (await panel.count()) {
          items = await panel.locator('a[data-thinking-box-tool-call="true"][href]', {}).evaluateAll((anchors) => anchors.map((anchor) => ({ url: anchor.href, title: (anchor.innerText || "").replace(/^\s*\d+\.\s*/, "").trim(), publisher: "", snippet: "", html: anchor.outerHTML })));
          panelHtml = await panel.evaluate((element) => element.outerHTML);
        }
      } else if (platform === "千问") {
        let control = tab.playwright.getByText(/\d+篇来源/).filter({ visible: true }).last();
        if (await control.count()) await control.click();
        else {
          control = tab.playwright.getByText("查看全部", { exact: true }).filter({ visible: true }).last();
          if (await control.count()) await control.click();
        }
        await sleep(1_500);
        const panel = tab.playwright.locator("[class*=deep-think-source]").filter({ visible: true }).last();
        if (await panel.count()) {
          items = await panel.locator("[class*=source-item]", {}).evaluateAll((elements) => elements.map((element) => {
            try {
              const metadata = JSON.parse(element.getAttribute("data-click-extra") || "{}");
              return { url: metadata.url || metadata.ref_url, title: metadata.title || element.innerText.trim(), publisher: "", snippet: "", html: element.outerHTML };
            } catch { return null; }
          }).filter(Boolean));
          panelHtml = await panel.evaluate((element) => element.outerHTML);
        }
      } else if (platform === "Kimi") {
        const control = tab.playwright.getByText("引用", { exact: true }).filter({ visible: true }).last();
        if (await control.count()) await control.click();
        await sleep(1_500);
        const panel = tab.playwright.locator(".side-console-rail.open").filter({ visible: true }).last();
        if (await panel.count()) {
          items = await panel.locator("a.site-item[href]", {}).evaluateAll((anchors) => anchors.map((anchor) => ({ url: anchor.href, title: (anchor.querySelector(".site-title")?.innerText || anchor.innerText || "").trim(), publisher: (anchor.querySelector(".site-name-text")?.innerText || anchor.querySelector(".site-name")?.innerText || "").trim(), snippet: "", html: anchor.outerHTML })));
          panelHtml = await panel.evaluate((element) => element.outerHTML);
        }
      } else {
        await closeTransient(platform, tab);
        const control = tab.playwright.locator('[aria-label^="引用"][aria-label$="作为参考"]').filter({ visible: true }).last();
        if (await control.count()) await control.click();
        await sleep(1_500);
        items = await tab.playwright.locator('.hyc-common-markdown__ref_card[data-url]').evaluateAll((elements) => elements.filter((element) => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0; }).map((element) => ({ url: element.getAttribute("data-url"), title: (element.querySelector(".hyc-common-markdown__ref_card-title")?.innerText || "").trim(), publisher: (element.querySelector(".hyc-common-markdown__ref_card-foot__source_txt")?.innerText || "").trim(), snippet: (element.querySelector(".hyc-common-markdown__ref_card-desc")?.innerText || "").trim(), html: element.outerHTML })));
        panelHtml = items.map((item) => item.html).join("\n");
      }
    } catch {}
    if (!items.length) {
      try {
        items = await answerLocator.locator("a[href]", {}).evaluateAll((anchors) => anchors.filter((anchor) => anchor.innerText && /^https?:/.test(anchor.href)).map((anchor) => ({ url: anchor.href, title: anchor.innerText.trim(), publisher: "", snippet: (anchor.parentElement?.innerText || "").slice(0, 240), html: anchor.outerHTML })));
        panelHtml = items.map((item) => item.html).join("\n");
      } catch {}
    }
    const unique = [];
    const seen = new Set();
    for (const item of items) {
      if (item?.url && item?.title && !seen.has(item.url)) { seen.add(item.url); unique.push(item); }
    }
    return { items: unique, panelHtml };
  }

  function observationContext(platform, questionIndex, attempt = 1) {
    const config = platformConfig[platform];
    const question = manifest.planned_questions[questionIndex];
    const observationId = observationIdFor(config.slug, question.question_id, attempt);
    return { config, question, observationId, stagingPath: `${stageRoot}/${observationId}`, tab: tabs[platform] };
  }

  async function sendOne(platform, questionIndex, attempt = 1) {
    const { question, observationId, stagingPath, tab } = observationContext(platform, questionIndex, attempt);
    try { await fs.access(`${runRoot}/raw/observations/${observationId}`); return { status: "exists", platform, questionId: question.question_id, observationId }; } catch {}
    try { await fs.access(`${stagingPath}/observation.json`); return { status: "staged", platform, questionId: question.question_id, observationId }; } catch {}
    const events = await readEvents(eventsPath);
    if (events.some((event) => event.event_type === "prompt_sent" && event.observation_id === observationId)) {
      return { status: "already_sent", platform, questionId: question.question_id, observationId };
    }
    const ready = await nextReady(platform);
    if (!ready.ready) return { status: "not_ready", platform, questionIndex, ...ready };
    try {
      await startNew(platform, tab);
      const input = await inputFor(platform, tab);
      if (!(await input.count())) throw new Error("input_missing");
      await input.fill(question.exact_question_text);
      const sentAt = new Date().toISOString();
      await input.press("Enter");
      await appendEvent({ event_id: `${observationId}-send`, event_type: "prompt_sent", platform, question_id: question.question_id, observation_id: observationId, at: sentAt });
      return { status: "sent", platform, questionId: question.question_id, observationId, sentAt };
    } catch (error) {
      const message = String(error);
      await appendEvent({ event_id: `${observationId}-send-failure-${Date.now()}`, event_type: "technical_failure", platform, question_id: question.question_id, observation_id: observationId, at: new Date().toISOString(), note: message });
      return { status: "technical_failure", platform, questionId: question.question_id, observationId, error: message };
    }
  }

  async function captureOne(platform, questionIndex, attempt = 1, retryOf = null) {
    const { config, question, observationId, stagingPath, tab } = observationContext(platform, questionIndex, attempt);
    try { await fs.access(`${runRoot}/raw/observations/${observationId}`); return { status: "exists", platform, questionId: question.question_id, observationId }; } catch {}
    try { await fs.access(`${stagingPath}/observation.json`); return { status: "staged", platform, questionId: question.question_id, observationId }; } catch {}
    const events = await readEvents(eventsPath);
    const sent = [...events].reverse().find((event) => event.event_type === "prompt_sent" && event.observation_id === observationId);
    if (!sent) return { status: "not_sent", platform, questionId: question.question_id, observationId };
    try {
      const terminal = await terminalState(tab);
      if (terminal) throw new Error(terminal);
      const answerLocator = answerFor(platform, tab);
      if (!(await answerLocator.count())) return { status: "generating", platform, questionId: question.question_id, observationId, reason: "answer_missing" };
      const firstText = (await answerLocator.innerText()).trim();
      if (firstText.length < 80) return { status: "generating", platform, questionId: question.question_id, observationId, answerChars: firstText.length };
      if (platform !== "千问" && platform !== "腾讯元宝") {
        await sleep(4_000);
        const secondText = (await answerLocator.innerText()).trim();
        if (secondText !== firstText) return { status: "generating", platform, questionId: question.question_id, observationId, answerChars: secondText.length };
      }
      const visible = await visibleQuestion(platform, tab, question.exact_question_text);
      if (normalizeText(visible) !== normalizeText(question.exact_question_text)) throw new Error(`visible_question_mismatch:${visible}`);
      const answer = (await answerLocator.innerText()).trim();
      await fs.mkdir(stagingPath, { recursive: true });
      await fs.writeFile(`${stagingPath}/answer.txt`, answer);
      await fs.writeFile(`${stagingPath}/initial.html`, `<section data-capture-state="initial">${await answerLocator.evaluate((element) => element.outerHTML)}</section>`);
      await fs.writeFile(`${stagingPath}/initial.png`, await tab.screenshot({ fullPage: true }));
      const sources = await expandAndSources(platform, tab, answerLocator);
      await fs.writeFile(`${stagingPath}/expanded.html`, `<section data-capture-state="expanded-answer">${await answerLocator.evaluate((element) => element.outerHTML)}</section><aside id="VISIBLE_SOURCE_CARDS" data-capture-state="expanded-visible-source-cards">${sources.panelHtml}</aside>`);
      await fs.writeFile(`${stagingPath}/expanded.png`, await tab.screenshot({ fullPage: true }));
      const cards = [];
      for (const [index, item] of sources.items.entries()) {
        try { cards.push({ source_card_id: `${observationId}:card:${index + 1}`, visible_url: item.url, visible_anchor_text: item.title, visible_domain_text: item.publisher || new URL(item.url).hostname, answer_or_card_span: item.snippet || null, source_card_status: "complete", capture_state: "expanded" }); } catch {}
      }
      await fs.writeFile(`${stagingPath}/source-cards.json`, JSON.stringify(cards, null, 2));
      await fs.writeFile(`${stagingPath}/citation-candidates.json`, JSON.stringify(cards.map((card) => ({ candidate_origin: "visible_source_card", url: card.visible_url, anchor_or_span: card.visible_anchor_text, kind: "visible_source_card" })), null, 2));
      const completedAt = new Date().toISOString();
      await fs.writeFile(`${stagingPath}/observation.json`, JSON.stringify({
        observation_id: observationId, run_id: manifest.run_id, question_id: question.question_id, question_revision_id: question.question_revision_id,
        frozen_question_text: question.exact_question_text, actual_sent_text: question.exact_question_text, platform_visible_query_text: visible,
        prompt_integrity_state: "exact_match", transform_type: "none", transform_observability: "observed", sent_at: sent.at, completed_at: completedAt,
        session_reference: await tab.url(), retry_lineage: { attempt, retry_of: retryOf },
        measurement_context: { platform, surface_class: "general_ai_web", platform_product_surface: config.surface, market_region: contextDefaults.market_region || "unavailable", language_locale: contextDefaults.language_locale || "unavailable", answer_language: contextDefaults.answer_language || contextDefaults.language_locale || "unavailable", account_session_class: contextDefaults.account_session_class || "existing_logged_in_monitoring_session;identity_not_stored", web_search_state: config.webSearch || contextDefaults.web_search_state || "observed_default_ui", mode_reasoning_state: config.mode || contextDefaults.mode_reasoning_state || "observed_default_ui", collection_mode: contextDefaults.collection_mode || "standardized_local_UI" },
        comparable: true, non_comparable_reasons: [],
      }, null, 2));
      await appendEvent({ event_id: `${observationId}-complete`, event_type: "observation_completed", platform, question_id: question.question_id, observation_id: observationId, at: completedAt });
      return { status: "complete", platform, questionId: question.question_id, observationId, answerChars: answer.length, sources: cards.length };
    } catch (error) {
      const message = String(error);
      const terminal = /auth_required|captcha/.test(message);
      await appendEvent({ event_id: `${observationId}-${terminal ? "terminal" : "capture-failure"}-${Date.now()}`, event_type: terminal ? "terminal" : "technical_failure", platform, question_id: question.question_id, observation_id: observationId, terminal_status: terminal ? (message.includes("auth") ? "auth_required" : "captcha") : "", at: new Date().toISOString(), note: message });
      return { status: terminal ? "terminal" : "technical_failure", platform, questionId: question.question_id, observationId, error: message };
    }
  }

  async function collectOne(platform, questionIndex, attempt = 1, retryOf = null) {
    const ready = await nextReady(platform);
    if (!ready.ready) return { status: "not_ready", platform, questionIndex, ...ready };
    const config = platformConfig[platform];
    const tab = tabs[platform];
    const question = manifest.planned_questions[questionIndex];
    const questionId = question.question_id;
    const observationId = observationIdFor(config.slug, questionId, attempt);
    const stagingPath = `${stageRoot}/${observationId}`;
    try { await fs.access(`${runRoot}/raw/observations/${observationId}`); return { status: "exists", platform, questionId, observationId }; } catch {}
    try { await fs.access(`${stagingPath}/observation.json`); return { status: "staged", platform, questionId, observationId }; } catch {}
    try {
      await startNew(platform, tab);
      const input = await inputFor(platform, tab);
      if (!(await input.count())) throw new Error("input_missing");
      await input.fill(question.exact_question_text);
      const sentAt = new Date().toISOString();
      await input.press("Enter");
      await appendEvent({ event_id: `${observationId}-send`, event_type: "prompt_sent", platform, question_id: questionId, observation_id: observationId, at: sentAt });
      const answerLocator = await waitAnswer(platform, tab);
      const visible = await visibleQuestion(platform, tab, question.exact_question_text);
      if (normalizeText(visible) !== normalizeText(question.exact_question_text)) throw new Error(`visible_question_mismatch:${visible}`);
      const answer = (await answerLocator.innerText()).trim();
      if (answer.length < 80) throw new Error(`answer_too_short:${answer.length}`);
      await fs.mkdir(stagingPath, { recursive: true });
      await fs.writeFile(`${stagingPath}/answer.txt`, answer);
      await fs.writeFile(`${stagingPath}/initial.html`, `<section data-capture-state="initial">${await answerLocator.evaluate((element) => element.outerHTML)}</section>`);
      await fs.writeFile(`${stagingPath}/initial.png`, await tab.screenshot({ fullPage: true }));
      const sources = await expandAndSources(platform, tab, answerLocator);
      await fs.writeFile(`${stagingPath}/expanded.html`, `<section data-capture-state="expanded-answer">${await answerLocator.evaluate((element) => element.outerHTML)}</section><aside id="VISIBLE_SOURCE_CARDS" data-capture-state="expanded-visible-source-cards">${sources.panelHtml}</aside>`);
      await fs.writeFile(`${stagingPath}/expanded.png`, await tab.screenshot({ fullPage: true }));
      const cards = [];
      for (const [index, item] of sources.items.entries()) {
        try {
          cards.push({ source_card_id: `${observationId}:card:${index + 1}`, visible_url: item.url, visible_anchor_text: item.title, visible_domain_text: item.publisher || new URL(item.url).hostname, answer_or_card_span: item.snippet || null, source_card_status: "complete", capture_state: "expanded" });
        } catch {}
      }
      await fs.writeFile(`${stagingPath}/source-cards.json`, JSON.stringify(cards, null, 2));
      await fs.writeFile(`${stagingPath}/citation-candidates.json`, JSON.stringify(cards.map((card) => ({ candidate_origin: "visible_source_card", url: card.visible_url, anchor_or_span: card.visible_anchor_text, kind: "visible_source_card" })), null, 2));
      const completedAt = new Date().toISOString();
      const observation = {
        observation_id: observationId,
        run_id: manifest.run_id,
        question_id: questionId,
        question_revision_id: question.question_revision_id,
        frozen_question_text: question.exact_question_text,
        actual_sent_text: question.exact_question_text,
        platform_visible_query_text: visible,
        prompt_integrity_state: "exact_match",
        transform_type: "none",
        transform_observability: "observed",
        sent_at: sentAt,
        completed_at: completedAt,
        session_reference: await tab.url(),
        retry_lineage: { attempt, retry_of: retryOf },
        measurement_context: { platform, surface_class: "general_ai_web", platform_product_surface: config.surface, market_region: contextDefaults.market_region || "unavailable", language_locale: contextDefaults.language_locale || "unavailable", answer_language: contextDefaults.answer_language || contextDefaults.language_locale || "unavailable", account_session_class: contextDefaults.account_session_class || "existing_logged_in_monitoring_session;identity_not_stored", web_search_state: config.webSearch || contextDefaults.web_search_state || "observed_default_ui", mode_reasoning_state: config.mode || contextDefaults.mode_reasoning_state || "observed_default_ui", collection_mode: contextDefaults.collection_mode || "standardized_local_UI" },
        comparable: true,
        non_comparable_reasons: [],
      };
      await fs.writeFile(`${stagingPath}/observation.json`, JSON.stringify(observation, null, 2));
      await appendEvent({ event_id: `${observationId}-complete`, event_type: "observation_completed", platform, question_id: questionId, observation_id: observationId, at: completedAt });
      return { status: "complete", platform, questionId, observationId, answerChars: answer.length, sources: cards.length };
    } catch (error) {
      const message = String(error);
      const terminal = /auth_required|captcha/.test(message);
      await appendEvent({ event_id: `${observationId}-${terminal ? "terminal" : "failure"}-${Date.now()}`, event_type: terminal ? "terminal" : "technical_failure", platform, question_id: questionId, observation_id: observationId, terminal_status: terminal ? (message.includes("auth") ? "auth_required" : "captcha") : "", at: new Date().toISOString(), note: message });
      return { status: terminal ? "terminal" : "technical_failure", platform, questionId, observationId, error: message };
    }
  }

  return { manifest, platformConfig, nextReady, sendOne, captureOne, collectOne };
}
