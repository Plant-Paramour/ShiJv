import { evaluateGeneratedPoem, getSongciStanzaEnds } from "./prosody-evaluation.js?v=17";
import { flattenMeter } from "./core.js?v=22";
import { getLocalMeterCatalog, getLocalRhymeOptions, lookupLocalRhymeParts, validateLocalFixedLines } from "./local-rhyme.js?v=3";
import { getDatasetJson } from "./dataset-cache.js?v=3";
import { renderChatMarkdown } from "./chat-markdown.js?v=3";
import { createStreamingText } from "./stream-text.js?v=2";
import renderMathInElement from "./vendor/katex/auto-render.mjs";

window.ShijuStartup?.mark("入口模块及静态依赖执行成功");

const routeTitles = {
  chat: "AI 对话 · 诗矩",
  prosody: "格律检测 · 诗矩",
  "sentence-search": "佳句检索 · 诗矩",
  profile: "个人中心 · 诗矩",
  poem: "诗作修改 · 诗矩",
  settings: "模型设置 · 诗矩",
};

const views = new Map(
  [...document.querySelectorAll("[data-view]")].map((view) => [view.dataset.view, view]),
);
const navLinks = [...document.querySelectorAll(".site-nav [data-route-link]")];
const navToggle = document.querySelector(".nav-toggle");
const siteNav = document.querySelector(".site-nav");
const drawerBackdrop = document.querySelector("#drawer-backdrop");
const leftDrawer = document.querySelector("#site-left-drawer");
const rightDrawer = document.querySelector("#site-right-drawer");
const mainContent = document.querySelector("#main-content");
const siteStatus = document.querySelector("#site-status");
let statusTimer;
let currentUser = null;
const chatImagePreviews = new Map();
let currentConversationId = null;
let currentConversationReadOnly = false;
const poemPageState = { source: null, evaluation: null, evaluatedContent: "", version: 0, timer: null, returnHash: "#profile" };
let editingMessageId = null;
const activeEvaluations = new Map();
// Job snapshots can lag behind the evaluation PUT response. Keep successful
// results locally so polling/SSE snapshots cannot schedule the same score again.
const completedEvaluations = new Map();
const failedEvaluations = new Set();
const prosodyInspector = document.querySelector("#prosody-inspector");
const prosodyInspectorBody = document.querySelector("#prosody-inspector-body");

function announce(message) {
  window.clearTimeout(statusTimer);
  siteStatus.replaceChildren();
  siteStatus.textContent = message; siteStatus.onclick = null;
  siteStatus.classList.add("is-visible");
  statusTimer = window.setTimeout(() => {
    siteStatus.classList.remove("is-visible");
    siteStatus.textContent = "";
  }, 5000);
}

function closeNavigation() {
  navToggle.setAttribute("aria-expanded", "false");
  siteNav.classList.remove("is-open");
  closeDrawers();
}

function closeDrawers() {
  for (const drawer of [leftDrawer, rightDrawer]) {
    drawer?.classList.remove("is-open");
    drawer?.setAttribute("aria-hidden", "true");
  }
  drawerBackdrop?.classList.remove("is-visible");
  if (drawerBackdrop) drawerBackdrop.hidden = true;
  navToggle?.setAttribute("aria-expanded", "false");
  document.body.classList.remove("has-open-drawer");
}

function openDrawer(drawer) {
  if (!drawer) return;
  closeDrawers();
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  if (drawer === leftDrawer) navToggle?.setAttribute("aria-expanded", "true");
  document.body.classList.add("has-open-drawer");
  if (drawerBackdrop) {
    drawerBackdrop.hidden = false;
    requestAnimationFrame(() => drawerBackdrop.classList.add("is-visible"));
  }
  drawer.querySelector("[data-drawer-close]")?.focus();
  if (drawer === leftDrawer && currentUser) loadConversations();
}

function showRoute(route, options = {}) {
  const resolvedRoute = views.has(route) ? route : "chat";
  if (!window.location.hash || route === "home" || !views.has(route)) window.history.replaceState(null, "", "#chat");
  for (const [name, view] of views) view.hidden = name !== resolvedRoute;
  if (resolvedRoute === "profile") activateProfileTab(profileTabFromHash());
  for (const link of navLinks) {
    const isCurrent = link.dataset.routeLink === resolvedRoute;
    if (isCurrent) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
    link.closest(".han-navbar__item")?.classList.toggle("han-navbar__item--active", isCurrent);
  }
  document.body.dataset.currentView = resolvedRoute;
  if (resolvedRoute !== "chat") clearConversationRefresh();
  window.dispatchEvent(new CustomEvent("shiju:route", { detail: resolvedRoute }));
  document.title = routeTitles[resolvedRoute];
  closeNavigation();
  if (options.scroll !== false) window.scrollTo({ top: 0, behavior: "auto" });
  if (options.focus) mainContent.focus({ preventScroll: true });
  if (resolvedRoute === "profile" && currentUser) {
    loadProfileData();
  }
  if (resolvedRoute === "settings") loadModelConfig();
  if (resolvedRoute === "poem" && currentUser) loadPoemPage();
  if (resolvedRoute === "chat" && currentUser) loadConversations({ restore: options.restore !== false && Boolean(conversationIdFromLocation()) });
  if (!prosodyInspector.hidden) closeProsodyInspector();
}

function routeFromHash() {
  const parts = window.location.hash.slice(1).split("/");
  if (parts[0] === "poem" && parts[1]) return "poem";
  return parts[0] || "chat";
}

const profileTabNames = new Set(["account", "poems", "favorites", "archive"]);
function profileTabFromHash() {
  const parts = window.location.hash.slice(1).split("/");
  return parts[0] === "profile" && profileTabNames.has(parts[1]) ? parts[1] : "account";
}

function activateProfileTab(tab) {
  const selected = profileTabNames.has(tab) ? tab : "account";
  document.querySelector(".profile-view")?.setAttribute("data-profile-tab-active", selected);
  document.querySelector(".profile-heading__stats")?.toggleAttribute("hidden", selected === "poems");
  document.querySelectorAll("[data-profile-tab]").forEach((button) => {
    const active = button.dataset.profileTab === selected;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll("[data-profile-panel]").forEach((panel) => { panel.hidden = panel.dataset.profilePanel !== selected; });
}

function navigateToProfileTab(tab) {
  const selected = profileTabNames.has(tab) ? tab : "account";
  activateProfileTab(selected);
  const target = `#profile/${selected}`;
  if (window.location.hash !== target) window.location.hash = target;
}

function conversationIdFromLocation() {
  const match = window.location.hash.match(/^#chat\/s\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

function setConversationLocation(conversationId) {
  if (routeFromHash() !== "chat") return;
  const target = conversationId ? `#chat/s/${encodeURIComponent(conversationId)}` : "#chat";
  if (window.location.hash !== target) {
    window.history.replaceState(null, "", target);
  }
}

navToggle.addEventListener("click", () => {
  const open = leftDrawer.classList.contains("is-open");
  if (open) closeDrawers();
  else openDrawer(leftDrawer);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (navToggle.getAttribute("aria-expanded") === "true" || leftDrawer.classList.contains("is-open") || rightDrawer.classList.contains("is-open"))) {
    closeNavigation();
  }
});

siteNav.addEventListener("click", (event) => {
  if (event.target.closest("a")) closeNavigation();
});

const siteHeader = document.querySelector("[data-han-navbar]");
window.addEventListener("scroll", () => siteHeader?.classList.toggle("is-scrolled", window.scrollY > 8), { passive: true });

document.querySelectorAll("[data-drawer-close]").forEach((button) => button.addEventListener("click", closeDrawers));
drawerBackdrop?.addEventListener("click", closeDrawers);
document.querySelectorAll("[data-drawer-route]").forEach((link) => link.addEventListener("click", () => closeDrawers()));

document.querySelector("#login-button").addEventListener("click", () => {
  if (currentUser) {
    openDrawer(rightDrawer);
    return;
  }
  openDrawer(rightDrawer);
});

document.querySelectorAll("[data-profile-drawer-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!currentUser) {
      closeDrawers();
      requireLocalProfile();
      return;
    }
    const tab = button.dataset.profileDrawerTab;
    closeDrawers();
    navigateToProfileTab(tab);
  });
});
window.addEventListener("hashchange", () => {
  if (window.location.hash === "#main-content") return;
  ++poemPageState.version;
  window.clearTimeout(poemPageState.timer);
  if (!window.location.hash.startsWith("#poem/")) poemPageState.source = null;
  showRoute(routeFromHash(), { focus: true });
  const conversationId = conversationIdFromLocation();
  if (conversationId && currentUser) openConversation(conversationId).catch((error) => announce(`无法打开对话：${error.message}`));
});

document.querySelectorAll("[data-demo-action]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.demoAction === "login") {
      requireLocalProfile();
      return;
    }
    if (button.dataset.demoAction === "publish") openThreadDialog();
  });
});

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-open-new-chat]")) document.querySelector("#new-chat")?.click();
});

const avatarCropDialog = document.querySelector("#avatar-crop-dialog");
const avatarCropForm = document.querySelector("#avatar-crop-form");
const avatarCropCanvas = document.querySelector("#avatar-crop-canvas");
const avatarCropZoom = document.querySelector("#avatar-crop-zoom");
const avatarCropZoomValue = document.querySelector("#avatar-crop-zoom-value");
const avatarCropImage = new Image();
const avatarCropState = { objectUrl: "", file: null, centerX: 0, centerY: 0, pointerId: null, lastX: 0, lastY: 0 };

function avatarCropBounds() {
  const zoom = Number(avatarCropZoom.value) || 1;
  const sourceSize = Math.min(avatarCropImage.naturalWidth, avatarCropImage.naturalHeight) / zoom;
  return {
    zoom,
    sourceSize,
    minX: sourceSize / 2,
    maxX: avatarCropImage.naturalWidth - sourceSize / 2,
    minY: sourceSize / 2,
    maxY: avatarCropImage.naturalHeight - sourceSize / 2,
  };
}

function clampAvatarCropCenter() {
  const bounds = avatarCropBounds();
  avatarCropState.centerX = Math.min(bounds.maxX, Math.max(bounds.minX, avatarCropState.centerX || avatarCropImage.naturalWidth / 2));
  avatarCropState.centerY = Math.min(bounds.maxY, Math.max(bounds.minY, avatarCropState.centerY || avatarCropImage.naturalHeight / 2));
}

function drawAvatarCrop() {
  if (!avatarCropImage.naturalWidth) return;
  clampAvatarCropCenter();
  const { sourceSize } = avatarCropBounds();
  const context = avatarCropCanvas.getContext("2d");
  context.clearRect(0, 0, avatarCropCanvas.width, avatarCropCanvas.height);
  context.imageSmoothingQuality = "high";
  context.drawImage(avatarCropImage, avatarCropState.centerX - sourceSize / 2, avatarCropState.centerY - sourceSize / 2, sourceSize, sourceSize, 0, 0, avatarCropCanvas.width, avatarCropCanvas.height);
  avatarCropZoomValue.textContent = `${Math.round(Number(avatarCropZoom.value) * 100)}%`;
}

function closeAvatarCrop() {
  if (avatarCropDialog.open) avatarCropDialog.close();
  if (avatarCropState.objectUrl) URL.revokeObjectURL(avatarCropState.objectUrl);
  avatarCropState.objectUrl = ""; avatarCropState.file = null; avatarCropImage.removeAttribute("src");
}

document.querySelector("#profile-avatar-input")?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  const status = document.querySelector("#profile-status");
  if (!file) return;
  if (![/^image\/jpeg$/, /^image\/png$/, /^image\/webp$/].some((pattern) => pattern.test(file.type)) || file.size > 2 * 1024 * 1024) {
    status.textContent = "请选择 2MB 以内的 JPG、PNG 或 WebP 图片。"; event.target.value = ""; return;
  }
  closeAvatarCrop();
  avatarCropState.file = file;
  avatarCropState.objectUrl = URL.createObjectURL(file);
  avatarCropImage.onload = () => { avatarCropState.centerX = avatarCropImage.naturalWidth / 2; avatarCropState.centerY = avatarCropImage.naturalHeight / 2; avatarCropZoom.value = "1"; drawAvatarCrop(); avatarCropDialog.showModal(); };
  avatarCropImage.onerror = () => { status.textContent = "图片无法读取，请重新选择。"; closeAvatarCrop(); };
  avatarCropImage.src = avatarCropState.objectUrl;
  event.target.value = "";
});

avatarCropZoom?.addEventListener("input", drawAvatarCrop);
avatarCropCanvas?.addEventListener("pointerdown", (event) => { avatarCropState.pointerId = event.pointerId; avatarCropState.lastX = event.clientX; avatarCropState.lastY = event.clientY; avatarCropCanvas.setPointerCapture(event.pointerId); });
avatarCropCanvas?.addEventListener("pointermove", (event) => {
  if (avatarCropState.pointerId !== event.pointerId) return;
  const bounds = avatarCropBounds(); const rect = avatarCropCanvas.getBoundingClientRect();
  avatarCropState.centerX -= (event.clientX - avatarCropState.lastX) * bounds.sourceSize / rect.width;
  avatarCropState.centerY -= (event.clientY - avatarCropState.lastY) * bounds.sourceSize / rect.height;
  avatarCropState.lastX = event.clientX; avatarCropState.lastY = event.clientY; drawAvatarCrop();
});
avatarCropCanvas?.addEventListener("pointerup", (event) => { if (avatarCropState.pointerId === event.pointerId) avatarCropState.pointerId = null; });
avatarCropCanvas?.addEventListener("pointercancel", () => { avatarCropState.pointerId = null; });
document.querySelector("#avatar-crop-close")?.addEventListener("click", closeAvatarCrop);
document.querySelector("#avatar-crop-cancel")?.addEventListener("click", closeAvatarCrop);
avatarCropDialog?.addEventListener("cancel", closeAvatarCrop);
avatarCropForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!avatarCropState.file || !avatarCropImage.naturalWidth) return;
  const status = document.querySelector("#profile-status"); const confirm = document.querySelector("#avatar-crop-confirm");
  confirm.disabled = true; status.textContent = "头像上传中……";
  try {
    const blob = await new Promise((resolve, reject) => avatarCropCanvas.toBlob((value) => value ? resolve(value) : reject(new Error("裁剪失败，请重试。")), "image/jpeg", 0.9));
    currentUser = await apiFetch("/v1/profile/me/avatar", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob });
    closeAvatarCrop(); updateProfileUi(); status.textContent = "头像已更新。"; announce("头像已更新");
  } catch (error) { status.textContent = error.message; }
  finally { confirm.disabled = false; }
});

const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-input");
const chatModel = document.querySelector("#chat-model");
const chatAttachment = document.querySelector("#chat-attachment");
const chatAttachmentPreview = document.querySelector("#chat-attachment-preview");
const chatPromptButton = document.querySelector("#chat-prompt-button");
const chatThreadScroll = document.querySelector("#chat-thread-scroll");
const chatThread = document.querySelector("#chat-thread");
const chatLayout = document.querySelector(".chat-layout");
const conversationSearch = document.querySelector("#conversation-search");
const conversationSearchInput = document.querySelector("#conversation-search-input");
const chatTitle = document.querySelector("#chat-title");
const conversationTitleMaxLength = 15;
const poemScrollContentMaxWidth = 684;
function shortenConversationTitle(title = "新建对话") {
  return Array.from(String(title || "新建对话").replace(/\s+/g, " ").trim() || "新建对话").slice(0, conversationTitleMaxLength).join("");
}
function setChatTitle(title = "新建对话") {
  const value = shortenConversationTitle(title);
  if (!chatTitle) return;
  chatTitle.textContent = value;
  chatTitle.title = value;
}
const chatSidebarToggle = document.querySelector("#chat-sidebar-toggle");
const chatSidebarReopen = document.querySelector("#chat-sidebar-reopen");
function setChatSidebarCollapsed(collapsed) {
  chatLayout?.classList.toggle("is-sidebar-collapsed", collapsed);
  chatSidebarToggle?.setAttribute("aria-expanded", String(!collapsed));
  chatSidebarReopen?.setAttribute("aria-expanded", String(collapsed));
}
function syncChatSidebarViewport() {
  if (!chatLayout) return;
  const mobile = window.matchMedia("(max-width: 760px)").matches;
  if (mobile) setChatSidebarCollapsed(true);
  else setChatSidebarCollapsed(false);
}
syncChatSidebarViewport();
window.matchMedia("(max-width: 760px)").addEventListener?.("change", syncChatSidebarViewport);
function syncChatContentWidth() {
  if (!chatThreadScroll || !chatLayout) return;
  const available = chatThreadScroll.clientWidth;
  const width = window.matchMedia("(max-width: 760px)").matches
    ? Math.max(0, available - 36)
    : Math.min(poemScrollContentMaxWidth, Math.max(0, available - 48));
  chatLayout.style.setProperty("--chat-content-width", `${width}px`);
}
syncChatContentWidth();
new ResizeObserver(syncChatContentWidth).observe(chatThreadScroll);
window.addEventListener("resize", syncChatContentWidth);
const chatSubmit = chatForm.querySelector("button[type='submit']");
const chatCancel = document.querySelector("#chat-cancel");
const agentStatus = document.querySelector("#agent-status");
const initialThreadMarkup = chatThread.innerHTML;
function openChatPromptChooser() {
  let dialog = document.querySelector("#chat-prompt-chooser");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "chat-prompt-chooser";
    dialog.className = "chat-prompt-chooser";
    dialog.setAttribute("aria-labelledby", "chat-prompt-chooser-title");
    dialog.innerHTML = `
      <form method="dialog" class="chat-prompt-chooser__shell">
        <header class="chat-prompt-chooser__header">
          <div><p class="eyebrow">诗词创作</p><h2 id="chat-prompt-chooser-title">选择创作方式</h2></div>
          <button type="submit" class="icon-button" aria-label="关闭">×</button>
        </header>
        <div class="chat-prompt-chooser__options">
          <button type="button" data-prompt-mode="full"><strong>完整生成</strong><span>写下创作要求，选择体裁和格律，生成完整作品。</span></button>
          <button type="button" data-prompt-mode="partial"><strong>诗词补完</strong><span>补完残稿，或指定原诗中的句子进行改写。</span></button>
          <button type="button" data-prompt-mode="rewrite"><strong>局部重写</strong><span>导入作品ID，选择需要重写的句子，保留其余上下文。</span></button>
        </div>
      </form>`;
    dialog.querySelectorAll("[data-prompt-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!requireLocalProfile()) { dialog.close(); return; }
        dialog.close();
        openDirectGenerationEditor(button.dataset.promptMode);
      });
    });
    document.body.append(dialog);
  }
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}
chatPromptButton?.addEventListener("click", openChatPromptChooser);

async function openDirectGenerationEditor(mode) {
  const isPartial = mode === "partial" || mode === "rewrite";
  const isRewrite = mode === "rewrite";
  if (currentConversationReadOnly) {
    announce("当前对话为只读，无法在其中创建创作面板。");
    return;
  }
  const title = isRewrite ? "局部重写" : isPartial ? "部分生成" : "整首创作";
  if (!currentConversationId) setChatTitle(title);
  document.querySelector("#chat-form").hidden = false;
  chatSessionId = currentConversationId;
  const proposalId = `direct-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const prepared = {
    proposal_id: proposalId,
    direct: true,
    draft_key: `shiju:direct-draft:${mode}`,
    kind: isRewrite ? "rewrite" : isPartial ? "partial_generate" : "generate",
    editable_prompt: "",
    candidate_count: 1,
    proposal: {
      meter_type: "唐诗",
      form_name: "七言绝句",
      rhyme_dict_name: "Xinyun",
      rhyme_mode: "auto",
      rhyme_parts: {},
      fixed_lines: {},
      task_options: isRewrite ? { omit_original_target_from_prompt: true } : {},
      strict_polyphonic: false,
    },
  };
  if (isRewrite) prepared.proposal.rewrite_mode = true;
  const panel = appendProposalEditor(prepared);
  if (panel) panel.open = true;
  panel?.scrollIntoView({ behavior: "smooth", block: "center" });
  panel?.querySelector("textarea")?.focus({ preventScroll: true });
}
let chatSessionId = null;
let activeChatRequest = null;
let chatBusy = false;
let activeAssistantMessage = null;
let activeChatStopMode = null;
let activeTurnId = null;
let resumeTurnId = null;
const activeJobPolls = new Map();
let conversationRefreshTimer = null;
let conversationRefreshDelay = 1500;
let conversationLoadVersion = 0;
let profileGeneration = 0;
let activeConversationEvents = null;
let selectedChatAttachment = null;

function renderChatAttachment(file) {
  selectedChatAttachment = file || null;
  if (!chatAttachmentPreview) return;
  chatAttachmentPreview.replaceChildren();
  chatAttachmentPreview.hidden = !file;
  if (!file) return;
  const label = document.createElement("span");
  label.textContent = file.name;
  const remove = document.createElement("button");
  remove.type = "button"; remove.className = "chat-attachment-remove"; remove.textContent = "×";
  remove.title = "移除附件"; remove.setAttribute("aria-label", "移除附件");
  remove.addEventListener("click", () => { if (chatAttachment) chatAttachment.value = ""; renderChatAttachment(null); });
  chatAttachmentPreview.append(label, remove);
}
chatAttachment?.addEventListener("change", () => {
  const file = chatAttachment.files?.[0];
  if (!file) return;
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) { chatAttachment.value = ""; announce("请选择 PNG、JPEG 或 WebP 图片"); return; }
  if (file.size > 8 * 1024 * 1024) { chatAttachment.value = ""; announce("图片不能超过 8MB"); return; }
  renderChatAttachment(file);
});

async function loadConversations({ restore = false } = {}) {
  if (document.body.dataset.currentView !== "chat" && !leftDrawer?.classList.contains("is-open")) return;
  if (!currentUser) {
    document.querySelector("#conversation-list").innerHTML = '<div class="conversation-empty"><span class="conversation-empty__mark" aria-hidden="true">卷</span><strong>尚无对话记录</strong><span>开始一段新对话，创作与推敲将留存在这台电脑。</span><button type="button" data-open-new-chat>新建对话</button></div>';
    return;
  }
  const generation = profileGeneration;
  const linkedId = conversationIdFromLocation();
  try {
    const query = conversationSearchInput?.value.trim() || "";
    const payload = await apiFetch("/v1/conversations");
    if (generation !== profileGeneration) return;
    const list = document.querySelector("#conversation-list");
    list.innerHTML = "";
    if (!payload.items.length) {
      list.innerHTML = '<div class="conversation-empty conversation-empty--compact"><span class="conversation-empty__mark" aria-hidden="true">卷</span><strong>尚无历史对话</strong><span>新建一场对谈，开始推敲。</span></div>';
      if (restore && linkedId && linkedId !== currentConversationId) await openConversation(linkedId);
      return;
    }
    const sortedItems = [...payload.items].sort((a, b) => Number(b.activity_at || b.created_at || 0) - Number(a.activity_at || a.created_at || 0));
    sortedItems.forEach((item) => {
      const row = document.createElement("div"); row.className = "conversation-row";
      const displayTitle = shortenConversationTitle(item.title);
      const button = document.createElement("button"); button.type = "button"; button.textContent = displayTitle; button.dataset.conversationId = item.id;
      if (item.id === currentConversationId) button.setAttribute("aria-current", "true");
      const rename = document.createElement("button"); rename.type = "button"; rename.className = "conversation-rename"; rename.dataset.renameConversation = item.id; rename.title = "修改对话名称"; rename.setAttribute("aria-label", `修改对话名称：${displayTitle}`); rename.textContent = "改名";
      const archive = document.createElement("button"); archive.type = "button"; archive.className = "conversation-archive"; archive.dataset.archiveConversation = item.id; archive.title = "归档对话"; archive.setAttribute("aria-label", `归档对话：${displayTitle}`); archive.textContent = "归档";
      row.append(button); if (!item.restricted_at) row.append(rename, archive); list.append(row);
    });
    const activeConversation = payload.items.find((item) => item.id === currentConversationId);
    if (activeConversation) setChatTitle(activeConversation.title);
    if (restore && linkedId && linkedId !== currentConversationId) await openConversation(linkedId);
    filterConversations();
  } catch { /* 登录状态可能已过期，发送消息时会提示 */ }
}

async function openConversation(conversationId) {
  if (!requireLocalProfile()) return;
  const loadVersion = ++conversationLoadVersion;
  activeChatRequest?.abort();
  activeChatRequest = null;
  activeAssistantMessage = null;
  activeTurnId = null;
  editingMessageId = null;
  resumeTurnId = null;
  setChatBusy(true);
  clearJobProgressPolls();
  if (activeConversationEvents) activeConversationEvents.abort();
  clearConversationRefresh();
  chatThread.replaceChildren();
  currentConversationId = null;
  chatSessionId = null;
  agentStatus.textContent = "正在打开对话";
  const payload = await apiFetch(`/v1/conversations/${encodeURIComponent(conversationId)}`).catch((error) => {
    if (loadVersion === conversationLoadVersion) setChatBusy(false);
    throw error;
  });
  if (loadVersion !== conversationLoadVersion || !currentUser) return;
  currentConversationId = payload.id; chatSessionId = payload.id;
  const readOnly = Boolean(payload.read_only);
  currentConversationReadOnly = readOnly;
  document.querySelector("#chat-form").hidden = readOnly || Boolean(payload.restricted);
  if (payload.restricted) {
    chatThread.innerHTML = "";
    const notice = document.createElement("p"); notice.className = "empty-state"; notice.textContent = payload.restriction_notice || "该对话已被站长限制。"; chatThread.append(notice);
    agentStatus.textContent = "对话已限制";
    setChatBusy(false);
    return;
  }
  if (readOnly) agentStatus.textContent = `只读 · ${payload.owner?.display_name || "其他用户"}`;
  window.currentConversationBranches = Array.isArray(payload.branches) ? payload.branches : [];
  window.currentActiveBranchId = payload.active_branch_id || window.currentConversationBranches.find((item) => item.is_active)?.id || null;
  setConversationLocation(payload.id);
  setChatTitle(payload.title);
  localStorage.setItem(`shiju_conversation_${currentUser.id}`, payload.id);
  chatThread.innerHTML = "";
  payload.messages.forEach(appendPersistedMessage);
  const conversationProposal = readOnly ? null : payload.pending_proposal || payload.conversation_proposal;
  if (conversationProposal && payload.messages.some((message) => message.id === conversationProposal.assistant_message_id)) {
    const anchor = payload.messages.find((message) => message.id === conversationProposal.assistant_message_id);
    const anchorElement = anchor ? chatThread.querySelector(`[data-message-id="${CSS.escape(anchor.id)}"]`) : null;
    appendProposalEditor(conversationProposal, anchorElement);
  }
  const jobs = readOnly ? [] : await loadConversationJobs(payload).catch((error) => {
    if (loadVersion === conversationLoadVersion) setChatBusy(false);
    throw error;
  });
  if (loadVersion !== conversationLoadVersion) return;
  if (jobs === null) { await openConversation(conversationId); return; }
  [...jobs].sort((left, right) => Number(left.created_at || 0) - Number(right.created_at || 0)).forEach((job) => {
    const anchor = findHistoricalJobAnchor(job, payload.messages);
    let proposalAnchor = chatThread.querySelector(`.proposal-editor[data-job-id="${CSS.escape(job.job_id)}"]`);
    if (!proposalAnchor && job.request?.requirement) {
      proposalAnchor = appendProposalEditor({
        proposal_id: `submitted-${job.job_id}`,
        direct: true,
        submitted_job_id: job.job_id,
        created_at: job.created_at,
        kind: job.kind,
        editable_prompt: job.request.requirement,
        candidate_count: job.request.candidate_count,
        proposal: {
          meter_type: job.request.meter_type,
          form_name: job.request.form_name,
          variant_name: job.request.variant_name,
          rhyme_dict_name: job.request.rhyme_dict_name,
          rhyme_mode: job.request.rhyme_mode,
          rhyme_parts: job.request.rhyme_parts,
          fixed_lines: job.request.fixed_lines,
          sentence_inputs: job.request.sentence_inputs,
          original_text: job.request.original_text,
          target_line_numbers: job.request.target_line_numbers,
          source_poem_id: job.request.source_poem_id,
          source_version: job.request.source_version,
          task_options: job.request.task_options,
          strict_polyphonic: job.request.strict_polyphonic,
          num_lines: job.request.num_lines,
        },
      }, anchor);
    }
    startJobProgress(job.job_id, job, proposalAnchor || anchor, payload.id);
  });
  document.querySelectorAll("#conversation-list button[data-conversation-id]").forEach((button) => button.toggleAttribute("aria-current", button.dataset.conversationId === payload.id));
  agentStatus.textContent = readOnly ? `只读 · ${payload.owner?.display_name || "其他用户"}` : "已连接";
  setChatBusy(false);
  if (payload.messages.some((message) => message.status === "pending")) {
    scheduleConversationRefresh(payload.id);
  }
}

async function loadConversationJobs(conversation) {
  try {
    const payload = await apiFetch(`/v1/conversations/${encodeURIComponent(conversation.id)}/state`);
    if (payload.messages && (payload.messages.length !== conversation.messages.length
      || payload.messages.some((message, index) => message.id !== conversation.messages[index]?.id))) return null;
    return (payload.jobs || []).filter((job) => ["generate", "rewrite", "partial_generate"].includes(job.kind));
  } catch (error) {
    if (error.status !== 404) throw error;
    const ids = new Set(conversation.messages.map((message) => message.job_id).filter(Boolean));
    const jobs = await Promise.all([...ids].map((id) => apiFetch(`/v1/poetry/jobs/${id}/state`).catch(() => null)));
    return jobs.filter((job) => job && ["generate", "rewrite", "partial_generate"].includes(job.kind));
  }
}

function appendPersistedMessage(message) {
  const state = message.status === "pending" ? "pending" : message.status === "failed" ? "error" : message.status === "paused" || message.status === "interrupted" ? "paused" : "";
  const article = appendMessage(message.content, message.role, state, { ...message, images: chatImagePreviews.get(message.id) || [] });
  article.dataset.messageId = message.id;
  article.dataset.messageStatus = message.status || "completed";
  article.dataset.executionPhase = message.status === "pending" ? "running" : "finished";
  article.dataset.branchId = window.currentActiveBranchId || "";
  if (message.turn_id) article.dataset.turnId = message.turn_id;
  if (!currentConversationReadOnly && message.role === "assistant" && ["paused", "interrupted"].includes(message.status) && message.turn_id) appendAssistantContinueButton(article);
  if (message.job_id) {
    article.dataset.jobId = message.job_id;
  }
  restoreToolActivity(article, message.tool_activity);
  return article;
}

function clearConversationRefresh() {
  if (conversationRefreshTimer) window.clearTimeout(conversationRefreshTimer);
  conversationRefreshTimer = null;
  conversationRefreshDelay = 1500;
}

function conversationHasPendingMessages() {
  return Boolean(currentConversationId && chatThread.querySelector('[data-message-status="pending"]'));
}

function scheduleConversationRefresh(conversationId) {
  if (conversationRefreshTimer) window.clearTimeout(conversationRefreshTimer);
  conversationRefreshTimer = window.setTimeout(() => refreshConversationMessages(conversationId), conversationRefreshDelay);
}

async function refreshConversationMessages(conversationId) {
  if (currentConversationId !== conversationId || document.body.dataset.currentView !== "chat") return;
  const refreshVersion = conversationLoadVersion;
  try {
    const payload = await apiFetch(`/v1/conversations/${encodeURIComponent(conversationId)}/messages`);
    if (refreshVersion !== conversationLoadVersion || currentConversationId !== conversationId) return;
    // The active branch may have changed in another tab or after a completed turn.
    // Re-render the authoritative path instead of leaving stale branch descendants
    // in the linear chat DOM.
    const renderedIds = [...chatThread.querySelectorAll("[data-message-id]")]
      .map((node) => node.dataset.messageId)
      .filter(Boolean);
    const persistedIds = payload.items.map((message) => message.id);
    if (renderedIds.length !== persistedIds.length || renderedIds.some((id, index) => id !== persistedIds[index])) {
      await openConversation(conversationId);
      return;
    }
    if (payload.items.some((message) => message.status !== "pending"
      && chatThread.querySelector(`[data-message-id="${CSS.escape(message.id)}"]`)?.dataset.messageStatus === "pending")) {
      await openConversation(conversationId);
      return;
    }
    let hasPending = false;
    payload.items.forEach((message) => {
      hasPending ||= message.status === "pending";
      const article = chatThread.querySelector(`[data-message-id="${message.id}"]`);
      if (!article) {
        const created = appendPersistedMessage(message);
        if (message.job_id && message.status === "pending") startJobProgress(message.job_id, null, created, conversationId);
        return;
      }
      restoreToolActivity(article, message.tool_activity);
      if (article.dataset.messageStatus !== message.status || article.dataset.messageText !== String(message.content || "")) {
        const state = message.status === "pending" ? "pending" : message.status === "failed" ? "error" : "";
        replaceMessage(article, message.content, state);
        article.dataset.messageStatus = message.status || "completed";
      }
      if (message.job_id && message.status === "pending") {
        article.dataset.jobId = message.job_id;
        const proposalAnchor = chatThread.querySelector(`.proposal-editor[data-job-id="${CSS.escape(message.job_id)}"]`);
        startJobProgress(message.job_id, null, proposalAnchor || article, conversationId);
      }
    });
    if (hasPending && refreshVersion === conversationLoadVersion && currentConversationId === conversationId) {
      conversationRefreshDelay = Math.min(conversationRefreshDelay * 2, 10000);
      scheduleConversationRefresh(conversationId);
    } else {
      conversationRefreshDelay = 1500;
    }
  } catch {
    if (refreshVersion === conversationLoadVersion && currentConversationId === conversationId) {
      conversationRefreshDelay = Math.min(conversationRefreshDelay * 2, 10000);
      scheduleConversationRefresh(conversationId);
    }
  }
}

let profilePoems = [];
let ownedPoems = [];
let favoritePoems = [];
let ownedPoemsHasMore = false;
let ownedPoemsLoadingMore = false;
let ownedPoemsRequestVersion = 0;
let favoriteThreads = [];
let favoriteThreadCollections = [];
function collectionKey(name) { return String(name || "").trim().replace(/\s+/g, " ").toLocaleLowerCase(); }
function mergeNamedCollections(items) {
  const merged = new Map();
  for (const item of items || []) {
    item.id ||= crypto.randomUUID(); item.items ||= [];
    const key = collectionKey(item.name);
    const current = merged.get(key);
    if (!current) merged.set(key, { ...item, name: String(item.name || "未命名合集").trim(), duplicate_ids: [...(item.duplicate_ids || [])] });
    else {
      current.duplicate_ids.push(item.id, ...(item.duplicate_ids || []));
      current.items = [...new Set([...current.items, ...item.items])];
      current.count = Number(current.count || 0) + Number(item.count || 0);
    }
  }
  return [...merged.values()];
}
let pendingCollection = null;
async function migrateLegacyThreadCollections(userId) {
  const marker = `shiju_thread_collections_migrated_${userId}`;
  if (localStorage.getItem(marker)) return;
  const legacyFavoriteThreads = JSON.parse(localStorage.getItem("shiju_favorite_threads") || "[]");
  const legacyThreadCollections = JSON.parse(localStorage.getItem("shiju_thread_collections") || "[]");
  const collections = mergeNamedCollections(legacyThreadCollections).map(({ name, items }) => ({ name, items }));
  const favorites = [...new Set(legacyFavoriteThreads.map((item) => item.id).filter(Boolean))];
  if (!collections.length && !favorites.length) { localStorage.setItem(marker, "1"); return; }
  await apiFetch("/v1/profile/thread-collections/import-legacy", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ collections, favorite_threads: favorites }) });
  localStorage.setItem(marker, "1");
  localStorage.removeItem("shiju_thread_collections");
  localStorage.removeItem("shiju_favorite_threads");
}
async function openCollectionManager(kind) {
  await loadCollectionData(kind);
  pendingCollection = { kind, manage: true };
  const modal = document.querySelector("#collection-modal");
  const list = document.querySelector("#collection-modal-list");
  const collections = kind === "poem" ? profileCollections : favoriteThreadCollections;
  document.querySelector("#collection-modal-title").textContent = kind === "poem" ? "管理作品合集" : "管理帖子合集";
  document.querySelector(".collection-modal__dialog > .eyebrow").textContent = kind === "poem" ? "收藏的作品" : "收藏的帖子";
  document.querySelector("#collection-create-form").hidden = true;
  document.querySelector("#collection-create-status").textContent = "";
  document.querySelector("#collection-name-input").value = "";
  document.querySelector("#collection-modal-new").hidden = false;
  document.querySelector("#collection-modal-new").textContent = "＋ 新建合集";
  list.replaceChildren();
  if (!collections.length) list.innerHTML = '<p class="empty-state">还没有合集。</p>';
  collections.forEach((collection) => {
    const row = document.createElement("div"); row.className = "collection-manage-row";
    const name = document.createElement("strong"); name.textContent = collection.name;
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "text-button"; remove.textContent = "删除"; remove.dataset.deleteCollection = collection.id;
    row.append(name, remove); list.append(row);
  });
  modal.hidden = false;
}
async function openCollectionPicker(kind, item, onDone) {
  if (kind) await loadCollectionData(kind);
  pendingCollection = { kind, item, onDone }; const modal = document.querySelector("#collection-modal"); const list = document.querySelector("#collection-modal-list");
  document.querySelector("#collection-modal-title").textContent = "选择合集";
  document.querySelector(".collection-modal__dialog > .eyebrow").textContent = "收藏整理";
  document.querySelector("#collection-create-form").hidden = true; document.querySelector("#collection-create-status").textContent = ""; document.querySelector("#collection-name-input").value = "";
  if (!kind) { list.innerHTML = '<p class="collection-modal-hint">请选择要新建的合集类型</p>'; [ ["poem", "作品合集"], ["thread", "帖子合集"] ].forEach(([value, label]) => { const b = document.createElement("button"); b.type = "button"; b.className = "collection-choice"; b.textContent = `＋ 新建${label}`; b.addEventListener("click", () => openCollectionPicker(value, null)); list.append(b); }); document.querySelector("#collection-modal-new").hidden = true; modal.hidden = false; return; }
  document.querySelector("#collection-modal-new").hidden = false;
  const names = kind === "poem" ? profileCollections.map((x) => x.name) : favoriteThreadCollections.map((x) => x.name); list.replaceChildren();
  if (!names.length) list.innerHTML = '<p class="empty-state">暂无合集，请先新建。</p>';
  names.forEach((name) => { const b = document.createElement("button"); b.type = "button"; b.className = "collection-choice"; b.textContent = name; b.addEventListener("click", async () => { try { await onDone?.(name); modal.hidden = true; } catch (error) { announce(`移动失败：${error.message}`); } }); list.append(b); }); modal.hidden = false;
}

function findHistoricalJobAnchor(job, messages) {
  const direct = chatThread.querySelector(`[data-job-id="${CSS.escape(job.job_id)}"]`);
  if (direct) return direct;
  const message = (messages || []).find((item) => item.id === job.assistant_message_id || item.job_id === job.job_id);
  return message ? chatThread.querySelector(`[data-message-id="${CSS.escape(message.id)}"]`) : null;
}

function insertChatTimelineNode(node, createdAt, anchor = null) {
  const timestamp = Number(createdAt);
  node.dataset.createdAt = String(Number.isFinite(timestamp) && timestamp > 0
    ? timestamp : Number(anchor?.dataset.createdAt) || Date.now() / 1000);
  if (anchor?.parentElement === chatThread) {
    const children = [...chatThread.children];
    const following = children.slice(children.indexOf(anchor) + 1);
    const boundary = following.find((child) => child.dataset.messageId
      || Number(child.dataset.createdAt) > Number(node.dataset.createdAt));
    if (boundary) chatThread.insertBefore(node, boundary);
    else chatThread.append(node);
    return;
  }
  const later = [...chatThread.children].find((child) =>
    Number(child.dataset.createdAt) > Number(node.dataset.createdAt));
  if (later) chatThread.insertBefore(node, later);
  else if (anchor?.parentElement === chatThread
    && Number(anchor.dataset.createdAt) === Number(node.dataset.createdAt)) anchor.after(node);
  else chatThread.append(node);
}
function confirmAction(message, { title = "确认操作", confirmLabel = "确认" } = {}) {
  const modal = document.querySelector("#confirm-modal");
  modal.querySelector("#confirm-modal-title").textContent = title;
  modal.querySelector("#confirm-modal-message").textContent = message;
  modal.querySelector("#confirm-modal-submit").textContent = confirmLabel;
  modal.hidden = false;
  return new Promise((resolve) => {
    const finish = (value) => { modal.hidden = true; modal.querySelector("#confirm-modal-submit").onclick = null; modal.querySelectorAll("[data-confirm-close]").forEach((el) => { el.onclick = null; }); resolve(value); };
    modal.querySelector("#confirm-modal-submit").onclick = () => finish(true);
    modal.querySelectorAll("[data-confirm-close]").forEach((el) => { el.onclick = () => finish(false); });
  });
}
document.querySelectorAll("[data-collection-close]").forEach((el) => el.addEventListener("click", () => { document.querySelector("#collection-modal").hidden = true; document.querySelector("#collection-create-form").hidden = true; document.querySelector("#collection-modal-new").hidden = false; }));
document.querySelector("#collection-modal-new")?.addEventListener("click", () => { document.querySelector("#collection-modal-new").hidden = true; document.querySelector("#collection-create-form").hidden = false; document.querySelector("#collection-name-input").focus(); });
document.querySelector("#collection-create-submit")?.addEventListener("click", async () => {
  if (!pendingCollection) return;
  const input = document.querySelector("#collection-name-input");
  const status = document.querySelector("#collection-create-status");
  const clean = input.value.trim();
  if (!clean) { status.textContent = "请输入合集名称"; return; }
  const target = pendingCollection.kind === "poem" ? profileCollections : favoriteThreadCollections;
  if (target.some((item) => collectionKey(item.name) === collectionKey(clean))) { status.textContent = "同类合集不能重名"; return; }
  try {
    const pending = pendingCollection;
    const endpoint = pending.kind === "poem" ? "/v1/profile/poem-collections" : "/v1/profile/thread-collections";
    await apiFetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: clean }) });
    input.value = "";
    document.querySelector("#collection-create-form").hidden = true;
    document.querySelector("#collection-modal-new").hidden = false;
    await loadCollectionData(pending.kind);
    if (pending.manage) openCollectionManager(pending.kind);
    else {
      await pending.onDone?.(clean);
      document.querySelector("#collection-modal").hidden = true;
      await loadProfileData();
    }
  } catch (error) { status.textContent = error.message; }
});
let profileCollections = [];
let profileLoadVersion = 0;

async function loadCollectionData(kind) {
  const generation = profileGeneration;
  if (kind === "thread") await migrateLegacyThreadCollections(currentUser.id);
  const payload = await apiFetch(`/v1/profile/${kind === "poem" ? "poem" : "thread"}-collections`);
  if (generation !== profileGeneration) return;
  const collections = mergeNamedCollections(payload.items || []);
  if (kind === "poem") profileCollections = collections;
  else {
    favoriteThreadCollections = collections;
    favoriteThreads = [...new Set(collections.flatMap((collection) => collection.items || []))].map((id) => ({ id }));
  }
}

function parsePoemFields(poem) {
  const raw = String(poem.content || "");
  const protocol = raw.match(/\[title\]\s*([\s\S]*?)\s*\[content\]\s*([\s\S]*)/i);
  return { ...poem, title: protocol?.[1]?.trim() || poem.title || "未命名诗作", content: protocol?.[2]?.trim() || raw };
}

function poemStanzas(content) {
  return String(content || "")
    .trim()
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph
      .split(/\n+/)
      .flatMap((line) => line.match(/[^，。！？；!?]+[，。！？；!?]?/g) || [line])
      .map((line) => line.trim())
      .filter(Boolean))
    .filter((stanza) => stanza.length);
}

function isSongCi(poem) {
  if (String(poem.evaluation?.type || poem.type || "").toLowerCase() === "songci") return true;
  return [poem.work_type, poem.meter_type, poem.form_name]
    .filter(Boolean)
    .some((value) => /宋词|词牌|词|song\s*ci|songci|^ci$|_ci$/i.test(String(value)));
}

function songCiStanzas(content) {
  return String(content || "")
    .trim()
    .split(/\n+/)
    .map((stanza) => stanza.trim())
    .filter(Boolean)
    .map((stanza) => [stanza]);
}

function groupSongCiStanzas(stanzas, stanzaEnds) {
  if (!stanzaEnds?.length || !stanzas.length) return stanzas;
  const groups = [];
  let start = 0;
  for (const end of stanzaEnds) {
    const group = stanzas.slice(start, end + 1);
    if (group.length) groups.push(group);
    start = end + 1;
  }
  if (start < stanzas.length) {
    if (groups.length) groups[groups.length - 1].push(...stanzas.slice(start));
    else groups.push(stanzas.slice(start));
  }
  return groups.length ? groups : stanzas;
}

function isTangPoem(poem) {
  return [poem.work_type, poem.meter_type, poem.form_name]
    .filter(Boolean)
    .some((value) => /唐诗|律诗|绝句|排律|五言|七言/.test(String(value)));
}

function poemGenreLabel(poem) {
  const values = [poem.work_type, poem.meter_type, poem.form_name]
    .filter(Boolean)
    .map((value) => String(value));
  if (values.some((value) => /宋词|词牌|词$/.test(value))) return "宋词";
  if (values.some((value) => /汉俳|俳句/.test(value))) return "汉俳";
  if (values.some((value) => /排律/.test(value))) return "排律";
  if (values.some((value) => /唐诗|律诗|绝句/.test(value))) return "唐诗";
  return poem.work_type || "诗词";
}

function poemCreatedDate(value) {
  if (value == null || value === "") return null;
  const numeric = Number(value);
  const date = Number.isFinite(numeric)
    ? new Date(numeric > 1e12 ? numeric : numeric * 1000)
    : new Date(String(value));
  return Number.isNaN(date.getTime()) ? null : date;
}

const poemTextMeasurer = document.createElement("canvas").getContext("2d");

function updatePoemLayout(article) {
  const verses = article.querySelector(".poem-handscroll__verses");
  const lines = [...article.querySelectorAll(".poem-handscroll__line")];
  if (!verses || !lines.length) return;
  const versesStyle = getComputedStyle(verses);
  const lineStyle = getComputedStyle(lines[0]);
  poemTextMeasurer.font = lineStyle.font;
  const lineWidths = lines.map((line) => poemTextMeasurer.measureText(line.textContent).width);
  const horizontalNaturalWidth = Math.max(...[...article.querySelectorAll(".poem-handscroll__stanza")].map((stanza) => {
    const stanzaWidths = [...stanza.querySelectorAll(".poem-handscroll__line")]
      .map((line) => {
        return poemTextMeasurer.measureText(line.textContent).width;
      });
    if (!article.classList.contains("poem-handscroll--tang")) return Math.max(...stanzaWidths);
    let widestRow = 0;
    for (let index = 0; index < stanzaWidths.length; index += 2) {
      widestRow = Math.max(widestRow, (stanzaWidths[index] || 0) + (stanzaWidths[index + 1] || 0));
    }
    return widestRow;
  }));
  const wasVertical = article.classList.contains("poem-handscroll--vertical");
  if (wasVertical) article.classList.remove("poem-handscroll--vertical");
  // 收藏作品嵌套在合集卡片中；用作品页列表的可用宽度布局，避免
  // details/grid 的内边距让同一首诗得到不同方向。
  const collectionScroll = Boolean(article.closest("#collection-list"));
  let horizontalWidth = article.parentElement?.clientWidth || article.clientWidth || verses.clientWidth || 855;
  if (collectionScroll) {
    const profileContent = article.closest(".profile-content");
    if (profileContent) {
      const profileStyle = getComputedStyle(profileContent);
      const innerWidth = profileContent.clientWidth
        - parseFloat(profileStyle.paddingLeft)
        - parseFloat(profileStyle.paddingRight);
      horizontalWidth = Math.min(innerWidth - (window.matchMedia("(max-width: 760px)").matches ? 8 : 0), 684);
    }
  }
  horizontalWidth = Math.max(1, Math.min(horizontalWidth, 855));
  article.classList.add("poem-handscroll--vertical");
  const verticalRect = article.getBoundingClientRect();
  // 先假想完成竖卷排版，再按卷轴正文区域的高宽比决定方向。
  // 页脚在作品页和合集页有不同的按钮排版，不能让它影响方向结果。
  const footerHeight = article.querySelector(".poem-handscroll__footer")?.getBoundingClientRect().height || 0;
  const verticalContentHeight = Math.max(0, verticalRect.height - footerHeight);
  const vertical = verticalContentHeight / Math.max(1, verticalRect.width) >= 1.6;
  article.classList.toggle("poem-handscroll--vertical", vertical);
  if (collectionScroll) article.style.width = vertical ? "" : `${horizontalWidth}px`;

  const contentWidth = verses.clientWidth - parseFloat(versesStyle.paddingLeft) - parseFloat(versesStyle.paddingRight);
  const widestLine = Math.max(...lineWidths);
  article.classList.toggle("poem-handscroll--paired", !vertical && article.classList.contains("poem-handscroll--tang") && widestLine * 2 <= contentWidth);
}

const poemLayoutObserver = typeof ResizeObserver === "function"
  ? new ResizeObserver((entries) => entries.forEach(({ target }) => updatePoemLayout(target)))
  : null;

function scoreText(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2).replace(/\.00$/, "") : "--";
}

async function openPoemEditorFromScroll(poem) {
  if (!requireLocalProfile()) return;
  try {
    const source = poem.id
      ? { id: poem.id }
      : await apiFetch(`/v1/profile/poems/by-job/${encodeURIComponent(poem.job_id)}/${Number(poem.candidate_ordinal)}`);
    poemPageState.returnHash = window.location.hash || "#profile";
    window.location.hash = `poem/edit/${encodeURIComponent(source.id)}`;
  } catch (error) {
    announce(`无法打开作品：${error.message}`);
  }
}

function poemDraftKey(id) {
  return `shiju:poem-edit:${currentUser?.id || "guest"}:${id}`;
}

// 保存前只比较诗词字符本身；换行、空格和缩进不构成正文修改。
function normalizedPoemContent(value) {
  return String(value || "").replace(/[\s\u200b\uFEFF]+/g, "");
}

async function loadPoemTree(poemId, existingTree = null) {
  const section = document.querySelector("#poem-version-tree");
  const container = document.querySelector("#poem-version-tree-nodes");
  const tree = existingTree || await apiFetch(`/v1/profile/poems/${encodeURIComponent(poemId)}/tree`);
  if (poemPageState.source?.id !== poemId) return;
  container.replaceChildren();
  poemPageState.treeNodeCount = tree.tree_node_count ?? tree.nodes.length;
  poemPageState.tree = tree;
  poemPageState.source.can_delete = Boolean(tree.nodes.find((node) => node.id === poemId)?.can_delete);
  document.querySelector("#poem-editor-delete").hidden = !poemPageState.source.can_delete;
  const byId = new Map(tree.nodes.map((node) => [node.id, node]));
  const children = new Map();
  for (const node of tree.nodes) {
    const parent = byId.has(node.parent_poem_id) ? node.parent_poem_id : null;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(node);
  }
  for (const siblings of children.values()) siblings.sort((a, b) => Number(a.created_at) - Number(b.created_at) || a.id.localeCompare(b.id));
  const visited = new Set();
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const renderNode = (node, depth) => {
    visited.add(node.id);
    const item = document.createElement("li");
    const row = document.createElement("div");
    row.className = `poem-version-tree__node${node.id === poemId ? " is-current" : ""}${node.available ? "" : " is-unavailable"}`;
    row.dataset.depth = depth;
    row.dataset.poemId = node.id;
    if (node.id === poemId) row.setAttribute("aria-current", "page");
    const entry = document.createElement(node.available && node.id !== poemId ? "a" : "span");
    entry.className = "poem-version-tree__link";
    if (entry.tagName === "A") entry.href = `#poem/${window.location.hash.startsWith("#poem/edit/") ? "edit" : "view"}/${encodeURIComponent(node.id)}`;
    const name = document.createElement("strong"); name.textContent = node.title;
    const author = document.createElement("small"); author.textContent = node.author_display_name || "";
    const time = document.createElement("time");
    const date = new Date(Number(node.created_at) * 1000);
    if (Number.isFinite(date.getTime())) {
      time.dateTime = date.toISOString();
      time.textContent = dateFormat.format(date);
      time.title = `${depth ? "修改" : "创作"}时间（北京时间）`;
    }
    if (node.can_mark) {
      const marker = document.createElement("button");
      marker.type = "button";
      marker.className = `poem-version-tree__marker${node.is_marked ? " is-marked" : ""}`;
      marker.textContent = node.is_marked ? "取消显示" : "显示";
      marker.title = node.is_marked ? "取消固定此作品，恢复默认显示" : "让卷轴固定显示此作品";
      marker.setAttribute("aria-label", marker.title);
      marker.setAttribute("aria-pressed", String(Boolean(node.is_marked)));
      marker.addEventListener("click", async () => {
        marker.disabled = true;
        try {
          await apiFetch(`/v1/profile/poems/${encodeURIComponent(node.id)}/forest-marker`, { method: node.is_marked ? "DELETE" : "POST" });
          announce(node.is_marked ? "已取消固定，恢复默认显示" : "已设为卷轴默认作品");
          await loadPoemTree(poemId);
        } catch (error) {
          marker.disabled = false;
          announce(`${node.is_marked ? "取消显示" : "标记"}失败：${error.message}`);
        }
      });
      row.append(marker);
    }
    entry.append(name, author, time); row.append(entry);
    item.append(row);
    const descendants = (children.get(node.id) || []).filter((child) => !visited.has(child.id));
    if (descendants.length) {
      const group = document.createElement("ul");
      for (const child of descendants) if (!visited.has(child.id)) group.append(renderNode(child, depth + 1));
      item.append(group);
    }
    return item;
  };
  const roots = document.createElement("ul");
  roots.className = "poem-version-tree__roots";
  for (const node of children.get(null) || []) roots.append(renderNode(node, 0));
  for (const node of tree.nodes) if (!visited.has(node.id)) roots.append(renderNode(node, 0));
  container.append(roots);
  section.hidden = !tree.nodes.length;
}

async function deleteCurrentPoem() {
  const source = poemPageState.source;
  if (!source || !source.can_delete) return;
  if (!await confirmAction("确定删除这首作品吗？", { title: "删除作品", confirmLabel: "删除" })) return;
  try {
    await apiFetch(`/v1/profile/poems/${encodeURIComponent(source.id)}`, { method: "DELETE" });
    announce("作品已删除");
    await loadPoems({ source: "owned" });
    const tree = poemPageState.tree;
    const fallback = [...(tree?.nodes || [])].filter((candidate) => candidate.available && candidate.id !== source.id)
      .sort((left, right) => Number(right.created_at) - Number(left.created_at) || right.id.localeCompare(left.id))[0];
    if (fallback) {
      const mode = window.location.hash.startsWith("#poem/edit/") ? "edit" : "view";
      window.location.hash = `poem/${mode}/${encodeURIComponent(fallback.id)}`;
    } else window.location.hash = poemPageState.returnHash || "profile";
  } catch (error) { announce(`删除失败：${error.message}`); }
}

document.querySelector("#poem-editor-delete").addEventListener("click", deleteCurrentPoem);

function renderPoemEditorIdentity(poemId) {
  const value = document.querySelector("#poem-editor-id");
  value.replaceChildren();
  const id = document.createElement("code"); id.textContent = poemId;
  const copy = document.createElement("button"); copy.type = "button"; copy.className = "quiet-button";
  copy.textContent = "复制 ID"; copy.setAttribute("aria-label", "复制当前作品 ID");
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(poemId); announce("作品 ID 已复制"); }
    catch (_) { announce("复制失败，请选中作品 ID 手动复制"); }
  });
  const remove = document.querySelector("#poem-editor-delete");
  remove.hidden = !poemPageState.source?.can_delete;
  value.append(id, copy);
}

function editorLines(content) {
  return String(content).split(/[，,。.？！；;!?\r\n]+/).map((line) => line.trim()).filter(Boolean);
}

let poemSentenceSelection = null;
let poemSentencePatterns = [];
let poemSentenceCommaPositions = [];
let poemSentenceStanzaEnds = [];
let poemSentenceQueryVersion = 0;
let poemSentenceQueryCacheKey = null;
let poemSentenceQueryPendingKey = null;
let poemSentenceVariationTexts = [];
let poemSentenceVariationSelection = null;
function clearPoemSentenceVariations() {
  poemSentenceVariationTexts = [];
  poemSentenceVariationSelection = null;
  document.querySelector("#poem-sentence-variations").replaceChildren();
}
function setPoemReviewDrawer(open) {
  const panel = document.querySelector("#poem-editor-review");
  const toggle = document.querySelector("#poem-review-drawer-toggle");
  const scrim = document.querySelector("#poem-review-drawer-scrim");
  panel.classList.toggle("is-open", open);
  toggle.setAttribute("aria-expanded", String(open));
  toggle.textContent = open ? "收起查询" : "句子查询";
  scrim.hidden = !open;
}
function updatePoemReviewToggle() {
  const toggle = document.querySelector("#poem-review-drawer-toggle");
  const selected = poemSentenceSelection !== null;
  toggle.hidden = !selected;
  if (!selected) setPoemReviewDrawer(false);
}
document.querySelector("#poem-review-drawer-toggle").addEventListener("click", () => {
  const open = document.querySelector("#poem-review-drawer-toggle").getAttribute("aria-expanded") !== "true";
  setPoemReviewDrawer(open);
});
document.querySelector("#poem-review-drawer-scrim").addEventListener("click", () => setPoemReviewDrawer(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.querySelector("#poem-review-drawer-toggle").getAttribute("aria-expanded") === "true") {
    setPoemReviewDrawer(false);
    document.querySelector("#poem-review-drawer-toggle").focus();
  }
});
function poemSentenceParts() {
  return [...document.querySelector("#poem-editor-content").value.matchAll(/[^，,。.？！；;!?\r\n]+[，,。.？！；;!?]?/gu)].filter((part) => part[0].trim());
}
function renderPoemSentences() {
  const list = document.querySelector("#poem-editor-sentences");
  list.replaceChildren();
  const parts = poemSentenceParts();
  if (!parts.length) return;
  const stanzaEnds = poemSentenceStanzaEnds.length ? poemSentenceStanzaEnds : [parts.length - 1];
  const stanzas = [];
  let cursor = 0;
  for (const stanzaEnd of stanzaEnds) {
    if (cursor >= parts.length) break;
    const limit = Math.min(stanzaEnd + 1, parts.length);
    stanzas.push(parts.slice(cursor, limit).map((part, offset) => ({ part, index: cursor + offset })));
    cursor = limit;
  }
  if (cursor < parts.length) stanzas.push(parts.slice(cursor).map((part, offset) => ({ part, index: cursor + offset })));
  stanzas.forEach((stanza, stanzaIndex) => {
    const line = document.createElement("div"); line.className = "poem-stanza"; line.setAttribute("role", "group");
    line.setAttribute("aria-label", `第 ${stanzaIndex + 1} 阕`);
    stanza.forEach(({ part, index }) => {
    const row = document.createElement("div"); row.className = "poem-sentence";
    row.classList.toggle("is-selected", index === poemSentenceSelection);
    const sentenceText = part[0].trim();
    const pick = document.createElement("button"); pick.type = "button"; pick.className = "poem-sentence-text";
    pick.setAttribute("aria-pressed", String(index === poemSentenceSelection));
    pick.setAttribute("aria-label", `选择第 ${index + 1} 句：${sentenceText}`);
    const patternText = poemSentencePatterns[index] || "格律模板未提供";
    const punctuation = /[，,。.？！；;!?]$/u.test(sentenceText) ? sentenceText.slice(-1) : "";
    const body = punctuation ? sentenceText.slice(0, -1) : sentenceText;
    const patternBody = patternText.length === sentenceText.length && punctuation ? patternText.slice(0, -1) : patternText;
    const alignLength = Math.max(body.length, patternBody.length);
    const alignedBody = body.padEnd(alignLength, "　");
    const alignedPattern = patternBody.padEnd(alignLength, " ");
    const makeAlignedText = (target, text, pattern = false) => {
      [...text].forEach((character) => {
        const cell = document.createElement("span"); cell.className = "poem-sentence-cell";
        if (pattern && character === "中") cell.classList.add("is-neutral");
        cell.textContent = character;
        target.append(cell);
      });
    };
    makeAlignedText(pick, alignedBody);
    if (punctuation) { const cell = document.createElement("span"); cell.className = "poem-sentence-cell poem-sentence-punctuation"; cell.textContent = punctuation; pick.append(cell); }
    pick.addEventListener("click", () => {
      if (poemSentenceSelection === index) return;
      poemSentenceSelection = index; ++poemSentenceQueryVersion;
      poemSentenceQueryCacheKey = null;
      poemSentenceQueryPendingKey = null;
      document.querySelector("#poem-sentence-query-state").textContent = `正在查询第 ${index + 1} 句……`;
      clearPoemSentenceVariations();
      updatePoemReviewToggle();
      if (window.matchMedia("(max-width: 780px)").matches) setPoemReviewDrawer(true);
      querySelectedPoemSentence();
      renderPoemSentences();
      document.querySelectorAll(".poem-sentence-text")[index]?.focus({ preventScroll: true });
    });
    const pattern = document.createElement("div"); pattern.className = "poem-sentence-pattern";
    pattern.setAttribute("aria-hidden", "true");
    makeAlignedText(pattern, alignedPattern, true);
    if (punctuation && patternBody.length + 1 === sentenceText.length) { const cell = document.createElement("span"); cell.className = "poem-sentence-cell poem-sentence-punctuation"; cell.textContent = punctuation; pattern.append(cell); }
    row.append(pick, pattern);
    if (index === poemSentenceSelection) {
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "poem-sentence-pen";
      edit.setAttribute("aria-label", `修改第 ${index + 1} 句`); edit.title = "修改这一句";
      edit.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="m16 3 5 5-12 12-6 1 1-6Z M14 5l5 5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';
      edit.addEventListener("click", () => {
        const input = document.createElement("textarea"); input.className = "poem-sentence-input"; input.rows = 2;
        input.value = part[0].trim(); input.setAttribute("aria-label", `第 ${index + 1} 句草稿`);
        pick.hidden = true; edit.hidden = true; row.insertBefore(input, pattern);
        let currentLength = part[0].length;
        const done = document.createElement("button"); done.type = "button"; done.className = "quiet-button"; done.textContent = "完成编辑";
        done.addEventListener("click", () => {
          if (!input.reportValidity()) return;
          renderPoemSentences();
          document.querySelectorAll(".poem-sentence-text")[index]?.focus({ preventScroll: true });
        }); row.append(done);
        input.addEventListener("input", () => {
          // A sentence editor cannot introduce sentence boundaries; Songci separators follow its meter.
          const entered = input.value.trim();
          const enteredMark = entered.match(/[，,。.？！；;!?：:…、]$/u)?.[0] || "";
          const oldMark = part[0].match(/[，,。.？！；;!?]$/u)?.[0] || "";
          const isSongci = poemPageState.source?.meter_type === "宋词";
          const commaPosition = isSongci
            && poemSentenceCommaPositions[index] === true;
          const fullWidthMarks = { ",": "，", ".": "。", "?": "？", "!": "！", ";": "；" };
          let separator;
          if (!isSongci) separator = oldMark;
          else if (commaPosition) separator = "，";
          else if (enteredMark) {
            const converted = fullWidthMarks[enteredMark] || enteredMark;
            separator = "。？！；".includes(converted) ? converted : "。";
          } else if ("。？！；".includes(fullWidthMarks[oldMark] || oldMark)
              && (fullWidthMarks[oldMark] || oldMark).length === 1) {
            separator = fullWidthMarks[oldMark] || oldMark;
          } else separator = "。";
          const body = entered.replace(/[，,。.？！；;!?：:…\r\n]/gu, "").replace(/、$/u, "").trim();
          input.setCustomValidity(body ? "" : "这一句不能为空");
          if (!body) { document.querySelector("#poem-editor-save").disabled = true; return; }
          const text = body + separator;
          const content = document.querySelector("#poem-editor-content");
          content.value = content.value.slice(0, part.index) + text + content.value.slice(part.index + currentLength);
          currentLength = text.length;
          schedulePoemEditorScore();
        });
        input.focus();
      }); row.append(edit);
    }
    line.append(row);
    });
    list.append(line);
  });
}
async function loadPoemSentencePatterns() {
  const source = poemPageState.source; if (!source) return;
  const variant = document.querySelector("#poem-editor-variant").value;
  poemSentencePatterns = []; poemSentenceCommaPositions = []; poemSentenceStanzaEnds = []; renderPoemSentences();
  if (source.meter_type !== "宋词") return;
  try {
    const meter = await getDatasetJson("meter", `${source.form_name}.json`);
    if (source !== poemPageState.source || variant !== document.querySelector("#poem-editor-variant").value) return;
    const selected = meter.variants.find((item) => item.name === variant) || meter.variants[0];
    const flattened = flattenMeter(selected);
    poemSentencePatterns = flattened.clauses.map((line) => line.rawPattern.replace(/[\/\s]/gu, ""));
    let stanzaLineCount = 0;
    poemSentenceStanzaEnds = Object.keys(selected).filter((key) => /^stanza\d+$/.test(key))
      .sort((left, right) => Number(left.slice(6)) - Number(right.slice(6)))
      .map((key) => { stanzaLineCount += selected[key].lines?.length || 0; return stanzaLineCount - 1; })
      .filter((end) => end >= 0);
    const terminalPositions = new Set(flattened.rhymeGroups.flatMap((group) => group.positions));
    poemSentenceStanzaEnds.forEach((index) => terminalPositions.add(index));
    poemSentenceCommaPositions = flattened.clauses.map((_, index) => !terminalPositions.has(index));
    renderPoemSentences();
  } catch (_) { /* The poem remains editable when its template is unavailable. */ }
}
async function querySelectedPoemSentence() {
  const source = poemPageState.source; const selected = poemSentenceSelection;
  if (!source || selected === null) return;
  const cacheKey = `${source.id}:${selected + 1}`;
  if (poemSentenceQueryCacheKey === cacheKey) return;
  if (poemSentenceQueryPendingKey === cacheKey) return;
  poemSentenceQueryPendingKey = cacheKey;
  const version = ++poemSentenceQueryVersion;
  const state = document.querySelector("#poem-sentence-query-state");
  const list = document.querySelector("#poem-sentence-variations"); list.replaceChildren(); state.textContent = "正在查询……";
  try {
    const result = await apiFetch(`/v1/profile/poems/${encodeURIComponent(source.id)}/sentence-variations/${selected + 1}`);
    if (version !== poemSentenceQueryVersion || source !== poemPageState.source) return;
    poemSentenceVariationTexts = [...new Set((result.items || []).map((item) => item.text).filter((text) => typeof text === "string"))];
    poemSentenceVariationSelection = null;
    poemSentenceQueryCacheKey = cacheKey;
    state.textContent = `第 ${selected + 1} 句 · ${poemSentenceVariationTexts.length} 种写法，选择后可替换`;
    poemSentenceVariationTexts.forEach((text, index) => {
      const item = document.createElement("button"); item.type = "button"; item.className = "poem-sentence-variation";
      item.setAttribute("aria-pressed", "false");
      const label = document.createElement("span"); label.className = "poem-sentence-variation-text"; label.textContent = text;
      const replace = document.createElement("span"); replace.className = "primary-button poem-sentence-variation-replace"; replace.textContent = "替换";
      replace.setAttribute("aria-hidden", "true");
      item.append(label, replace);
      item.addEventListener("click", () => {
        poemSentenceVariationSelection = index;
        list.querySelectorAll(".poem-sentence-variation").forEach((button, buttonIndex) => button.setAttribute("aria-pressed", String(buttonIndex === index)));
      });
      replace.addEventListener("click", (event) => { event.stopPropagation(); poemSentenceVariationSelection = index; replacePoemSentenceVariation(); });
      item.addEventListener("keydown", (event) => {
        if ((event.key === "Enter" || event.key === " ") && poemSentenceVariationSelection === index) {
          event.preventDefault(); replacePoemSentenceVariation();
        }
      });
      list.append(item);
    });
  } catch (error) { if (version === poemSentenceQueryVersion && source === poemPageState.source) state.textContent = `查询失败：${error.message}`; }
  finally { if (poemSentenceQueryPendingKey === cacheKey) poemSentenceQueryPendingKey = null; }
}
function replacePoemSentenceVariation() {
  const source = poemPageState.source;
  const selected = poemSentenceSelection;
  const text = poemSentenceVariationTexts[poemSentenceVariationSelection];
  if (!source || selected === null || typeof text !== "string") return;
  const part = poemSentenceParts()[selected];
  if (!part) return;
  const content = document.querySelector("#poem-editor-content");
  content.value = content.value.slice(0, part.index) + text + content.value.slice(part.index + part[0].length);
  ++poemSentenceQueryVersion;
  clearPoemSentenceVariations();
  document.querySelector("#poem-sentence-query-state").textContent = `已替换第 ${selected + 1} 句；草稿已更新，保存后写入作品。`;
  renderPoemSentences();
  schedulePoemEditorScore();
  if (window.matchMedia("(max-width: 780px)").matches) setPoemReviewDrawer(false);
}

function renderPoemEditorChanges(poem, title, content, saved = false) {
  const section = document.querySelector("#poem-editor-changes");
  const list = document.querySelector("#poem-editor-change-list");
  list.replaceChildren();
  if (poem.source?.deleted) { section.hidden = true; return; }
  const changes = saved ? poem.changes || [] : [];
  if (!saved) {
    const oldLines = editorLines(poem.content);
    const newLines = editorLines(content);
    for (let index = 0; index < Math.max(oldLines.length, newLines.length); index += 1) {
      if (oldLines[index] !== newLines[index]) changes.push({ old_line_number: oldLines[index] ? index + 1 : null, old_text: oldLines[index] || null, new_line_number: newLines[index] ? index + 1 : null, new_text: newLines[index] || null });
    }
  }
  const oldTitle = saved ? poem.source?.title : poem.title;
  if (oldTitle && title !== oldTitle) {
    const row = document.createElement("div"); row.className = "poem-editor-change-row";
    const label = document.createElement("span"); label.textContent = "标题";
    const text = document.createElement("div"); text.textContent = `${oldTitle} → ${title}`;
    row.append(label, text); list.append(row);
  }
  for (const change of changes) {
    const row = document.createElement("div"); row.className = "poem-editor-change-row";
    const label = document.createElement("span"); label.textContent = `第 ${change.old_line_number || change.new_line_number} 句`;
    const text = document.createElement("div"); text.textContent = `${change.old_text || "（新增）"} → ${change.new_text || "（删除）"}`;
    row.append(label, text); list.append(row);
  }
  section.hidden = !list.childElementCount;
}

function renderPoemEditorScore(evaluation, message = "") {
  const score = document.querySelector("#poem-editor-score");
  const grid = document.querySelector("#poem-editor-score-grid");
  const state = document.querySelector("#poem-editor-score-state");
  const issues = document.querySelector("#poem-editor-issues");
  score.textContent = evaluation ? scoreText(evaluation.comprehensive_score) : "--";
  score.style.color = evaluation && evaluation.comprehensive_score < 100 ? "var(--site-red-dark)" : "#446b50";
  state.textContent = message || (evaluation ? `${evaluation.form_name} · ${evaluation.rhyme_book_name}` : "等待正文评分");
  grid.replaceChildren(); issues.replaceChildren();
  if (!evaluation) return;
  for (const [label, value] of [["结构", evaluation.structure_score], ["平仄", evaluation.tonal_score], ["押韵", evaluation.rhyme_score]]) {
    const item = document.createElement("div"); const term = document.createElement("dt"); const description = document.createElement("dd");
    term.textContent = label; description.textContent = scoreText(value); item.append(term, description); grid.append(item);
  }
  const deductionMessages = scoreDeductionMessages(evaluation);
  if (deductionMessages.length) {
    const section = document.createElement("section");
    const heading = document.createElement("h3"); heading.textContent = "平仄与押韵错误";
    const list = document.createElement("ul");
    deductionMessages.slice(0, 20).forEach((text) => { const item = document.createElement("li"); item.textContent = text; list.append(item); });
    section.append(heading, list); issues.append(section);
  }
  const polyphonic = (evaluation.result?.polyphonicDecisions || []).map((item) => String(item.message || "").replaceAll("平/仄", "多"));
  const aoJiu = (evaluation.result?.aoJiu?.details || []).map((item) => item.message);
  for (const [heading, messages, empty] of [
    ["多音字判定", polyphonic, "未发现需要单独判断的多音字。"],
    ["拗救判定", aoJiu, evaluation.result?.aoJiu?.enabled ? "未识别到需要拗救的句式。" : "未启用拗救。"],
  ]) {
    const group = document.createElement("section");
    const title = document.createElement("h3"); title.textContent = heading; group.append(title);
    for (const message of messages.length ? messages : [empty]) {
      const line = document.createElement("p"); line.textContent = message; group.append(line);
    }
    issues.append(group);
  }
}

function refreshPoemEditorSaveState() {
  const source = poemPageState.source;
  const save = document.querySelector("#poem-editor-save");
  if (!source) { save.disabled = true; return; }
  const title = document.querySelector("#poem-editor-name").value.trim();
  const content = document.querySelector("#poem-editor-content").value.trim();
  save.disabled = !title || !content
    || Boolean(document.querySelector(".poem-sentence-input:invalid"))
    || !poemPageState.evaluation || poemPageState.evaluatedContent !== content;
}

function schedulePoemEditorScore() {
  const source = poemPageState.source;
  if (!source) return;
  const title = document.querySelector("#poem-editor-name").value.trim();
  const content = document.querySelector("#poem-editor-content").value.trim();
  const version = ++poemPageState.version;
  window.clearTimeout(poemPageState.timer);
  poemPageState.evaluation = null;
  poemPageState.evaluatedContent = "";
  refreshPoemEditorSaveState();
  renderPoemEditorChanges(source, title, content);
  try { localStorage.setItem(poemDraftKey(source.id), JSON.stringify({ title, content, sequence: document.querySelector("#poem-editor-sequence").value, variant: document.querySelector("#poem-editor-variant").value })); } catch (_) { /* storage unavailable */ }
  if (!content) { renderPoemEditorScore(null, "正文不能为空"); return; }
  renderPoemEditorScore(null, "正在检查格律……");
  poemPageState.timer = window.setTimeout(async () => {
    try {
      const context = { meter_type: source.meter_type, form_name: source.form_name, ...source.prosody_context };
      context.variant_name = document.querySelector("#poem-editor-variant").value || null;
      context.task_options = { ...context.task_options, variant_name: context.variant_name };
      const evaluation = await evaluateGeneratedPoem(content, context, {
        polyphonicMode: "automatic",
        strictPolyphonic: false,
        allowAoJiu: context.task_options?.allow_aojiu !== false,
      });
      if (version !== poemPageState.version) return;
      poemPageState.evaluation = evaluation;
      poemPageState.evaluatedContent = content;
      renderPoemEditorScore(evaluation);
      refreshPoemEditorSaveState();
    } catch (error) {
      if (version !== poemPageState.version) return;
      renderPoemEditorScore(null, `评分暂不可用：${error.message}。修改已保存在当前浏览器。`);
    }
  }, 380);
}

async function loadPoemPage() {
  const match = window.location.hash.match(/^#poem\/(edit|view)\/([^/]+)$/);
  if (!match) return;
  const [, mode, encodedId] = match;
  const id = decodeURIComponent(encodedId);
  const existingTree = poemPageState.tree?.nodes.some((node) => node.id === id) ? poemPageState.tree : null;
  const version = ++poemPageState.version;
  window.clearTimeout(poemPageState.timer);
  poemPageState.source = null;
  poemPageState.treeNodeCount = null;
  poemPageState.tree = null;
  poemPageState.evaluation = null;
  const status = document.querySelector("#poem-editor-status");
  const layout = document.querySelector("#poem-editor-layout");
  const save = document.querySelector("#poem-editor-save");
  const remove = document.querySelector("#poem-editor-delete");
  document.querySelector("#poem-version-tree").hidden = !existingTree;
  status.textContent = "正在加载作品……"; layout.hidden = true; save.disabled = true; remove.hidden = true;
  try {
    const poem = await apiFetch(`/v1/profile/poems/${encodeURIComponent(id)}`);
    if (version !== poemPageState.version) return;
    if (poem.deleted) { status.textContent = poem.message || "作者已删除该作品"; return; }
    poemPageState.source = poem;
    remove.hidden = !poem.can_delete;
    renderPoemEditorIdentity(poem.id);
    const title = document.querySelector("#poem-editor-name");
    const content = document.querySelector("#poem-editor-content");
    const sequence = document.querySelector("#poem-editor-sequence");
    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(poemDraftKey(id)) || "null"); } catch (_) { /* invalid draft */ }
    title.value = typeof draft?.title === "string" ? draft.title : poem.title;
    content.value = typeof draft?.content === "string" ? draft.content : poem.content;
    sequence.value = typeof draft?.sequence === "string" ? draft.sequence : (poem.sequence || "");
    title.readOnly = content.readOnly = false;
    const variant = document.querySelector("#poem-editor-variant");
    document.querySelector("#poem-editor-variant-field").hidden = poem.meter_type !== "宋词";
    variant.replaceChildren(new Option("未记录", ""));
    variant.disabled = true;
    if (poem.meter_type === "宋词") {
      const catalog = await getLocalMeterCatalog();
      if (version !== poemPageState.version) return;
      const meter = catalog.find((item) => item.name === poem.form_name || item.aliases?.includes(poem.form_name));
      for (const name of meter?.variants || []) variant.add(new Option(`${name.endsWith("体") ? name : `${name}体`}${name === meter.default_variant ? "（默认）" : ""}`, name));
      const wanted = typeof draft?.variant === "string" ? draft.variant : poemVariantName(poem);
      if (wanted && !Array.from(variant.options).some((option) => option.value === wanted)) variant.add(new Option(wanted, wanted));
      variant.value = wanted;
      variant.disabled = false;
    }
    save.textContent = "保存到我的创作";
    save.disabled = false;
    document.querySelector("#poem-editor-title").textContent = "修改诗作";
    const source = document.querySelector("#poem-editor-source");
    source.replaceChildren();
    source.append(document.createTextNode(`原作者：${poem.original_author_name || poem.author_display_name || "未标注"}`));
    if (poem.source) {
      source.append(document.createTextNode(" · 来源："));
      const link = document.createElement("a"); link.href = `#poem/view/${encodeURIComponent(poem.source.id)}`;
      link.textContent = poem.source.deleted ? "作者已删除该作品" : (poem.source.title || "查看来源版本");
      source.append(link);
    }
    layout.hidden = false;
    poemSentenceSelection = null;
    ++poemSentenceQueryVersion;
    poemSentenceQueryCacheKey = null;
    poemSentenceQueryPendingKey = null;
    document.querySelector("#poem-review-drawer-toggle").hidden = true;
    clearPoemSentenceVariations();
    setPoemReviewDrawer(false);
    document.querySelector("#poem-sentence-query-state").textContent = "选择一句后，这里会显示森林中的不同写法。";
    document.querySelector("#poem-sentence-variations").replaceChildren();
    loadPoemSentencePatterns();
    status.textContent = draft ? "已恢复当前浏览器的编辑草稿" : "";
    const reusableTree = existingTree?.root_poem_id === (poem.root_poem_id || poem.id) ? existingTree : null;
    loadPoemTree(id, reusableTree).catch(() => { if (poemPageState.source?.id === id) document.querySelector("#poem-version-tree").hidden = true; });
    schedulePoemEditorScore();
  } catch (error) {
    if (version === poemPageState.version) status.textContent = `无法打开作品：${error.message}`;
  }
}

function confirmLowPoemScore(evaluation) {
  const dialog = document.querySelector("#poem-save-warning");
  document.querySelector("#poem-save-warning-text").textContent = `当前综合格律评分为 ${scoreText(evaluation.comprehensive_score)} 分。仍要保存这个版本吗？`;
  const details = document.querySelector("#poem-save-warning-details");
  details.replaceChildren();
  const summary = document.createElement("p");
  summary.textContent = `结构 ${scoreText(evaluation.structure_score)} · 平仄 ${scoreText(evaluation.tonal_score)} · 押韵 ${scoreText(evaluation.rhyme_score)}`;
  details.append(summary);
  const messages = [];
  if (Number(evaluation.structure_score) < 100) messages.push("结构分未满：请核对句数、每句字数及篇式。");
  messages.push(...scoreDeductionMessages(evaluation));
  for (const message of messages.slice(0, 8)) {
    const line = document.createElement("p"); line.textContent = message; details.append(line);
  }
  return new Promise((resolve) => {
    dialog.returnValue = "cancel";
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    dialog.showModal();
  });
}

document.querySelector("#poem-save-cancel").addEventListener("click", () => document.querySelector("#poem-save-warning").close("cancel"));
document.querySelector("#poem-save-confirm").addEventListener("click", () => document.querySelector("#poem-save-warning").close("confirm"));
document.querySelector("#poem-editor-name").addEventListener("input", schedulePoemEditorScore);
document.querySelector("#poem-editor-variant").addEventListener("change", () => { schedulePoemEditorScore(); loadPoemSentencePatterns(); });
document.querySelector("#poem-editor-content").addEventListener("input", schedulePoemEditorScore);
document.querySelector("#poem-editor-sequence").addEventListener("input", () => {
  const source = poemPageState.source;
  if (!source) return;
  refreshPoemEditorSaveState();
  try {
    localStorage.setItem(poemDraftKey(source.id), JSON.stringify({
      title: document.querySelector("#poem-editor-name").value,
      content: document.querySelector("#poem-editor-content").value,
      sequence: document.querySelector("#poem-editor-sequence").value,
      variant: document.querySelector("#poem-editor-variant").value,
    }));
  } catch (_) { /* storage unavailable */ }
});
document.querySelector("#poem-editor-back").addEventListener("click", () => { window.location.hash = poemPageState.returnHash || "#profile"; });
document.querySelector("#poem-editor-save").addEventListener("click", async () => {
  const source = poemPageState.source;
  if (!source) return;
  if (document.querySelector(".poem-sentence-input:invalid")) return;
  const sourceHash = window.location.hash;
  const sourceVersion = poemPageState.version;
  const title = document.querySelector("#poem-editor-name").value.trim();
  const content = document.querySelector("#poem-editor-content").value.trim();
  const sequence = document.querySelector("#poem-editor-sequence").value.trim();
  const evaluation = poemPageState.evaluation;
  if (!evaluation || poemPageState.evaluatedContent !== content) return;
  if (title === String(source.title || "").trim()
      && normalizedPoemContent(content) === normalizedPoemContent(source.content)
      && sequence === String(source.sequence || "").trim()
      && document.querySelector("#poem-editor-variant").value === poemVariantName(source)) {
    announce("请修改后再保存");
    return;
  }
  if (Number(evaluation.comprehensive_score) < 100 && !(await confirmLowPoemScore(evaluation))) return;
  if (poemPageState.source !== source || window.location.hash !== sourceHash || sourceVersion !== poemPageState.version || title !== document.querySelector("#poem-editor-name").value.trim() || content !== document.querySelector("#poem-editor-content").value.trim() || sequence !== document.querySelector("#poem-editor-sequence").value.trim() || evaluation !== poemPageState.evaluation) return;
  const save = document.querySelector("#poem-editor-save");
  const status = document.querySelector("#poem-editor-status");
  save.disabled = true; status.textContent = "正在保存新版本……";
  try {
    const created = await apiFetch(`/v1/profile/poems/${encodeURIComponent(source.id)}/revisions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, sequence, content, evaluated_content: poemPageState.evaluatedContent, evaluation, ...(source.meter_type === "宋词" ? { variant_name: document.querySelector("#poem-editor-variant").value || null } : {}) }),
    });
    try { localStorage.removeItem(poemDraftKey(source.id)); } catch (_) { /* storage unavailable */ }
    if (poemPageState.source !== source || window.location.hash !== sourceHash || sourceVersion !== poemPageState.version) return;
    await loadPoems({ source: "owned" });
    if (poemPageState.source !== source || window.location.hash !== sourceHash || sourceVersion !== poemPageState.version) return;
    window.location.hash = `poem/view/${encodeURIComponent(created.id)}`;
    announce("新版本已保存到我的创作");
  } catch (error) {
    if (poemPageState.source !== source || window.location.hash !== sourceHash || sourceVersion !== poemPageState.version) return;
    status.textContent = `保存失败：${error.message}`; refreshPoemEditorSaveState();
  }
});

function violationMessages(result = {}) {
  result = result || {};
  const explicit = result.violations || [];
  const inferred = explicit.length ? [] : (result.lines || []).flatMap((line, lineIndex) =>
    (line.characters || []).flatMap((character, charIndex) => character.error ? [{
      lineIndex, charIndex, char: character.char, rule: "平仄",
      required: character.expected || "符合句式要求",
    }] : []));
  return [...explicit, ...inferred].map((item) => item.message ||
    `第 ${Number(item.lineIndex) + 1} 句第 ${Number(item.charIndex) + 1} 字“${item.char}”：${item.rule}，应${item.required}。`);
}

function rhymeViolationMessages(result = {}) {
  result = result || {};
  const explicit = result.rhymeViolations || [];
  const inferred = explicit.length ? [] : (result.rhymeGroups || []).flatMap((group) =>
    (group.endings || []).filter((ending) => group.dominantPart
      && !(ending.parts || []).includes(group.dominantPart)
      && !(result.conditionalConflicts || []).some((conflict) => conflict.lineIndex === ending.lineIndex)).map((ending) => ({
      lineIndex: ending.lineIndex,
      charIndex: (result.lines?.[ending.lineIndex]?.characters || []).length - 1,
      char: ending.char,
      required: `${group.dominantPart}韵部`,
    })));
  return [...explicit, ...inferred].map((item) => item.message ||
    `第 ${Number(item.lineIndex) + 1} 句第 ${Number(item.charIndex) + 1} 字“${item.char}”：未与本组主韵部“${item.required}”相合。`);
}

function scoreDeductionMessages(evaluation = {}) {
  evaluation = evaluation || {};
  const result = evaluation.result || {};
  const tonal = violationMessages(result);
  const rhyme = rhymeViolationMessages(result);
  const conflicts = result.conditionalConflicts || [];
  const explainsTone = conflicts.some((item) => item.readingConditions.some((branch) => branch.violations.length));
  const explainsRhyme = conflicts.some((item) => item.readingConditions.some((branch) => branch.rhymeViolations.length));
  if (Number(evaluation.tonal_score) < 100 && !tonal.length && !explainsTone) tonal.push("平仄分未满，请核对逐字声调和句式要求。");
  if (Number(evaluation.rhyme_score) < 100 && !rhyme.length && !explainsRhyme) rhyme.push("押韵分未满，请核对各韵脚与本组主韵部。");
  return [...tonal, ...rhyme];
}

function errorPositions(result = {}) {
  result = result || {};
  const positions = new Set();
  for (const item of result.violations || []) positions.add(`${item.lineIndex}:${item.charIndex}`);
  for (const item of result.rhymeViolations || []) positions.add(`${item.lineIndex}:${item.charIndex}`);
  (result.lines || []).forEach((line, lineIndex) => (line.characters || []).forEach((character, charIndex) => {
    if (character.error) positions.add(`${lineIndex}:${charIndex}`);
  }));
  (result.rhymeGroups || []).forEach((group) => (group.endings || []).forEach((ending) => {
    if (!group.dominantPart || (ending.parts || []).includes(group.dominantPart)) return;
    const charIndex = (result.lines?.[ending.lineIndex]?.characters || []).length - 1;
    if (charIndex >= 0) positions.add(`${ending.lineIndex}:${charIndex}`);
  }));
  return positions;
}

function appendMarkedPoemText(container, content, result, cursor) {
  if (!result?.lines?.length) { container.textContent = content; return; }
  const positions = errorPositions(result);
  const annotations = result.lines.flatMap((line, lineIndex) => (line.characters || []).map((character, charIndex) => ({
    char: character.char,
    error: positions.has(`${lineIndex}:${charIndex}`),
    conditionalError: character.conditionalError,
  })));
  let plainText = "";
  for (const char of [...content]) {
    if (!/[\u3400-\u9fff]/u.test(char)) { plainText += char; continue; }
    if (plainText) { container.append(document.createTextNode(plainText)); plainText = ""; }
    const annotation = annotations[cursor.index++];
    const span = document.createElement("span");
    span.textContent = char;
    if (annotation?.conditionalError === "affected") span.className = "prosody-conditional-affected";
    else if (annotation?.error) span.className = "prosody-error";
    container.append(span);
  }
  if (plainText) container.append(document.createTextNode(plainText));
}

function renderMarkedPoemVerses(verses, stanzas, result) {
  verses.replaceChildren();
  const cursor = { index: 0 };
  (stanzas.length ? stanzas : [["正文尚未生成"]]).forEach((lines) => {
    const stanza = document.createElement("p");
    stanza.className = "poem-handscroll__stanza";
    lines.forEach((line) => {
      const verse = document.createElement("span");
      verse.className = "poem-handscroll__line";
      appendMarkedPoemText(verse, line, result, cursor);
      stanza.append(verse);
    });
    verses.append(stanza);
  });
}

function decisionMessages(result = {}) {
  result = result || {};
  const polyphonic = (result.polyphonicDecisions || []).map((item) =>
    String(item.message || "").replaceAll("平/仄", "多"));
  const aoJiu = (result.aoJiu?.details || []).map((item) => item.message);
  if (!polyphonic.length && !aoJiu.length) return ["未发现需要人工复核的多音字或拗救判定。"];
  return [...polyphonic, ...aoJiu];
}

function appendInspectorList(parent, headingText, items, emptyText) {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = headingText;
  section.append(heading);
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "prosody-inspector__empty";
    empty.textContent = emptyText;
    section.append(empty);
  } else {
    const list = document.createElement("ul");
    items.forEach((item) => {
      const row = document.createElement("li");
      row.textContent = item;
      list.append(row);
    });
    section.append(list);
  }
  parent.append(section);
}

function openProsodyInspector(evaluation, title = "评分详情") {
  if (!evaluation?.result) return;
  const result = evaluation.result;
  document.querySelector("#prosody-inspector-title").textContent = title;
  prosodyInspectorBody.replaceChildren();

  const meta = document.createElement("div");
  meta.className = "prosody-inspector__meta";
  const form = document.createElement("strong");
  form.textContent = evaluation.form_name || "智能选式";
  const book = document.createElement("span");
  book.textContent = evaluation.rhyme_book_name || evaluation.rhyme_book_id || "未标注韵书";
  meta.append(form, book);

  const scores = document.createElement("dl");
  scores.className = "prosody-inspector__scores";
  [
    ["综合", evaluation.comprehensive_score],
    ["结构", evaluation.structure_score],
    ["平仄", evaluation.tonal_score],
    ["押韵", evaluation.rhyme_score],
  ].forEach(([label, value]) => {
    const group = document.createElement("div");
    const term = document.createElement("dt"); term.textContent = label;
    const description = document.createElement("dd"); description.textContent = scoreText(value);
    group.append(term, description); scores.append(group);
  });
  prosodyInspectorBody.append(meta, scores);

  appendInspectorList(prosodyInspectorBody, "结构提示", result.issues || [], "结构符合所选诗式。");
  appendInspectorList(prosodyInspectorBody, "平仄与押韵错误", scoreDeductionMessages(evaluation), "未发现平仄或押韵错误。");
  appendInspectorList(prosodyInspectorBody, "多音字与拗救", decisionMessages(result), "未发现需要人工复核的判定。");

  if (Array.isArray(result.lines) && result.lines.length) {
    const section = document.createElement("section");
    const heading = document.createElement("h3"); heading.textContent = "逐字标注";
    const lines = document.createElement("div"); lines.className = "prosody-inspector__lines";
    result.lines.forEach((line, index) => {
      const row = document.createElement("div");
      const text = document.createElement("p");
      text.className = "prosody-inspector__line-text";
      text.append(document.createTextNode(`${index + 1}. `));
      const errors = new Set([...errorPositions(result)]
        .filter((position) => Number(position.split(":")[0]) === index)
        .map((position) => Number(position.split(":")[1])));
      (line.characters || []).forEach((character, charIndex) => {
        const span = document.createElement("span");
        span.textContent = character.char;
        if (character.conditionalError === "affected") span.className = "is-conditional-affected";
        else if (errors.has(charIndex)) span.className = "is-error";
        text.append(span);
      });
      const tones = document.createElement("p"); tones.className = "prosody-inspector__tones";
      tones.textContent = (line.characters || []).map((item) => item.polyphonic ? "多" : (item.tone || "·")).join(" ");
      row.append(text, tones); lines.append(row);
    });
    section.append(heading, lines); prosodyInspectorBody.append(section);
  }

  const rhymeGroups = (result.rhymeGroups || []).map((group, index) => {
    const endings = (group.endings || []).map((ending) => ending.char).join("、") || "无";
    return `第 ${index + 1} 韵组：${endings}；主韵部 ${group.dominantPart || "未识别"}`;
  });
  appendInspectorList(prosodyInspectorBody, "押韵详情", rhymeGroups, "未形成可识别的韵组。");
  prosodyInspector.hidden = false;
  document.body.classList.add("has-prosody-inspector");
  document.body.classList.toggle(
    "has-profile-prosody-inspector",
    routeFromHash() === "profile" && profileTabFromHash() === "poems",
  );
  syncProfileInspectorSpace();
  document.querySelector("#prosody-inspector-close").focus();
}

function syncProfileInspectorSpace() {
  if (!document.body.classList.contains("has-profile-prosody-inspector")) return;
  const drawerWidth = prosodyInspector.getBoundingClientRect().width;
  document.body.style.setProperty("--profile-inspector-width", `${drawerWidth}px`);
}

function closeProsodyInspector() {
  prosodyInspector.hidden = true;
  document.body.classList.remove("has-prosody-inspector");
  document.body.classList.remove("has-profile-prosody-inspector");
  document.body.style.removeProperty("--profile-inspector-width");
}

document.querySelector("#prosody-inspector-close").addEventListener("click", closeProsodyInspector);
window.addEventListener("resize", syncProfileInspectorSpace);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !prosodyInspector.hidden) closeProsodyInspector();
});

async function setPoemFavorite(poemId, adding) {
  await apiFetch(`/v1/profile/poems/${encodeURIComponent(poemId)}/favorite`, { method: adding ? "POST" : "DELETE" });
  if (adding) {
    await loadCollectionData("poem");
    const fallback = profileCollections.find((item) => item.name === "默认合集");
    if (fallback) await apiFetch(`/v1/profile/poem-collections/${fallback.id}/items`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ poem_id: poemId }) });
  }
}

function compactPoemFormName(value) {
  return String(value || "")
    .trim()
    .replace(/([五七])言排律/g, "$1排")
    .replace(/([五七])言绝句/g, "$1绝")
    .replace(/([五七])言律诗/g, "$1律");
}

function poemVariantName(poem) {
  if (poem.variant_name) return String(poem.variant_name).trim();
  if (poem.prosody_context && typeof poem.prosody_context === "object") return String(poem.prosody_context.variant_name || "").trim();
  if (typeof poem.prosody_context_json === "string") {
    try { return String(JSON.parse(poem.prosody_context_json)?.variant_name || "").trim(); } catch { return ""; }
  }
  return "";
}

function createPoemHandscroll(poem, { resultLabel = "", showTime = false, evaluationPending = false, shareable = true, editable = true, favoritable = false } = {}) {
  const value = parsePoemFields(poem);
  const article = document.createElement("article");
  const songCi = isSongCi(value);
  const sourceStanzas = songCi ? songCiStanzas(value.content) : poemStanzas(value.content);
  const stanzaEnds = value.evaluation?.result?.meter?.stanzaEnds;
  const stanzas = songCi && stanzaEnds?.length
    ? groupSongCiStanzas(sourceStanzas, stanzaEnds)
    : sourceStanzas;
  article.className = `poem-handscroll${songCi ? " poem-handscroll--ci" : isTangPoem(value) ? " poem-handscroll--tang" : " poem-handscroll--single-column"}`;
  if (value.id) article.dataset.poemId = value.id;

  const leftRoller = document.createElement("span");
  leftRoller.className = "poem-handscroll__roller poem-handscroll__roller--left";
  leftRoller.setAttribute("aria-hidden", "true");
  const rightRoller = document.createElement("span");
  rightRoller.className = "poem-handscroll__roller poem-handscroll__roller--right";
  rightRoller.setAttribute("aria-hidden", "true");

  const paper = document.createElement("div");
  paper.className = "poem-handscroll__paper";
  const heading = document.createElement("header");
  heading.className = "poem-handscroll__heading";
  const kind = document.createElement("p");
  const genre = poemGenreLabel(value);
  const formName = String(value.form_name || "").trim();
  const variantName = genre === "宋词" ? poemVariantName(value) : "";
  const variantLabel = variantName && !variantName.endsWith("体") ? `${variantName}体` : variantName;
  const kindLabel = [genre, formName].filter((item, index, items) => item && (index === 0 || item !== items[0])).join(" · ");
  const displayLabel = resultLabel || (variantLabel ? `${kindLabel}（${variantLabel}）` : kindLabel) || "诗词";
  kind.textContent = displayLabel;
  const title = document.createElement("h3");
  title.textContent = compactPoemFormName(value.title);
  const originalAuthor = value.original_author_name || value.author_display_name || value.author_username;
  const author = originalAuthor ? document.createElement("p") : null;
  if (author) { author.className = "poem-handscroll__author"; author.textContent = `原作者：${originalAuthor}`; }
  const seal = document.createElement("span");
  seal.className = "poem-handscroll__seal";
  seal.setAttribute("aria-hidden", "true");
  seal.textContent = "诗矩";
  const evaluation = value.evaluation;
  const scorePanel = document.createElement("div");
  scorePanel.className = "poem-score";
  const scoreTop = document.createElement("div"); scoreTop.className = "poem-score__top";
  const inspect = document.createElement("button");
  inspect.type = "button"; inspect.className = "poem-score__inspect";
  inspect.textContent = evaluation ? "格律评分" : (evaluationPending ? "评分中" : "暂无评分");
  const canEvaluate = !evaluation && Boolean(value.job_id && value.candidate_ordinal != null && value.content);
  inspect.disabled = !evaluation && !canEvaluate;
  if (evaluation) inspect.addEventListener("click", async () => {
    if (!value.evaluation?._summary || !value.id) { openProsodyInspector(value.evaluation, value.title); return; }
    inspect.disabled = true;
    try {
      const detail = await apiFetch(`/v1/profile/poems/${encodeURIComponent(value.id)}`);
      if (!detail.evaluation) throw new Error("作品没有可查看的评分详情");
      value.evaluation = detail.evaluation;
      openProsodyInspector(value.evaluation, value.title);
    } catch (error) { announce(`评分详情加载失败：${error.message}`); }
    finally { inspect.disabled = false; }
  });
  else if (canEvaluate) inspect.addEventListener("click", () => evaluateHistoricalPoem(value, inspect, scorePanel));
  if (evaluation) {
    const scoreBook = document.createElement("span");
    scoreBook.className = "poem-score__book";
    scoreBook.textContent = evaluation.rhyme_book_name || evaluation.rhyme_book_id || "韵书未标注";
    const scoreGrid = document.createElement("dl");
    [["结构", evaluation.structure_score], ["平仄", evaluation.tonal_score], ["押韵", evaluation.rhyme_score]].forEach(([label, score]) => {
      const group = document.createElement("div");
      const term = document.createElement("dt"); term.textContent = label;
      const description = document.createElement("dd"); description.textContent = scoreText(score);
      group.append(term, description); scoreGrid.append(group);
    });
    scoreTop.append(inspect, scoreBook); scorePanel.append(scoreTop, scoreGrid);
  } else {
    scorePanel.classList.add("poem-score--pending");
    scoreTop.append(inspect); scorePanel.append(scoreTop);
  }

  const verses = document.createElement("div");
  verses.className = "poem-handscroll__verses";
  renderMarkedPoemVerses(verses, stanzas, evaluation?.result);
  const preface = document.createElement("div");
  preface.className = "poem-handscroll__preface";
  preface.textContent = String(value.sequence || "").trim();
  if (songCi && !stanzaEnds?.length && value.form_name) {
    getSongciStanzaEnds({
      ...value,
      meter_type: value.meter_type || "宋词",
      variant_name: poemVariantName(value),
    }).then((ends) => {
      if (!ends.length || !article.isConnected) return;
      const grouped = groupSongCiStanzas(sourceStanzas, ends);
      renderMarkedPoemVerses(verses, grouped, evaluation?.result);
      updatePoemLayout(article);
    }).catch(() => {});
  }
  paper.append(heading);
  if (preface.textContent) paper.append(preface);
  paper.append(verses);

  const scoreFooter = document.createElement("footer");
  scoreFooter.className = "poem-handscroll__footer poem-handscroll__footer--score";
  const footerActions = document.createElement("div");
  footerActions.className = "poem-handscroll__footer-actions";
  const flags = document.createElement("div");
  flags.className = "poem-score__flags";
  const violations = evaluation ? [...violationMessages(evaluation.result), ...rhymeViolationMessages(evaluation.result)] : [];
  const decisions = evaluation ? decisionMessages(evaluation.result) : [];
  [["×", violations.length ? violations : ["未发现平仄或押韵错误。"], "平仄与押韵检查"], ["?", decisions, "多音字与拗救判定"]].forEach(([symbol, messages, label]) => {
    const flag = document.createElement("button");
    flag.type = "button"; flag.className = "poem-score__flag"; flag.textContent = symbol;
    flag.setAttribute("aria-label", label); flag.disabled = !evaluation;
    const tooltip = document.createElement("span"); tooltip.className = "poem-score__tooltip";
    (messages || []).forEach((message) => { const line = document.createElement("span"); line.textContent = message; tooltip.append(line); });
    flag.append(tooltip); flags.append(flag);
  });
  heading.append(seal, kind, scorePanel, title);
  if (author) heading.append(author);
  if (favoritable) {
    const favorite = document.createElement("button");
    favorite.type = "button";
    favorite.className = "poem-score__inspect";
    let savedPoem = null;
    const updateFavorite = () => {
      const active = Boolean(savedPoem?.favorite);
      favorite.textContent = active ? "★" : "☆";
      favorite.title = active ? "取消收藏" : "收藏";
      favorite.setAttribute("aria-label", favorite.title);
      favorite.setAttribute("aria-pressed", String(active));
    };
    const resolvePoem = async () => {
      const detail = await apiFetch(value.id
        ? `/v1/profile/poems/${encodeURIComponent(value.id)}`
        : `/v1/profile/poems/by-job/${encodeURIComponent(value.job_id)}/${Number(value.candidate_ordinal)}`);
      if (detail.id) {
        value.id = detail.id;
        article.dataset.poemId = detail.id;
        delete article.dataset.poemIdUnavailable;
        article.dispatchEvent(new CustomEvent("poem-identity-resolved", { bubbles: true }));
      }
      return detail;
    };
    updateFavorite();
    favorite.disabled = Boolean(currentUser);
    if (currentUser) resolvePoem().then((detail) => { savedPoem = detail; updateFavorite(); }).catch(() => {
      article.dataset.poemIdUnavailable = "true";
      article.dispatchEvent(new CustomEvent("poem-identity-resolved", { bubbles: true }));
    }).finally(() => { favorite.disabled = false; });
    favorite.addEventListener("click", async () => {
      if (!requireLocalProfile()) return;
      favorite.disabled = true;
      try {
        savedPoem = await resolvePoem();
        const adding = !savedPoem.favorite;
        await setPoemFavorite(savedPoem.id, adding);
        savedPoem.favorite = adding;
        updateFavorite();
        announce(adding ? "已收藏到默认合集" : "已取消收藏");
      } catch (error) { announce(`收藏操作失败：${error.message}`); }
      finally { favorite.disabled = false; }
    });
    footerActions.append(favorite);
  }
  if (editable && (value.id || (value.job_id && value.candidate_ordinal != null))) {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "poem-score__inspect";
    edit.textContent = "修改";
    edit.title = "修改诗作并保存为我的新作品";
    edit.addEventListener("click", () => openPoemEditorFromScroll(value));
    footerActions.append(edit);
  }
  const createdDate = poemCreatedDate(value.created_at);
  if (showTime) {
    const time = document.createElement("time");
    if (createdDate) {
      time.dateTime = createdDate.toISOString();
      time.textContent = createdDate.toLocaleString("zh-CN");
    } else {
      time.textContent = "创建时间未知";
    }
    scoreFooter.append(time);
    const authorName = value.author_display_name || value.author_username;
    if (authorName && (!currentUser || (value.author_id && value.author_id !== currentUser.id))) { const source = document.createElement("span"); source.className = "poem-source-user"; source.textContent = `来自用户 ${authorName}`; scoreFooter.append(source); }
  }
  scoreFooter.append(footerActions);
  scoreFooter.append(flags);
  paper.append(scoreFooter);

  article.append(leftRoller, paper, rightRoller);
  poemLayoutObserver?.observe(article);
  requestAnimationFrame(() => updatePoemLayout(article));
  return article;
}

function poemCard(poem, { hideDelete = false } = {}) {
  const value = parsePoemFields(poem);
  const article = createPoemHandscroll(value, { showTime: true });
  article.dataset.poemId = value.id;
  const actions = document.createElement("div"); actions.className = "poem-card__actions";
  const favorite = document.createElement("button"); favorite.type = "button"; favorite.dataset.poemAction = "favorite"; favorite.title = value.favorite ? "取消收藏" : "收藏"; favorite.setAttribute("aria-label", favorite.title); favorite.textContent = value.favorite ? "★" : "☆";
  const collect = document.createElement("button"); collect.type = "button"; collect.dataset.poemAction = "collect"; collect.textContent = "修改合集";
  const remove = document.createElement("button"); remove.type = "button"; remove.dataset.poemAction = "delete"; remove.textContent = "删除";
  actions.append(favorite, collect);
  if (value.author_id === currentUser?.id && !hideDelete) actions.append(remove);
  article.querySelector(".poem-handscroll__footer-actions").append(actions);
  return article;
}

function renderPoemList(container, poems, emptyText, { append = false } = {}) {
  if (!container) return;
  if (!append) container.replaceChildren();
  if (!poems.length && !append) { const empty = document.createElement("p"); empty.className = "empty-state"; empty.textContent = emptyText; container.append(empty); return; }
  container.append(...poems.map((poem) => poemCard(poem, { hideDelete: container.id === "favorite-list" })));
}

async function loadPoems({ source = null, append = false } = {}) {
  if (!currentUser) return;
  if (!source && document.body.dataset.currentView !== "profile") return;
  source ||= profileTabFromHash() === "favorites" ? "favorites" : "owned";
  if (append && (ownedPoemsLoadingMore || !ownedPoemsHasMore)) return;
  if (source === "owned" && !append) {
    ++ownedPoemsRequestVersion;
    ownedPoemsHasMore = false;
    const more = document.querySelector("#poem-list-load-more");
    const paginationStatus = document.querySelector("#poem-list-pagination-status");
    if (more) more.hidden = true;
    if (paginationStatus) paginationStatus.textContent = "";
  }
  const requestVersion = ownedPoemsRequestVersion;
  if (append) ownedPoemsLoadingMore = true;
  const moreButton = document.querySelector("#poem-list-load-more");
  const paginationStatus = document.querySelector("#poem-list-pagination-status");
  if (append && moreButton) { moreButton.disabled = true; moreButton.textContent = "正在加载……"; }
  const generation = profileGeneration;
  const type = document.body.dataset.currentView === "profile" && profileTabFromHash() === "poems"
    ? (document.querySelector("#profile-work-type")?.value || "") : "";
  const params = new URLSearchParams({ latest: "true", limit: "24", offset: String(append ? ownedPoems.length : 0) });
  if (type) params.set("work_type", type);
  try {
  const [ownedPayload, favoritePayload] = await Promise.all([
    source !== "favorites" ? apiFetch(`/v1/profile/poems?${params}`) : null,
    source !== "owned" ? apiFetch("/v1/profile/poems?favorite=true") : null,
  ]);
  if (generation !== profileGeneration || requestVersion !== ownedPoemsRequestVersion) return;
  const pageItems = ownedPayload?.items || [];
  if (ownedPayload) {
    ownedPoems = append ? [...ownedPoems, ...pageItems] : pageItems;
    ownedPoemsHasMore = Boolean(ownedPayload.has_more);
  }
  if (favoritePayload) favoritePoems = favoritePayload.items || [];
  const merged = new Map(); [...ownedPoems, ...favoritePoems].forEach((poem) => merged.set(poem.id, poem)); profilePoems = [...merged.values()];
  if (ownedPayload) renderPoemList(document.querySelector("#poem-list"), append ? pageItems : ownedPoems,
    "暂无诗作记录。完成一次格律生成后，作品会自动归档到这里。", { append });
  renderPoemList(document.querySelector("#favorite-list"), favoritePoems, "还没有收藏作品。");
  if (source !== "favorites") {
    if (moreButton) { moreButton.hidden = !ownedPoemsHasMore; moreButton.disabled = false; moreButton.textContent = "加载更多作品"; delete moreButton.dataset.retry; }
    if (paginationStatus) paginationStatus.textContent = ownedPoemsHasMore ? `已显示 ${ownedPoems.length} 首` : (ownedPoems.length ? `共 ${ownedPoems.length} 首` : "");
  }
  } catch (error) {
    if (requestVersion !== ownedPoemsRequestVersion) return;
    if (paginationStatus) paginationStatus.textContent = `作品加载失败：${error.message}`;
    if (moreButton) { moreButton.disabled = false; moreButton.textContent = "重试加载"; moreButton.hidden = false; moreButton.dataset.retry = String(!append); }
  } finally {
    if (append) ownedPoemsLoadingMore = false;
  }
}

async function loadProfileData() {
  if (!currentUser || document.body.dataset.currentView !== "profile") return;
  const generation = profileGeneration;
  const requestVersion = ++profileLoadVersion;
  try {
    const tab = profileTabFromHash();
    if (tab === "account") return;
    if (tab === "poems") { await loadPoems({ source: "owned" }); return; }
    if (tab === "archive") {
      const payload = await apiFetch("/v1/conversations?include_deleted=true");
      if (generation !== profileGeneration || requestVersion !== profileLoadVersion) return;
      renderArchive((payload.items || []).filter((item) => item.deleted_at));
      return;
    }
    const collections = await apiFetch("/v1/profile/poem-collections");
    if (generation !== profileGeneration || requestVersion !== profileLoadVersion) return;
    profileCollections = mergeNamedCollections(collections.items || []);
    const collectionList = document.querySelector("#collection-list"); if (!collectionList) throw new Error("收藏合集容器未找到"); collectionList.replaceChildren();
    await Promise.all(profileCollections.map(async (item) => {
      const group = document.createElement("details"); group.className = "favorite-collection"; group.open = item.name === "默认合集"; group.dataset.collectionId = item.id;
      const summary = document.createElement("summary"); const name = document.createElement("strong"); name.textContent = item.name;
      const count = document.createElement("span"); count.textContent = `${item.count || 0} 首作品`; summary.append(name, count); group.append(summary);
      const grid = document.createElement("div"); grid.className = "profile-grid";
      const loadItems = async () => { try {
        const ids = [item.id, ...(item.duplicate_ids || [])];
        const payloads = await Promise.all(ids.map((id) => apiFetch(`/v1/profile/poem-collections/${encodeURIComponent(id)}/items`)));
        if (requestVersion !== profileLoadVersion) return;
        const poems = new Map(); payloads.flatMap((payload) => payload.items || []).forEach((poem) => poems.set(poem.id, poem));
        summary.querySelector("span").textContent = `${poems.size} 首作品`;
        [...poems.values()].forEach((poem) => grid.append(poemCard(poem, { hideDelete: true })));
        if (!poems.size) grid.innerHTML = '<p class="empty-state">合集里还没有作品。</p>';
      } catch (error) { const message = document.createElement("p"); message.className = "empty-state"; message.textContent = error.message; grid.replaceChildren(message); } };
      group.append(grid); collectionList.append(group);
      let loaded = false;
      group.addEventListener("toggle", () => { if (group.open && !loaded) { loaded = true; loadItems(); } });
      if (group.open) { loaded = true; await loadItems(); }
    }));
    if (requestVersion !== profileLoadVersion) return;
  } catch (error) { announce(`个人中心加载失败：${error.message}`); }
}

function renderArchive(items) {
  const list = document.querySelector("#archive-list"); list.replaceChildren();
  if (!items.length) { list.innerHTML = '<p class="empty-state">暂无归档对话。</p>'; return; }
  items.forEach((item) => {
    const row = document.createElement("article"); row.className = "profile-row"; row.dataset.conversationId = item.id;
    row.innerHTML = '<div><strong></strong><span></span></div><button type="button" data-archive-action="restore">恢复到聊天</button>';
    row.querySelector("strong").textContent = item.title; row.querySelector("span").textContent = `归档于 ${new Date(item.deleted_at * 1000).toLocaleString("zh-CN")}`; list.append(row);
  });
}

document.querySelectorAll("[data-profile-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    navigateToProfileTab(button.dataset.profileTab);
  });
});

document.querySelector("#profile-work-type")?.addEventListener("change", () => loadPoems({ source: "owned" }));
document.querySelector("#poem-list-load-more")?.addEventListener("click", (event) => {
  const button = event.currentTarget;
  if (button.dataset.retry === "true") loadPoems({ source: "owned" });
  else loadPoems({ source: "owned", append: true });
});

document.querySelector("#poem-list")?.parentElement?.addEventListener("click", handleProfileAction);
document.querySelector("#favorite-list")?.parentElement?.addEventListener("click", handleProfileAction);
document.querySelector("#collection-list")?.addEventListener("click", handleProfileAction);
document.querySelector("#collection-list")?.addEventListener("click", async (event) => {
  const rename = event.target.closest("[data-collection-action='rename']"); if (rename) { const row = rename.closest("[data-collection-id]"); const item = profileCollections.find((entry) => entry.id === row?.dataset.collectionId); if (!item) return; pendingCollection = { kind: "poem", item, onDone: null }; openCollectionPicker("poem", null, null); document.querySelector("#collection-name-input").value = item.name; document.querySelector("#collection-create-submit").dataset.renameId = item.id; document.querySelector("#collection-create-submit").textContent = "确认修改"; return; }
  const button = event.target.closest("[data-collection-action='delete']"); if (!button) return;
  const row = button.closest("[data-collection-id]");
  if (!await confirmAction("删除这个合集？作品本身不会被删除。", { confirmLabel: "删除" })) return;
  await apiFetch(`/v1/profile/poem-collections/${row.dataset.collectionId}`, { method: "DELETE" }); await loadProfileData();
});

async function handleProfileAction(event) {
  const button = event.target.closest("[data-poem-action]"); if (!button) return;
  const card = button.closest("[data-poem-id]"); const poemId = card?.dataset.poemId; if (!poemId) return;
  try {
    if (button.dataset.poemAction === "favorite") await setPoemFavorite(poemId, button.textContent !== "★");
    if (button.dataset.poemAction === "delete") { if (!await confirmAction("删除这首作品？", { confirmLabel: "删除" })) return; await apiFetch(`/v1/profile/poems/${poemId}`, { method: "DELETE" }); }
    if (button.dataset.poemAction === "collect") { await openCollectionPicker("poem", poemId, async (choice) => {
      const collection = profileCollections.find((item) => item.name === choice);
      if (collection) {
        // “修改合集”是移动操作：同一作品只保留在一个作品合集里。
        await Promise.all(profileCollections
          .filter((item) => item.id !== collection.id)
          .map((item) => apiFetch(`/v1/profile/poem-collections/${item.id}/items/${encodeURIComponent(poemId)}`, { method: "DELETE" }).catch(() => null)));
        await apiFetch(`/v1/profile/poem-collections/${collection.id}/items`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ poem_id: poemId }) });
      }
      await loadProfileData(); announce(`已移动到「${choice}」`);
    }); return; }
    await loadProfileData();
  } catch (error) { announce(`操作失败：${error.message}`); }
}

document.querySelectorAll("[data-manage-collections]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); openCollectionManager(button.dataset.manageCollections); }));
document.querySelector("#collection-modal-list")?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-collection]"); if (!button || !pendingCollection?.manage) return;
  const id = button.dataset.deleteCollection;
  const kind = pendingCollection.kind;
  if (!await confirmAction("删除这个合集？其中收藏的内容不会被删除。", { confirmLabel: "删除" })) return;
  try {
    if (kind === "poem") {
      const collection = profileCollections.find((item) => item.id === id);
      await Promise.all([id, ...(collection?.duplicate_ids || [])].map((collectionId) => apiFetch(`/v1/profile/poem-collections/${encodeURIComponent(collectionId)}`, { method: "DELETE" })));
    }
    else await apiFetch(`/v1/profile/thread-collections/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadProfileData();
    openCollectionManager(kind);
  } catch (error) { announce(`删除合集失败：${error.message}`); }
});

document.querySelector("#archive-list")?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-archive-action='restore']"); if (!button) return;
  const id = button.closest("[data-conversation-id]").dataset.conversationId;
  try { await apiFetch(`/v1/conversations/${id}/restore`, { method: "POST" }); await loadConversations(); await loadProfileData(); announce("对话已恢复"); } catch (error) { announce(`恢复失败：${error.message}`); }
});
document.querySelector("#favorite-thread-list")?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-thread-collection-edit]"); if (!button) return;
  const id = button.closest("[data-thread-id]")?.dataset.threadId;
  openCollectionPicker("thread", id, async (name) => {
    const target = favoriteThreadCollections.find((collection) => collection.name === name);
    if (!target) return;
    await apiFetch(`/v1/profile/thread-collections/${encodeURIComponent(target.id)}/items`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ thread_id: id }) });
    await loadProfileData(); announce(`已加入「${name}」`);
  });
});

function normalizeAssistantText(text) {
  return String(text)
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^```.*$/gm, "")
    .trim();
}

function requestHeaders(extra = {}) {
  return extra;
}

const pendingApiReads = new Map();
function apiFetch(url, options = {}) {
  if ((options.method || "GET").toUpperCase() !== "GET" || options.signal) return requestApi(url, options);
  const key = JSON.stringify([currentUser?.id, String(url), options.headers || {}]);
  if (!pendingApiReads.has(key)) {
    const request = requestApi(url, options).finally(() => { if (pendingApiReads.get(key) === request) pendingApiReads.delete(key); });
    pendingApiReads.set(key, request);
  }
  return pendingApiReads.get(key);
}

async function requestApi(url, options = {}) {
  const requestToken = currentUser?.id;
  const path = String(url).split("?", 1)[0];
  const source = path.endsWith("/state") || path.endsWith("/events") ? "poll" : (options.method || "GET").toUpperCase() === "GET" ? "page_init" : "user_action";
  const response = await fetch(url, { ...options, headers: requestHeaders({ ...(options.headers || {}), "X-Request-Source": source }) });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(responseError(payload, response.status));
    if (payload?.diagnostics?.log_path) error.message += `\n阶段：${payload.diagnostics.stage}\n日志：${payload.diagnostics.log_path}\n诊断编号：${payload.diagnostics.diagnostic_id}`;
    error.status = response.status;
    if (["/v1/local-profile", "/v1/model-config", "/v1/local-runtime"].includes(path)) {
      window.ShijuStartup?.fail(new Error(`${path}：HTTP ${response.status}；${error.message}`));
    }
    error.retryAfterSeconds = Number(response.headers.get("Retry-After")) || null;
    if (response.status === 401) announce("软件服务暂时不可用，请检查启动器运行状态。");
    throw error;
  }
  return payload;
}

function requireLocalProfile() {
  if (currentUser) return true;
  announce('本地档案暂时不可用，正在重新连接软件服务。');
  loadCurrentUser().catch(() => {});
  return false;
}

function updateProfileUi() {
  const accountLabel = document.querySelector("#account-entry-label");
  if (accountLabel) accountLabel.textContent = currentUser ? currentUser.display_name : "本地档案";
  const profileUserLabel = document.querySelector("#profile-user-label");
  if (profileUserLabel) profileUserLabel.textContent = currentUser ? `${currentUser.display_name} 的创作档案` : "本地创作档案";
  const displayName = document.querySelector("#profile-display-name");
  const bio = document.querySelector("#profile-bio");
  if (displayName) displayName.value = currentUser?.display_name || "";
  if (bio) bio.value = currentUser?.bio || "";
  const notifyReactions = document.querySelector("#profile-notify-reactions");
  if (notifyReactions) notifyReactions.checked = currentUser?.notify_on_reaction !== false;
  setUserAvatar(document.querySelector("#profile-avatar-preview"), currentUser);
  setUserAvatar(document.querySelector("#header-avatar"), currentUser);
  setUserAvatar(document.querySelector("#drawer-avatar"), currentUser);
  const drawerName = document.querySelector("#drawer-user-name");
  const drawerHandle = document.querySelector("#drawer-user-handle");
  if (drawerName) drawerName.textContent = currentUser?.display_name || "访客";
  if (drawerHandle) drawerHandle.textContent = currentUser ? "仅保存在这台电脑" : "仅保存在这台电脑";
}

function setUserAvatar(element, user) {
  if (!element) return;
  element.replaceChildren();
  const url = user?.avatar_url;
  if (url && /^\/(?:media\/avatars|v1\/profile\/avatar-file)\/[\w.-]+$/.test(url)) {
    const img = document.createElement("img"); img.src = url; img.alt = ""; element.append(img);
  } else element.textContent = "客";
}

async function loadCurrentUser({ restore = true } = {}) {
  try {
    window.ShijuStartup?.mark("开始读取本地档案");
    const result = await apiFetch("/v1/local-profile");
    if (!result.profile) throw new Error('软件没有返回本地唯一档案');
    currentUser = result.profile;
    window.ShijuStartup?.mark("本地档案读取成功");
    updateProfileUi();
    window.ShijuStartup?.mark("开始加载运行设置");
    await loadModelConfig();
    window.ShijuStartup?.mark("运行设置加载完成");
    showRoute(routeFromHash(), { scroll: false, restore });
    window.ShijuStartup?.ready();
  } catch (error) {
    window.ShijuStartup?.fail(error);
    announce(`无法连接本地档案：${error.message}。请检查启动器运行状态。`);
  }
}

function addModelEndpoint(endpoint = {}) {
  const row = document.createElement("fieldset");
  row.className = "model-endpoint profile-panel";
  row.innerHTML = `<legend>模型端点</legend><div class="model-endpoint__grid"><label class="field"><span>协议</span><select data-model-provider><option value="openai-compatible">OpenAI-like</option><option value="anthropic">Anthropic</option></select></label><label class="field"><span>端点名称</span><input data-model-id maxlength="64" required placeholder="例如 primary"></label><label class="field"><span>服务地址</span><input data-model-url type="url" required placeholder="https://api.example.com/v1"></label><label class="field"><span>模型名称</span><input data-model-name required placeholder="填写服务提供的模型 ID"></label><label class="field"><span>API 密钥</span><input data-model-key type="password" autocomplete="new-password" placeholder=""></label></div><div class="model-endpoint__actions"><button type="button" class="quiet-button" data-model-test>测试连接</button><button type="button" class="quiet-button" data-model-remove>移除</button><span role="status" data-model-status></span></div>`;
  row.querySelector("[data-model-provider]").value = endpoint.provider || "openai-compatible";
  row.querySelector("[data-model-id]").value = endpoint.provider_id || `endpoint-${document.querySelectorAll(".model-endpoint").length + 1}`;
  row.querySelector("[data-model-url]").value = endpoint.base_url || "";
  row.querySelector("[data-model-name]").value = endpoint.model || "";
  row.querySelector("[data-model-name]").placeholder = "选择下方模型，或直接输入模型 ID";
  row.querySelector("[data-model-name]").closest("label").insertAdjacentHTML("afterend", '<label class="field"><span>从服务模型列表选择</span><select data-model-choice disabled><option value="">请先获取模型列表</option></select></label>');
  row.querySelector(".model-endpoint__actions").insertAdjacentHTML("afterbegin", '<button type="button" class="quiet-button" data-model-fetch>获取模型列表</button>');
  const choices = row.querySelector("[data-model-choice]");
  const modelName = row.querySelector("[data-model-name]");
  let modelListVersion = 0;
  choices.addEventListener("change", () => { if (choices.value) modelName.value = choices.value; });
  modelName.addEventListener("input", () => { choices.value = [...choices.options].some((option) => option.value === modelName.value.trim()) ? modelName.value.trim() : ""; });
  for (const selector of ["[data-model-provider]", "[data-model-url]", "[data-model-key]"]) {
    row.querySelector(selector).addEventListener("input", () => {
      modelListVersion++;
      choices.replaceChildren(new Option("配置已变化，请重新获取", "")); choices.disabled = true;
    });
  }
  row.querySelector("[data-model-fetch]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const status = row.querySelector("[data-model-status]");
    const payload = modelEndpointPayload(row, { requireModel: false });
    if (!payload) return;
    const version = ++modelListVersion;
    button.disabled = true; status.textContent = "正在获取模型列表……";
    try {
      const result = await apiFetch("/v1/model-config/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (version !== modelListVersion || !row.isConnected) return;
      choices.replaceChildren(new Option("请选择模型，或在上方直接输入", ""));
      result.models.forEach((model) => choices.add(new Option(model.name === model.id ? model.id : `${model.name}（${model.id}）`, model.id)));
      choices.disabled = !result.models.length;
      if (result.models.some((model) => model.id === modelName.value.trim())) choices.value = modelName.value.trim();
      status.textContent = result.models.length ? `已获取 ${result.models.length} 个模型${result.truncated ? "（列表未完整返回，可继续手动输入）" : "，请选择或直接输入模型名。"}` : "服务未返回模型，可以直接输入模型名。";
    } catch (error) { if (version === modelListVersion) status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  if (endpoint.api_key_configured) row.querySelector("[data-model-key]").placeholder = "密钥已保存在本机；留空保留现有密钥";
  row.querySelector("[data-model-remove]").addEventListener("click", () => row.remove());
  row.querySelector("[data-model-test]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const status = row.querySelector("[data-model-status]");
    const payload = modelEndpointPayload(row);
    if (!payload) return;
    button.disabled = true; status.textContent = "正在连接……";
    try {
      const result = await apiFetch("/v1/model-config/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      status.textContent = result.message || "连接成功";
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.querySelector("#model-endpoint-list").append(row);
}

function modelEndpointPayload(row, { requireModel = true } = {}) {
  const value = (selector) => row.querySelector(selector).value.trim();
  const result = { provider_id: value("[data-model-id]"), provider: value("[data-model-provider]"), base_url: value("[data-model-url]"), model: value("[data-model-name]"), api_key: value("[data-model-key]") };
  if (!result.provider_id || !result.base_url || (requireModel && !result.model)) {
    row.querySelector("[data-model-status]").textContent = requireModel ? "请填写端点名称、服务地址和模型名称。" : "请先填写端点名称、服务地址和 API 密钥（已保存的密钥可留空）。";
    return null;
  }
  return result;
}

let modelConfigLoaded = false;
async function loadModelConfig() {
  if (modelConfigLoaded || !currentUser) return;
  modelConfigLoaded = true;
  try {
    const data = await apiFetch("/v1/model-config");
    const selectedModel = chatModel?.value;
    if (chatModel) {
      chatModel.replaceChildren();
      [...new Set(data.endpoints.map((endpoint) => endpoint.model))].forEach((model) => chatModel.add(new Option(model, model)));
      if (!chatModel.options.length) chatModel.add(new Option("请先配置模型", ""));
      if ([...chatModel.options].some((option) => option.value === selectedModel)) chatModel.value = selectedModel;
    }
    document.querySelector("#model-endpoint-list").replaceChildren();
    (data.endpoints.length ? data.endpoints : [{}]).forEach(addModelEndpoint);
    await loadLocalRuntime(data.endpoints);
  } catch (error) {
    modelConfigLoaded = false;
    window.ShijuStartup?.fail(error);
    document.querySelector("#model-config-status").textContent = error.message;
  }
}

document.querySelector("#model-endpoint-add")?.addEventListener("click", () => addModelEndpoint());
document.querySelector("#model-config-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = document.querySelector("#model-config-status");
  const endpoints = [...document.querySelectorAll(".model-endpoint")].map((row) => modelEndpointPayload(row));
  if (!endpoints.length || endpoints.some((endpoint) => !endpoint)) return;
  status.textContent = "正在保存……";
  try {
    await apiFetch("/v1/model-config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoints }) });
    status.textContent = "已保存到本机。";
    modelConfigLoaded = false;
    await loadModelConfig();
  } catch (error) { status.textContent = error.message; }
});

document.querySelector("#backup-download")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const status = document.querySelector("#backup-status");
  button.disabled = true; status.textContent = "正在打包本机数据……";
  try {
    const response = await fetch("/v1/backup");
    if (!response.ok) throw new Error((await response.json()).detail || "备份失败");
    const blob = await response.blob();
    if (!blob.size) throw new Error("备份文件为空，请重试并检查服务运行状态。");
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url;
    link.download = `shiju-backup-${new Date().toISOString().slice(0, 10)}.zip`;
    document.body.append(link);
    link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    status.textContent = "已发起下载，请在浏览器下载列表中确认文件已保存。";
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});

document.querySelector("#backup-restore-file")?.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  const status = document.querySelector("#backup-status");
  if (!file) return;
  if (!file.size) { status.textContent = "所选备份文件为空，请选择已完整下载的 ZIP 备份。"; event.target.value = ""; return; }
  if (!window.confirm("恢复备份会替换当前本机作品和数据。确定继续吗？")) { event.target.value = ""; return; }
  status.textContent = "正在校验并恢复……";
  try {
    const response = await fetch("/v1/backup/restore", { method: "POST", headers: { "Content-Type": "application/zip" }, body: file });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "恢复失败");
    status.textContent = "恢复完成，重启后生效。";
    const dialog = document.querySelector("#backup-restart-dialog");
    dialog.returnValue = "later";
    dialog.showModal();
  } catch (error) { status.textContent = error.message; }
  finally { event.target.value = ""; }
});

document.querySelector("#backup-restart-dialog")?.addEventListener("close", async (event) => {
  if (event.currentTarget.returnValue !== "restart") return;
  const status = document.querySelector("#backup-status");
  try {
    const previous = await apiFetch("/health/live");
    status.textContent = "正在重启诗矩……";
    await apiFetch("/v1/local-runtime/restart", { method: "POST" });
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try {
        const response = await fetch("/health/live", { cache: "no-store" });
        const health = await response.json();
        if (response.ok && health.instance_id && health.instance_id !== previous.instance_id) {
          window.location.reload();
          return;
        }
      } catch { /* The service is temporarily unavailable during restart. */ }
    }
    status.textContent = "重启尚未完成，请稍后刷新页面，或在启动器中检查服务状态。";
  } catch (error) {
    status.textContent = `无法立即重启：${error.message}。请在启动器中重新启动诗矩。`;
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && currentUser?.id) {
    if (document.body.dataset.currentView === "chat" && conversationHasPendingMessages() && !activeChatRequest) refreshConversationMessages(currentConversationId);
  }
});

window.addEventListener("focus", () => {
  if (!document.hidden && currentUser?.id && document.body.dataset.currentView === "chat" && conversationHasPendingMessages() && !activeChatRequest) {
    refreshConversationMessages(currentConversationId);
  }
});

function appendMessage(text, role, state = "", meta = {}) {
  const shouldFollow = isChatScrollPinnedToBottom();
  const article = document.createElement("article");
  article.className = `chat-message chat-message--${role}`;
  article.dataset.createdAt = String(meta.created_at || Date.now() / 1000);
  article.dataset.messageText = String(text || "");
  if (state) article.classList.add(`chat-message--${state}`);
  const avatar = document.createElement("div");
  avatar.className = `message-avatar message-avatar--${role}`;
  avatar.setAttribute("aria-hidden", "true");
  let avatarNode = avatar;
  if (role === "assistant") {
    const image = document.createElement("img");
    image.src = new URL("./ai-avatar.svg", import.meta.url).href;
    image.alt = "";
    avatar.append(image);
  } else {
    const author = meta.author || (!meta.id && currentUser ? currentUser : null);
    if (author?.id) {
      avatarNode = document.createElement("a");
      avatarNode.href = "#user/" + encodeURIComponent(author.id);
      avatarNode.className = "message-avatar message-avatar--user";
      avatarNode.setAttribute("aria-label", "查看" + (author.display_name || author.username) + "的主页");
      setUserAvatar(avatarNode, author);
    } else setUserAvatar(avatar, null);
  }
  const content = document.createElement("div");
  content.className = "message-content";
  const paragraph = document.createElement("div");
  paragraph.className = "message-text";
  renderMessageContent(paragraph, text);
  content.append(paragraph);
  for (const image of meta.images || []) {
    if (!/^data:image\/(?:png|jpeg|webp);base64,/.test(image.data_url || "")) continue;
    const img = document.createElement("img");
    img.className = "chat-message-image";
    img.src = image.data_url; img.alt = image.name || "用户发送的图片";
    content.append(img);
  }
  let actions = null;
  if (role === "user") {
    actions = document.createElement("div");
    actions.className = "chat-message-actions";
    const branchVersions = Array.isArray(meta.siblings) && meta.siblings.length > 1 ? meta.siblings : null;
    const versions = branchVersions || (Array.isArray(meta.versions) && meta.versions.length
      ? meta.versions.map((item) => ({ content: String(item.content || ""), version_number: item.version_number }))
      : [{ content: text, version_number: 1 }]);
    article.dataset.branchMessageIds = JSON.stringify((branchVersions || []).map((item) => item.id));
    article.dataset.messageSiblings = JSON.stringify(branchVersions || []);
    article.dataset.messageVersions = JSON.stringify(versions);
    const activeBranchIndex = branchVersions?.findIndex((item) => item.id === meta.id) ?? -1;
    article.dataset.versionIndex = String(activeBranchIndex >= 0 ? activeBranchIndex : Math.max(0, versions.length - 1));
    const copy = document.createElement("button");
    copy.type = "button"; copy.className = "chat-message-tool"; copy.dataset.copyMessage = "true";
    copy.title = "复制消息"; copy.setAttribute("aria-label", "复制消息"); copy.textContent = "⧉";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "chat-message-edit";
    edit.dataset.editMessage = "true";
    edit.title = "编辑这条消息";
    edit.setAttribute("aria-label", "编辑这条消息");
    edit.textContent = "✎";
    edit.classList.add("chat-message-tool");
    actions.append(copy);
    if (!currentConversationReadOnly) actions.append(edit);
    if (versions.length > 1 && !currentConversationReadOnly) {
      const previous = document.createElement("button");
      previous.type = "button"; previous.className = "chat-message-tool chat-message-version-prev";
      previous.dataset.versionStep = "-1"; previous.title = "查看上一版"; previous.setAttribute("aria-label", "查看上一版"); previous.textContent = "‹";
      const counter = document.createElement("span"); counter.className = "chat-message-version-count";
      const next = document.createElement("button");
      next.type = "button"; next.className = "chat-message-tool chat-message-version-next";
      next.dataset.versionStep = "1"; next.title = "查看下一版"; next.setAttribute("aria-label", "查看下一版"); next.textContent = "›";
      actions.append(previous, counter, next);
    }
  }
    if (role === "assistant") {
    const stack = document.createElement("div");
    stack.className = "chat-message-stack";
    stack.append(content);
    article.append(avatar, stack);
  }
  else {
    const stack = document.createElement("div");
    stack.className = "chat-message-stack";
    stack.append(content, actions);
    article.append(stack, avatarNode);
  }
  if (role === "user" && article.dataset.messageVersions) updateMessageVersionControls(article);
  chatThread.append(article);
  if (shouldFollow) scrollChatToBottom();
  return article;
}

function renderMessageContent(element, text) {
  element.innerHTML = renderChatMarkdown(text);
  renderMathInElement(element, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
      { left: "$", right: "$", display: false },
    ],
    throwOnError: false,
    trust: false,
    maxExpand: 500,
  });
}

function updateMessageVersionControls(article) {
  const versions = JSON.parse(article.dataset.messageVersions || "[]");
  const index = Number(article.dataset.versionIndex || 0);
  const version = versions[index];
  const paragraph = article.querySelector(".message-text");
  if (version && paragraph) renderMessageContent(paragraph, version.content);
  const counter = article.querySelector(".chat-message-version-count");
  if (counter) counter.textContent = `${index + 1} / ${versions.length}`;
  const previous = article.querySelector(".chat-message-version-prev");
  const next = article.querySelector(".chat-message-version-next");
  if (previous) previous.disabled = index <= 0;
  if (next) next.disabled = index >= versions.length - 1;
  const siblings = JSON.parse(article.dataset.messageSiblings || "[]");
  if (!siblings.length) syncMessageVersionBranch(article, index >= versions.length - 1);
}

function isChatScrollPinnedToBottom() {
  if (!chatThreadScroll) return true;
  return chatThreadScroll.scrollHeight - chatThreadScroll.scrollTop - chatThreadScroll.clientHeight < 24;
}

function scrollChatToBottom() {
  if (chatThreadScroll) chatThreadScroll.scrollTop = chatThreadScroll.scrollHeight;
}

function syncMessageVersionBranch(article, showDescendants) {
  let node = article?.nextElementSibling;
  while (node) {
    if (showDescendants) {
      if (node.dataset.hiddenByMessageVersion === "true") {
        node.hidden = false;
        delete node.dataset.hiddenByMessageVersion;
      }
    } else if (!node.hidden || node.dataset.hiddenByMessageVersion === "true") {
      node.hidden = true;
      node.dataset.hiddenByMessageVersion = "true";
    }
    node = node.nextElementSibling;
  }
}

const toolDisplayNames = {
  list_rhyme_books: "列出韵书",
  lookup_rhyme: "查询韵部",
  get_rhyme_part: "查询韵部详情",
  list_ci_meters: "列出词牌",
  get_ci_meter: "查询词牌格律",
  list_supported_forms: "查询支持的体裁",
  prepare_generation: "编辑创作提示词",
  prepare_partial_generation: "编辑补写提示词",
  prepare_rewrite: "编辑改写提示词",
  submit_generation: "提交生成任务",
  submit_partial_generation: "提交部分生成任务",
  submit_rewrite: "提交改写任务",
  get_poetry_job: "查询生成进度",
  get_latest_poetry_job: "查询最近生成批次",
  list_poetry_jobs: "列出本对话生成批次",
};

function toolDisplayName(name) {
  return toolDisplayNames[name] || "处理诗词资料";
}

function ensureToolActivity(article) {
  let activity = article.querySelector(".tool-activity");
  const content = article.querySelector(".message-content");
  if (!activity) {
    activity = document.createElement("div");
    activity.className = "tool-activity";
    activity.setAttribute("aria-label", "执行过程");
    const list = document.createElement("div");
    list.className = "tool-activity__list";
    activity.append(list);
    content?.insertBefore(activity, content.firstChild);
  }
  if (content) content.hidden = false;
  article.classList.add("has-tool-activity");
  article._toolActivityItems ||= new Map();
  return activity;
}

function renderToolActivityItem(article) {
  const shouldFollow = isChatScrollPinnedToBottom();
  const activity = ensureToolActivity(article);
  const list = activity.querySelector(".tool-activity__list");
  const items = [...article._toolActivityItems.values()];
  const oldRows = article._toolActivityRows || new Map();
  const newRows = new Map();
  const rows = items.map((item) => {
    const previous = oldRows.get(item.id);
    const signature = JSON.stringify([item.kind, item.text, item.label, item.state]);
    if (previous?.signature === signature) {
      newRows.set(item.id, previous);
      return previous.row;
    }
    const row = previous?.row || document.createElement("div");
    row.replaceChildren();
    newRows.set(item.id, { row, signature });
    if (item.kind === "narration") {
      row.className = "tool-activity__narration";
      row.innerHTML = renderChatMarkdown(String(item.text || "").trim());
      return row;
    }
    row.className = `tool-activity__item tool-activity__item--${item.state}`;
    row.setAttribute("role", "status");
    const icon = document.createElement("span");
    icon.className = "tool-activity__icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = { running: "◌", completed: "✓", failed: "!", interrupted: "–" }[item.state] || "–";
    const text = document.createElement("span");
    text.textContent = item.state === "running" ? `正在${item.label}……`
      : item.state === "completed" ? completedToolLabel(item.label)
      : `${item.label}${item.state === "failed" ? "失败" : "已中止"}`;
    row.append(icon, text);
    return row;
  });
  // Stream into the existing text node; do not detach every previous record
  // for each token. Only reconcile the list when its membership changes.
  if (rows.length !== oldRows.size || rows.some((row, index) => row !== [...oldRows.values()][index]?.row)) {
    list.replaceChildren(...rows);
  }
  article._toolActivityRows = newRows;
  const paragraph = article.querySelector(".message-text");
  if (paragraph && article.dataset.executionPhase === "running") paragraph.hidden = true;
  if (shouldFollow) scrollChatToBottom();
}

function restoreToolActivity(article, records) {
  if (!Array.isArray(records) || !records.length) return;
  article._toolActivityItems = new Map(records.map((item) => [item.id, item]));
  renderToolActivityItem(article);
}

function completedToolLabel(label) {
  for (const verb of ["查询", "校验", "检索", "读取", "准备", "提交", "处理", "编辑"]) {
    if (label.startsWith(verb)) return `${verb}了${label.slice(verb.length)}`;
  }
  return `完成了${label}`;
}

function updateToolActivity(article, id, label, state, timestamp = Date.now() / 1000) {
  if (!article || !id) return;
  article._toolActivityItems ||= new Map();
  const previous = article._toolActivityItems.get(id);
  // Replayed start events must never reopen an already settled operation.
  if (state === "running" && previous && previous.state !== "running") return;
  article._toolActivityItems.set(id, { id, label: label || previous?.label || "处理诗词资料", state,
    started_at: previous?.started_at ?? timestamp, updated_at: timestamp });
  renderToolActivityItem(article);
}

function finishToolActivity(article) {
  if (!article) return;
  article._streamingPreview?.stop();
  article.dataset.executionPhase = "finished";
  for (const item of article._toolActivityItems?.values() || []) {
    if (item.state === "running") item.state = "interrupted";
  }
  if (article._toolActivityItems?.size || article.querySelector(".tool-activity")) renderToolActivityItem(article);
  const paragraph = article.querySelector(".message-text");
  if (paragraph) paragraph.hidden = !paragraph.textContent.trim();
}

function updateActivityPreview(article, text) {
  article._toolActivityItems ||= new Map();
  if (!text) {
    article._streamingPreview?.stop();
    article._toolActivityItems.delete("live-preview");
    if (article.querySelector(".tool-activity")) renderToolActivityItem(article);
    return;
  }
  article._streamingPreview ||= createStreamingText((visible) => {
    article._toolActivityItems.set("live-preview", {
      id: "live-preview", kind: "narration", text: visible, state: "completed",
    });
    renderToolActivityItem(article);
  });
  article._streamingPreview.set(text);
}

function filterConversations() {
  const query = conversationSearchInput?.value.trim().toLocaleLowerCase() || "";
  document.querySelectorAll("#conversation-list .conversation-row").forEach((row) => {
    const title = row.querySelector("button[data-conversation-id]")?.textContent?.trim().toLocaleLowerCase() || "";
    row.hidden = Boolean(query && !title.includes(query));
  });
}

function hideChatWelcome() {
  const welcome = chatThread.querySelector("#chat-welcome");
  if (welcome) welcome.hidden = true;
}

function replaceMessage(article, text, state = "") {
  const shouldFollow = isChatScrollPinnedToBottom();
  const content = article.querySelector(".message-text");
  renderMessageContent(content, text);
  article.dataset.messageText = String(text || "");
  content.hidden = Boolean(article._toolActivityItems?.size && article.dataset.executionPhase === "running") || !content.textContent.trim();
  article.classList.remove("chat-message--pending", "chat-message--error");
  if (state) article.classList.add(`chat-message--${state}`);
  if (shouldFollow) scrollChatToBottom();
}

function appendAssistantContinueButton(article) {
  if (!article || article.querySelector("[data-continue-message]")) return;
  const stack = article.querySelector(".chat-message-stack");
  if (!stack) return;
  const actions = document.createElement("div");
  actions.className = "chat-message-actions";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "chat-message-tool chat-message-continue";
  button.dataset.continueMessage = "true";
  button.title = "继续生成";
  button.setAttribute("aria-label", "继续生成");
  button.textContent = "继续";
  actions.append(button);
  stack.append(actions);
}

function openProposalHelpDialog() {
  let dialog = document.querySelector("#proposal-help-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "proposal-help-dialog";
    dialog.className = "proposal-help-dialog";
    dialog.setAttribute("aria-labelledby", "proposal-help-title");
    dialog.innerHTML = `
      <form method="dialog" class="proposal-help-dialog__shell">
        <header class="proposal-help-dialog__header">
          <div><p class="eyebrow">创作方案说明</p><h2 id="proposal-help-title">每个配置怎么填写</h2></div>
          <button type="submit" class="icon-button" aria-label="关闭说明">×</button>
        </header>
        <div class="proposal-help-dialog__body">
          <p>这些字段会和“编辑创作要求”一起提交给本地创作模型。复杂配置也可以在对话框中用自然语言告诉 Agent。</p>
          <dl>
            <div><dt>编辑创作要求</dt><dd>给模型的具体写作指令，可直接用自然语言描述意象、情绪、结构和格律要求。</dd></div>
            <div><dt>候选数量</dt><dd>整首创作和部分生成最多 20 个，rewrite 最多 50 个。</dd></div>
            <div><dt>诗体</dt><dd>选择唐诗、宋词、汉俳或排律。</dd></div>
            <div><dt>篇式 / 词牌</dt><dd>填写具体的诗体形式或词牌名称。</dd></div>
            <div><dt>生成韵书</dt><dd>选择判定押韵时使用的韵书。</dd></div>
            <div><dt>押韵策略</dt><dd>自动选韵、固定韵部或随机选韵。选择固定韵部时，再填写“指定韵部”。</dd></div>
            <div><dt>指定韵部</dt><dd>选择韵组对应的韵部；留空的韵组由生成器自动选择。</dd></div>
            <div><dt>其他创作配置（JSON）</dt><dd>填写额外选项对象；没有额外配置时填空对象。完整生成时可设 skip_planning 为 true，跳过可见构思，直接输出 [title] 和 [content]。局部重写时可设 omit_original_target_from_prompt 为 true，让提示词不包含目标句原文。</dd></div>
            <div><dt>诗句</dt><dd>勾选的句子会保留；未勾选的已有句子重写，空句补完。</dd></div>
            <div><dt>多音字</dt><dd>严格判定会保守处理多音字；放行多音会允许模型结合语境判断。</dd></div>
            <div><dt>句数</dt><dd>仅排律需要填写，使用不少于 10 的偶数。</dd></div>
            <div><dt>允许拗救</dt><dd>唐诗或排律的格律选项，勾选后允许符合规则的拗救。</dd></div>
            <div><dt>首句末尾多音字</dt><dd>唐诗和排律默认不允许在生成的首句末尾使用韵书中有多个平仄或韵部的字；勾选后取消此限制。此选项与多音字平仄判定模式独立，其他格律约束仍然生效。保留的首句不受此选项限制。</dd></div>
          </dl>
        </div>
        <footer class="proposal-help-dialog__footer"><button type="submit" class="quiet-button">知道了</button></footer>
      </form>`;
    document.body.append(dialog);
  }
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function partialSentenceLengths(meterType, formName, numLines, taskOptions = {}) {
  if (meterType === "宋词") return null;
  if (meterType === "汉俳") {
    const pattern = String(taskOptions.line_pattern || "5-7-5").replaceAll("五", "5").replaceAll("七", "7").replaceAll("－", "-");
    if (!["5-7-5", "3-5-3"].includes(pattern)) throw new Error("汉俳格式仅支持 5-7-5 或 3-5-3");
    return pattern.split("-").map(Number);
  }
  const length = formName.startsWith("五言") ? 5 : 7;
  const count = meterType === "排律" ? Number(numLines || 10) : formName.includes("绝句") ? 4 : 8;
  const minimum = meterType === "排律" ? 10 : 4;
  if (!Number.isInteger(count) || count < minimum || count > 128 || count % 2) throw new Error(`句数必须为${minimum}至128之间的偶数`);
  return Array(count).fill(length);
}

function validateRewriteTargetNumbers(targetNumbers, lineCount) {
  if (!targetNumbers.length) throw new Error("请至少取消勾选一句，指定需要重写的句子");
  if (targetNumbers.length >= lineCount) throw new Error("请至少保留一句作为上下文");
  if (targetNumbers.some((number, index) => index > 0 && number !== targetNumbers[index - 1] + 1)) {
    throw new Error("必须选择连续的句子！");
  }
  return targetNumbers;
}

function appendProposalEditor(prepared, anchor = null) {
  if (!prepared?.proposal_id || chatThread.querySelector(`[data-proposal-id="${prepared.proposal_id}"]`)) return;
  const proposal = prepared.proposal || {};
  const isLocalRewrite = prepared.kind === "rewrite";
  const panel = document.createElement("details");
  panel.className = "proposal-editor";
  panel.dataset.proposalId = prepared.proposal_id;
  panel.dataset.conversationId = currentConversationId || "";
  if (prepared.submitted_job_id) panel.dataset.jobId = prepared.submitted_job_id;
  panel.open = !prepared.direct && !prepared.submitted_job_id;

  const summary = document.createElement("summary");
  const isPartial = prepared.kind === "partial_generate" || prepared.kind === "rewrite";
  let isRevision = isPartial && Boolean(proposal.original_text);
  summary.textContent = isPartial ? (isLocalRewrite ? "编辑改写要求" : "编辑补完要求") : "编辑创作提示词";
  const body = document.createElement("div");
  body.className = "proposal-editor__body";
  const meta = document.createElement("p");
  const form = [proposal.meter_type, proposal.form_name].filter(Boolean).join(" · ");
  const guidance = prepared.direct
    ? (prepared.submitted_job_id ? "以下是您已提交的创作要求和生成参数。" : "请选择生成参数，填写创作要求后提交。")
    : "以下是 Agent 已选择的实际生成参数，可直接修改后提交。";
  meta.textContent = `${form || "诗词生成方案"}。${guidance}`;

  const promptLabel = document.createElement("label");
  const promptHeading = document.createElement("span");
  promptHeading.className = "proposal-editor__field-heading";
  const promptTitle = document.createElement("span");
  promptTitle.textContent = "编辑创作要求";
  const help = document.createElement("button");
  help.type = "button";
  help.className = "proposal-editor__help text-button";
  help.textContent = "点击查看说明";
  help.addEventListener("click", openProposalHelpDialog);
  promptHeading.append(promptTitle, help);
  promptLabel.append(promptHeading);
  const prompt = document.createElement("textarea");
  prompt.setAttribute("aria-label", "编辑创作要求");
  prompt.rows = 5;
  prompt.value = prepared.editable_prompt || "";
  promptLabel.append(prompt);

  const rewriteTargets = new Set(proposal.target_line_numbers || []);
  let rewriteLines = proposal.source_lines || (proposal.original_text || "")
    .split(/[，,。.？！；;!?\r\n]+/u).map((text) => text.trim())
    .filter((text) => /[\u4e00-\u9fa5A-Za-z]/u.test(text))
    .map((text, index) => ({ number: index + 1, source_text: text, text: text.replace(/[^\u4e00-\u9fa5A-Za-z]/gu, "") }));
  let rewriteSummary;
  let syncRewriteSelection = () => {};
  const retainedLines = new Set(isRevision
    ? rewriteLines.filter((line) => !rewriteTargets.has(line.number)).map((line) => line.number)
    : Object.keys(proposal.fixed_lines || {}).map(Number));
  const sentenceValues = { ...(proposal.sentence_inputs || Object.fromEntries(rewriteLines.map((line) => [String(line.number), line.text]))), ...(proposal.fixed_lines || {}) };
  let rewriteSourceInput = null;
  let importRewriteSource = null;
  if (isLocalRewrite) {
    const sourceLabel = document.createElement("label");
    const sourceHeading = document.createElement("span");
    sourceHeading.className = "proposal-editor__field-heading";
    sourceHeading.textContent = "来源作品 ID";
    const sourceInput = document.createElement("input");
    sourceInput.type = "text";
    sourceInput.placeholder = "从作品森林复制 ID 后导入";
    rewriteSourceInput = sourceInput;
    sourceInput.value = proposal.source_poem_id || "";
    sourceLabel.append(sourceHeading, sourceInput);
    const fillButton = document.createElement("button");
    fillButton.type = "button";
    fillButton.className = "quiet-button proposal-editor__import-source";
    fillButton.textContent = "导入作品 ID";
    fillButton.disabled = Boolean(prepared.submitted_job_id);
    const importStatus = document.createElement("small"); importStatus.setAttribute("aria-live", "polite");
    let importVersion = 0;
    importRewriteSource = async (restoreDraft = null) => {
      if (prepared.submitted_job_id || panel.classList.contains("is-submitted")) return;
      const id = sourceInput.value.trim();
      const version = ++importVersion;
      fillButton.disabled = true;
      importStatus.textContent = "正在导入……";
      try {
        if (!id) throw new Error("ID 不存在");
        const source = await apiFetch(`/v1/profile/poems/${encodeURIComponent(id)}/rewrite-source`);
        if (version !== importVersion || sourceInput.value.trim() !== id) return;
        if (source.meter_type === "宋词") {
          await populateSongciForms();
          if (version !== importVersion || sourceInput.value.trim() !== id) return;
        }
        Object.assign(proposal, source, { source_lines: source.lines });
        isRevision = true;
        rewriteLines = source.lines;
        rewriteTargets.clear(); retainedLines.clear();
        for (const key of Object.keys(sentenceValues)) delete sentenceValues[key];
        rewriteLines.forEach((line) => { sentenceValues[String(line.number)] = line.text; retainedLines.add(line.number); });
        if (restoreDraft?.sourcePoemId === source.source_poem_id && restoreDraft.sourceVersion === source.source_version) {
          (restoreDraft.rewriteTargets || []).filter((number) => rewriteLines.some((line) => line.number === number)).forEach((number) => { rewriteTargets.add(number); retainedLines.delete(number); });
        }
        meterType.value = source.meter_type;
        formName.value = source.form_name;
        numLines.value = source.num_lines || "";
        taskOptions.value = JSON.stringify({ omit_original_target_from_prompt: true, ...(source.task_options || {}) }, null, 2);
        updateFormOptions();
        variant.value = source.variant_name || "";
        meterType.disabled = formName.disabled = variant.disabled = numLines.disabled = true;
        updateConditionalFields(); renderSentenceInputs(); updateMeta();
        await loadRhymeOptions();
        if (version !== importVersion) return;
        importStatus.textContent = `已导入：${source.title || source.source_poem_id}`;
        saveDraft();
      } catch (error) {
        if (version === importVersion) importStatus.textContent = error.message;
      } finally {
        if (version === importVersion) fillButton.disabled = false;
      }
    };
    fillButton.addEventListener("click", () => importRewriteSource());
    sourceLabel.append(fillButton, importStatus);
    body.append(sourceLabel);
    sourceInput.addEventListener("input", () => {
      ++importVersion; fillButton.disabled = false;
      proposal.source_poem_id = null; proposal.source_version = null; proposal.original_text = "";
      isRevision = false; rewriteLines = [];
      retainedLines.clear();
      rewriteTargets.clear();
      for (const key of Object.keys(sentenceValues)) delete sentenceValues[key];
      importStatus.textContent = "请导入作品 ID";
      renderSentenceInputs();
    });
  }
  let selection;
  let renderSentenceInputs = () => {};
  if (isPartial) {
    selection = document.createElement("fieldset");
    selection.className = "rewrite-selection";
    const legend = document.createElement("legend"); legend.textContent = isLocalRewrite
      ? "勾选句子重写（需连续选择）"
      : "诗句 · 勾选的句子会保留";
    rewriteSummary = document.createElement("p"); rewriteSummary.className = "rewrite-selection__summary";
    selection.append(legend, rewriteSummary);
    renderSentenceInputs = () => {
      selection.querySelectorAll('.rewrite-selection__line').forEach((row) => row.remove());
      selection.querySelector(".rewrite-selection__empty")?.remove();
      if (isLocalRewrite && !rewriteLines.length) {
        const empty = document.createElement("p");
        empty.className = "rewrite-selection__empty";
        empty.textContent = "请先导入作品 ID，原作句子会显示在这里。";
        selection.append(empty);
      }
      for (const line of rewriteLines) {
        const row = document.createElement("div"); row.className = "rewrite-selection__line";
        const check = document.createElement("input"); check.type = "checkbox";
        check.checked = isLocalRewrite ? rewriteTargets.has(line.number) : retainedLines.has(line.number);
        check.disabled = Boolean(prepared.submitted_job_id);
        check.setAttribute("aria-label", `${isLocalRewrite ? "重写" : "保留"}第 ${line.number} 句`);
        const location = document.createElement("span");
        location.textContent = line.stanza_number ? `第${line.stanza_number}阕·第${line.sentence_number}句（${line.number}）` : `第${line.number}句`;
        const text = document.createElement("input"); text.type = "text";
        text.value = sentenceValues[String(line.number)] || "";
        text.disabled = Boolean(prepared.submitted_job_id) || isLocalRewrite;
        text.setAttribute("aria-label", `第 ${line.number} 句正文`);
        if (line.length) text.placeholder = `${line.length}字`;
        const state = document.createElement("small");
        const refresh = () => {
          row.classList.toggle("is-selected", check.checked);
          state.textContent = isLocalRewrite
            ? check.checked ? "重写" : "保留"
            : check.checked ? "固定保留" : "空缺待补";
        };
        check.addEventListener("change", () => {
          if (isLocalRewrite) {
            if (check.checked) rewriteTargets.add(line.number); else rewriteTargets.delete(line.number);
          } else if (check.checked) retainedLines.add(line.number);
          else {
            retainedLines.delete(line.number);
            sentenceValues[String(line.number)] = "";
            text.value = "";
          }
          refresh(); syncRewriteSelection();
        });
        text.addEventListener("input", () => {
          sentenceValues[String(line.number)] = text.value;
          if (!isLocalRewrite) {
            check.checked = Boolean(text.value.trim());
            if (check.checked) retainedLines.add(line.number);
            else retainedLines.delete(line.number);
          }
          refresh(); syncRewriteSelection();
        });
        refresh(); row.append(check, location, text, state); selection.append(row);
      }
      syncRewriteSelection();
    };
    if (proposal.source_poem_id) {
      const link = document.createElement("a"); link.href = `#poem/view/${encodeURIComponent(proposal.source_poem_id)}`;
      link.textContent = "查看来源版本"; selection.append(link);
    }
    body.append(selection);
  }

  const config = document.createElement("div");
  config.className = "proposal-editor__config";
  const field = (labelText, control, example = "") => {
    const label = document.createElement("label");
    const text = document.createElement("span"); text.textContent = labelText;
    label.append(text);
    if (example) {
      const hint = document.createElement("small");
      hint.className = "proposal-editor__example";
      hint.textContent = `范例：${example}`;
      label.append(hint);
    }
    label.append(control); config.append(label);
    return control;
  };

  const count = document.createElement("input");
  count.type = "number";
  count.min = "1";
  count.max = "20";
  count.step = "1";
  count.value = String(prepared.candidate_count || proposal.candidate_count || 1);
  const countHint = document.createElement("small");
  field("候选数量", count);
  count.closest("label").append(countHint);
  const candidateLimit = () => isLocalRewrite ? 50 : 20;
  const refreshCandidateLimit = () => {
    const limit = candidateLimit();
    count.max = String(limit);
    countHint.textContent = `最多 ${limit} 个候选`;
    if (Number(count.value) > limit) count.value = String(limit);
  };
  refreshCandidateLimit();

  const meterType = document.createElement("select");
  ["唐诗", "宋词", "汉俳", "排律"].forEach((name) => meterType.add(new Option(name, name, false, name === proposal.meter_type)));
  field("诗体", meterType);
  const formName = document.createElement("select");
  const standardForms = ["五言绝句", "七言绝句", "五言律诗", "七言律诗", "五言排律", "七言排律", "汉俳"];
  for (const name of standardForms) formName.add(new Option(name, name));
  formName.value = proposal.form_name || "";
  field("篇式 / 词牌", formName);
  const songciSearch = document.createElement("input");
  songciSearch.type = "search";
  songciSearch.placeholder = "输入词牌名或别名筛选（800+）";
  songciSearch.autocomplete = "off";
  const songciSearchLabel = field("检索词牌", songciSearch).closest("label");
  songciSearchLabel.hidden = meterType.value !== "宋词";
  const selectedFormName = () => formName.value;
  const updateMeta = () => {
    const currentForm = [meterType.value, selectedFormName()].filter(Boolean).join(" · ");
    meta.textContent = `${currentForm || "诗词生成方案"}。${guidance}`;
  };
  const formLabel = formName.closest("label");
  const allowedForms = { "唐诗": new Set(["五言绝句", "七言绝句", "五言律诗", "七言律诗"]), "排律": new Set(["五言排律", "七言排律"]), "汉俳": new Set() };
  const updateFormOptions = () => {
    const type = meterType.value;
    const isSongci = type === "宋词";
    const isHanpai = type === "汉俳";
    const allowed = allowedForms[type] || new Set(meterCatalog.map((item) => item.name));
    const query = songciSearch.value.trim().toLocaleLowerCase();
    for (const option of formName.options) {
      const meter = meterCatalog.find((item) => item.name === option.value);
      const searchable = meter ? [meter.name, ...(meter.aliases || [])].join(" ").toLocaleLowerCase() : option.value.toLocaleLowerCase();
      option.hidden = isSongci
        ? !meter || (query && !searchable.includes(query))
        : !allowed.has(option.value);
    }
    formLabel.hidden = isHanpai;
    songciSearchLabel.hidden = !isSongci;
    if (isHanpai) formName.value = "汉俳";
    else if (![...formName.options].some((option) => option.value === formName.value && !option.hidden)) formName.value = [...formName.options].find((option) => !option.hidden)?.value || "";
    updateVariants();
  };
  formName.addEventListener("change", () => { updateFormOptions(); updateMeta(); });
  songciSearch.addEventListener("input", updateFormOptions);

  const variant = document.createElement("select");
  variant.add(new Option("默认变体", ""));
  const variantLabel = field("宋词变体", variant).closest("label");
  variantLabel.hidden = meterType.value !== "宋词";
  let meterCatalog = [];
  const loadMeterCatalog = async () => {
    return getLocalMeterCatalog();
  };
  const populateSongciForms = async () => {
    try {
      meterCatalog = await loadMeterCatalog();
      for (const item of meterCatalog) {
        if (item.name && !formName.querySelector(`option[value="${CSS.escape(item.name)}"]`)) formName.add(new Option(item.name, item.name));
      }
      let draftChoice = "";
      try { draftChoice = JSON.parse(localStorage.getItem(draftKey) || "null")?.formNameChoice || ""; } catch (_) { /* ignore */ }
      const wanted = draftChoice || proposal.form_name || formName.value;
      if (meterCatalog.some((item) => item.name === wanted)) formName.value = wanted;
      updateFormOptions();
    } catch (error) {
      announce(`宋词词牌加载失败：${error.message}`);
    }
  };
  const updateVariants = () => {
    const current = variant.value || proposal.variant_name || "";
    const meter = meterCatalog.find((item) => item.name === selectedFormName());
    const defaultVariant = meter?.default_variant || "";
    variant.replaceChildren(new Option(defaultVariant ? `默认变体（${defaultVariant}）` : "默认变体", defaultVariant));
    for (const name of (meter?.variants || [])) {
      if (name !== defaultVariant) variant.add(new Option(name, name, false, name === current));
    }
    variant.value = current && (meter?.variants || []).includes(current) ? current : defaultVariant;
    variantLabel.hidden = meterType.value !== "宋词";
  };
  meterType.addEventListener("change", () => {
    updateFormOptions();
    if (meterType.value === "宋词") populateSongciForms();
    updateMeta();
  });


  const rhymeBook = document.createElement("select");
  [["Xinyun", "中华新韵"], ["Pinshui", "平水韵"], ["Cilin", "词林正韵"], ["Tongyun", "中华通韵"]].forEach(([value, label]) => {
    rhymeBook.add(new Option(label, value, false, value === (proposal.rhyme_dict_name || "Xinyun")));
  });
  field("生成韵书", rhymeBook);

  const rhymeMode = document.createElement("select");
  [["auto", "自动选韵"], ["fixed", "固定韵部"], ["random", "随机选韵"]].forEach(([value, label]) => {
    rhymeMode.add(new Option(label, value, false, value === (proposal.rhyme_mode || "auto")));
  });
  rhymeMode.value = proposal.rhyme_mode || "auto";
  field("押韵策略", rhymeMode);

  const songciTaskType = document.createElement("select");
  songciTaskType.add(new Option("Instruction（按要求创作）", "instruction"));
  songciTaskType.add(new Option("One-shot（参考词牌范例）", "one-shot"));
  songciTaskType.value = "one-shot";
  const songciTaskTypeLabel = field("宋词生成模式", songciTaskType).closest("label");
  songciTaskTypeLabel.hidden = meterType.value !== "宋词" || isPartial;
  songciTaskType.addEventListener("change", () => saveDraft());

  const rhymeParts = document.createElement("div");
  const rhymePartLabel = field("指定韵部", rhymeParts).closest("label");
  let selectedRhymeParts = { ...(proposal.rhyme_parts || {}) };
  let availableRhymeGroups = [];
  let rhymeOptionsVersion = 0;
  const updateSentenceLayout = (lengths) => {
    if (isLocalRewrite) return;
    const nextLines = lengths.map((length, index) => ({ ...rewriteLines[index], number: index + 1, length }));
    const shapeChanged = nextLines.length !== rewriteLines.length || nextLines.some((line, index) => line.length !== rewriteLines[index]?.length);
    rewriteLines = nextLines;
    if (shapeChanged || !selection.querySelector('.rewrite-selection__line')) renderSentenceInputs();
  };
  const readRhymeParts = () => Object.fromEntries(Object.entries(selectedRhymeParts).filter(([group, part]) => part && availableRhymeGroups.includes(group)));
  const loadRhymeOptions = async () => {
    const version = ++rhymeOptionsVersion;
    rhymeParts.textContent = "正在加载韵部…";
    try {
      if (isPartial && !isLocalRewrite) {
        const lengths = partialSentenceLengths(meterType.value, selectedFormName(), numLines.value, JSON.parse(taskOptions.value || "{}"));
        if (lengths) updateSentenceLayout(lengths);
        else if (rewriteLines.length && !selection.querySelector('.rewrite-selection__line')) renderSentenceInputs();
      }
      const options = await getLocalRhymeOptions({
        rhyme_book: rhymeBook.value, meter_type: meterType.value, form_name: selectedFormName(),
        variant_name: variant.value || null, num_lines: numLines.value ? Number(numLines.value) : null,
        task_options: JSON.parse(taskOptions.value || "{}"),
      });
      if (version !== rhymeOptionsVersion) return;
      if (isPartial) {
        if (options.line_lengths) updateSentenceLayout(options.line_lengths);
        else if (meterType.value === "宋词") {
          const meter = await getDatasetJson("meter", `${selectedFormName()}.json`);
          if (version !== rhymeOptionsVersion) return;
          const selectedVariant = meter.variants.find((item) => item.name === variant.value) || meter.variants[0];
          updateSentenceLayout(flattenMeter(selectedVariant).clauses.map((line) => [...line.pattern].length));
        }
      }
      availableRhymeGroups = (options.groups || []).map(String);
      rhymeParts.replaceChildren();
      for (const group of options.groups || []) {
        const select = document.createElement("select");
        select.setAttribute("aria-label", `韵组 ${group}`);
        select.add(new Option("自动选择", ""));
        for (const part of options.parts || []) select.add(new Option(part, part));
        const current = selectedRhymeParts[String(group)] || "";
        select.value = [...select.options].some((option) => option.value === current) ? current : "";
        selectedRhymeParts[String(group)] = select.value;
        select.addEventListener("change", () => { selectedRhymeParts[String(group)] = select.value; });
        const row = document.createElement("div");
        row.append(document.createTextNode(`韵组 ${group}`), select);
        rhymeParts.append(row);
      }
      if (!rhymeParts.childElementCount) rhymeParts.textContent = "当前篇式没有可指定的韵组";
    } catch (error) {
      if (version === rhymeOptionsVersion) rhymeParts.textContent = `韵部加载失败：${error.message}`;
    }
  };

  const cleanTaskOptions = (options) => {
    const cleaned = { ...options };
    if (isLocalRewrite && !Object.hasOwn(cleaned, "omit_original_target_from_prompt")) {
      cleaned.omit_original_target_from_prompt = true;
    }
    delete cleaned.allow_aojiu;
    delete cleaned.allow_polyphonic_ending;
    return cleaned;
  };
  const cleanTaskOptionsText = () => {
    try {
      const options = JSON.parse(taskOptions.value || "{}");
      if (options && !Array.isArray(options) && typeof options === "object") taskOptions.value = JSON.stringify(cleanTaskOptions(options), null, 2);
    } catch (_) { /* Keep invalid JSON available for correction. */ }
  };
  const taskOptions = document.createElement("textarea");
  taskOptions.rows = 3;
  const initialTaskOptions = { ...(!isPartial ? { skip_planning: true } : {}), ...(proposal.task_options || {}) };
  taskOptions.value = JSON.stringify(cleanTaskOptions(initialTaskOptions), null, 2);
  taskOptions.placeholder = "{}";
  field("其他创作配置（JSON）", taskOptions);
  taskOptions.addEventListener("change", cleanTaskOptionsText);

  const fixedLines = document.createElement("textarea");
  fixedLines.rows = 3;
  fixedLines.value = JSON.stringify(proposal.fixed_lines || {}, null, 2);
  fixedLines.placeholder = '{"3":"指定的第三句"}';
  const fixedLinesLabel = field("指定句子（JSON）", fixedLines, '{"3":"江城秋色入帘明","4":"远浦归帆带晚风"}').closest("label");
  fixedLinesLabel.hidden = true;
  if (isPartial) {
    syncRewriteSelection = () => {
      if (isLocalRewrite) {
        retainedLines.clear();
        rewriteLines.filter((line) => !rewriteTargets.has(line.number)).forEach((line) => retainedLines.add(line.number));
      }
      fixedLines.value = JSON.stringify(Object.fromEntries(rewriteLines.filter((line) => retainedLines.has(line.number))
        .map((line) => [String(line.number), (sentenceValues[String(line.number)] || "").trim()])), null, 2);
      rewriteSummary.textContent = isLocalRewrite
        ? `重写 ${rewriteTargets.size} 句，保留 ${rewriteLines.length - rewriteTargets.size} 句`
        : `保留 ${rewriteLines.filter((line) => retainedLines.has(line.number)).length} 句，生成 ${rewriteLines.filter((line) => !retainedLines.has(line.number)).length} 句`;
      refreshCandidateLimit();
      fixedLines.dispatchEvent(new Event("input", { bubbles: true }));
    };
  }
  if (isLocalRewrite) renderSentenceInputs();
  if (isRevision) {
    meterType.disabled = true; formName.disabled = true; variant.disabled = true;
  }

  const rhymeLockHint = document.createElement("small");
  rhymeLockHint.className = "proposal-editor__rhyme-lock";
  rhymeLockHint.hidden = true;
  rhymePartLabel.append(rhymeLockHint);
  let rhymeLookupVersion = 0;
  const syncFixedLineRhyme = async () => {
    if (!isPartial) return;
    if (["唐诗", "排律", "汉俳"].includes(meterType.value)) return;
    const version = ++rhymeLookupVersion;
    let lines;
    try { lines = fixedLines.value.trim() ? JSON.parse(fixedLines.value) : {}; } catch (_) { return; }
    const rhymeLineNumbers = (meterType.value === "唐诗")
      ? (formName.value.includes("绝句") ? [1, 2, 4] : [1, 2, 4, 6, 8])
      : meterType.value === "排律"
        ? Array.from({ length: Number(numLines.value || 0) }, (_, index) => index + 1).filter((number) => number === 1 || number % 2 === 0)
        : [];
    const endings = Object.entries(lines)
      .filter(([line]) => rhymeLineNumbers.includes(Number(line)))
      .map(([, value]) => String(value).trim())
      .filter(Boolean);
    if (!endings.length) {
      rhymeMode.disabled = false; rhymeLockHint.hidden = true; return;
    }
    try {
      const results = await Promise.all(endings.map((line) => lookupLocalRhymeParts(line, rhymeBook.value)));
      if (version !== rhymeLookupVersion) return;
      const common = results.map((item) => new Set(item.parts || [])).reduce((shared, current) => new Set([...shared].filter((part) => current.has(part))));
      if (common.size === 1) {
        const part = [...common][0];
        rhymeMode.value = "fixed";
        selectedRhymeParts = { ...selectedRhymeParts, "1": part };
        loadRhymeOptions();
        rhymeMode.disabled = true;
        rhymeLockHint.hidden = false;
        rhymeLockHint.textContent = `已根据指定句末字锁定韵部：${part}，押韵策略不可修改。`;
        updateConditionalFields();
      } else {
        rhymeMode.disabled = false;
        rhymeLockHint.hidden = true;
      }
    } catch (_) {
      rhymeMode.disabled = false;
      rhymeLockHint.hidden = true;
    }
  };
  fixedLines.addEventListener("input", syncFixedLineRhyme);
  rhymeBook.addEventListener("change", syncFixedLineRhyme);
  rhymeBook.addEventListener("change", () => { selectedRhymeParts = {}; });
  const numLines = document.createElement("input");
  numLines.type = "number"; numLines.min = "4"; numLines.max = "128"; numLines.step = "2";
  numLines.value = proposal.num_lines || ""; numLines.placeholder = "例如：十六（排律）";
  for (const control of [rhymeBook, meterType, formName, variant, numLines]) control.addEventListener("change", loadRhymeOptions);
  taskOptions.addEventListener("change", loadRhymeOptions);

  const polyphonic = document.createElement("select");
  polyphonic.add(new Option("严格判定", "strict", false, proposal.strict_polyphonic === true));
  polyphonic.add(new Option("放行多音", "permissive", false, proposal.strict_polyphonic !== true));
  field("多音字", polyphonic);

  const numLinesLabel = field("句数", numLines).closest("label");
  if (isRevision) numLines.disabled = true;

  const allowAoJiu = document.createElement("input");
  allowAoJiu.type = "checkbox"; allowAoJiu.checked = proposal.task_options?.allow_aojiu !== false;
  const checkOptions = document.createElement("div");
  checkOptions.className = "proposal-editor__checks";
  config.append(checkOptions);
  const aoJiuLabel = document.createElement("label");
  aoJiuLabel.className = "proposal-editor__check";
  const aoJiuText = document.createElement("span"); aoJiuText.textContent = "允许拗救";
  aoJiuLabel.append(allowAoJiu, aoJiuText); checkOptions.append(aoJiuLabel);

  const allowPolyphonicEnding = document.createElement("input");
  allowPolyphonicEnding.type = "checkbox";
  allowPolyphonicEnding.checked = proposal.task_options?.allow_polyphonic_ending === true;
  const polyphonicEndingLabel = document.createElement("label");
  polyphonicEndingLabel.className = "proposal-editor__check";
  const polyphonicEndingText = document.createElement("span"); polyphonicEndingText.textContent = "首句末尾多音字";
  polyphonicEndingLabel.append(allowPolyphonicEnding, polyphonicEndingText); checkOptions.append(polyphonicEndingLabel);

  const hanpaiRhyme = document.createElement("select");
  for (const [value, label] of [["AAA", "AAA（三句押韵）"], ["ABA", "ABA（一、三句押韵）"], ["BAA", "BAA（二、三句押韵）"], ["none", "不押韵"], ["random", "随机押韵"]]) {
    hanpaiRhyme.add(new Option(label, value));
  }
  hanpaiRhyme.value = proposal.task_options?.rhyme_scheme || "random";
  const hanpaiRhymeLabel = field("汉俳押韵", hanpaiRhyme).closest("label");
  const syncHanpaiRhyme = () => {
    if (meterType.value !== "汉俳") return;
    const options = JSON.parse(taskOptions.value || "{}");
    options.rhyme_scheme = hanpaiRhyme.value;
    taskOptions.value = JSON.stringify(options, null, 2);
  };
  hanpaiRhyme.addEventListener("change", () => { syncHanpaiRhyme(); loadRhymeOptions(); });
  taskOptions.addEventListener("change", () => {
    try { hanpaiRhyme.value = JSON.parse(taskOptions.value || "{}").rhyme_scheme || "random"; } catch (_) {}
  });
   const updateConditionalFields = () => {
     songciTaskTypeLabel.hidden = meterType.value !== "宋词" || isPartial;
     hanpaiRhymeLabel.hidden = meterType.value !== "汉俳";
     try { syncHanpaiRhyme(); } catch (_) { /* Invalid JSON is reported when submitting. */ }

     numLinesLabel.hidden = meterType.value !== "排律";
     if (meterType.value !== "排律") numLines.value = "";
     aoJiuLabel.hidden = !["唐诗", "排律", "汉俳"].includes(meterType.value);
     polyphonicEndingLabel.hidden = !["唐诗", "排律"].includes(meterType.value);
     checkOptions.hidden = aoJiuLabel.hidden && polyphonicEndingLabel.hidden;
     cleanTaskOptionsText();
     rhymePartLabel.hidden = rhymeMode.value !== "fixed";
   };
   meterType.addEventListener("change", updateConditionalFields);
   rhymeMode.addEventListener("change", updateConditionalFields);
  updateConditionalFields();

  const actions = document.createElement("div");
  actions.className = "proposal-editor__actions";
  const state = document.createElement("span");
  state.className = "proposal-editor__state";
  state.textContent = "修改后将直接进入本地模型";
  const submit = document.createElement("button");
  submit.type = "button"; submit.className = "primary-button";
       submit.textContent = isLocalRewrite ? "提交改写" : isPartial ? "提交补完" : "提交生成";
  const draftKey = prepared.draft_key || `shiju:proposal-draft:${prepared.proposal_id}`;
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "quiet-button proposal-editor__edit";
  edit.textContent = "编辑";
  edit.hidden = true;
  const resetForResubmit = () => {
    const next = appendProposalEditor({
      ...structuredClone(prepared),
      proposal_id: crypto.randomUUID(),
      proposal: structuredClone(proposal),
      submitted_job_id: null,
      direct: true,
      draft_key: undefined,
      initial_draft: readDraft(),
      created_at: Date.now() / 1000,
    });
    if (next) {
      next.open = true;
      next.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  };
  const showResubmitDialog = () => {
    let dialog = document.querySelector("#proposal-resubmit-dialog");
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "proposal-resubmit-dialog";
      dialog.className = "proposal-resubmit-dialog";
      dialog.innerHTML = `<form method="dialog" class="proposal-resubmit-dialog__shell"><h2>确认重新提交</h2><p>将根据当前方案创建新的编辑面板，原面板和生成进度会保留。</p><div class="proposal-resubmit-dialog__actions"><button value="cancel" class="quiet-button">取消</button><button value="confirm" class="primary-button">确认重新提交</button></div></form>`;
      document.body.append(dialog);
    }
    dialog.returnValue = "cancel";
    dialog.addEventListener("close", () => { if (dialog.returnValue === "confirm") resetForResubmit(); }, { once: true });
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
  };
  edit.addEventListener("click", showResubmitDialog);
  let rewriteDraftToRestore = null;
  try {
    const draft = prepared.initial_draft || JSON.parse(localStorage.getItem(draftKey) || "null");
    if (draft && typeof draft === "object") {
      if (isLocalRewrite && rewriteSourceInput && draft.sourcePoemId && !prepared.submitted_job_id) {
        rewriteSourceInput.value = draft.sourcePoemId;
        rewriteDraftToRestore = draft;
      }
      for (const [control, key] of [[prompt, "prompt"], [count, "count"], [meterType, "meterType"], [formName, "formNameChoice"], [rhymeBook, "rhymeBook"], [rhymeMode, "rhymeMode"], [taskOptions, "taskOptions"], [fixedLines, "fixedLines"], [polyphonic, "polyphonic"], [numLines, "numLines"]]) {
        if (isRevision && [meterType, formName, fixedLines, numLines].includes(control)) continue;
        if (draft[key] !== undefined) control.value = draft[key];
      }
      if (draft.taskType) songciTaskType.value = draft.taskType;
      cleanTaskOptionsText();
      if (draft.variant && !isRevision) variant.value = draft.variant;
      if (isPartial && !isLocalRewrite && draft.sentenceInputs) {
        Object.assign(sentenceValues, draft.sentenceInputs);
        retainedLines.clear();
        (draft.retainedLines || []).forEach((number) => retainedLines.add(number));
        if (isLocalRewrite && Array.isArray(draft.rewriteTargets)) {
          rewriteTargets.clear();
          draft.rewriteTargets.filter((number) => rewriteLines.some((line) => line.number === number)).forEach((number) => rewriteTargets.add(number));
          retainedLines.clear();
          rewriteLines.filter((line) => !rewriteTargets.has(line.number)).forEach((line) => retainedLines.add(line.number));
        }
      } else if (isRevision && !isLocalRewrite && Array.isArray(draft.rewriteTargets)) {
        rewriteTargets.clear();
        draft.rewriteTargets.filter((number) => rewriteLines.some((line) => line.number === number)).forEach((number) => rewriteTargets.add(number));
        retainedLines.clear();
        rewriteLines.filter((line) => !rewriteTargets.has(line.number)).forEach((line) => retainedLines.add(line.number));
      } else if (isPartial && !isLocalRewrite && draft.fixedLines) {
        const restoredFixed = JSON.parse(draft.fixedLines);
        Object.assign(sentenceValues, restoredFixed);
        retainedLines.clear();
        Object.keys(restoredFixed).forEach((number) => retainedLines.add(Number(number)));
      }
      if (draft.rhymeParts !== undefined) {
        try { selectedRhymeParts = typeof draft.rhymeParts === "string" ? JSON.parse(draft.rhymeParts) : draft.rhymeParts; } catch (_) { /* ignore invalid draft */ }
      }
      if (draft.allowAoJiu !== undefined) allowAoJiu.checked = draft.allowAoJiu === true;
      if (draft.allowPolyphonicEnding !== undefined) allowPolyphonicEnding.checked = draft.allowPolyphonicEnding === true;
      // 草稿恢复可能改变诗体、押韵策略或句数，必须重新计算条件字段可见性。
      updateFormOptions();
      updateConditionalFields();
    }
  } catch (_) { /* ignore malformed local drafts */ }
  if (isLocalRewrite) renderSentenceInputs();
  refreshCandidateLimit();
  updateMeta();
  // 宋词需要先加载词牌目录并确定默认词牌，再请求韵部选项。
  // 否则首次打开面板时 form_name 为空，会请求 Songci_Meter/.json。
  (async () => {
    if (meterType.value === "宋词") await populateSongciForms();
    else updateFormOptions();
    loadRhymeOptions();
  })();
  const readDraft = () => {
    const draft = { prompt: prompt.value, count: count.value, meterType: meterType.value, formNameChoice: formName.value, variant: variant.value, rhymeBook: rhymeBook.value, rhymeMode: rhymeMode.value, rhymeParts: readRhymeParts(), taskOptions: taskOptions.value, taskType: songciTaskType.value, fixedLines: fixedLines.value, polyphonic: polyphonic.value, numLines: numLines.value, allowAoJiu: allowAoJiu.checked, allowPolyphonicEnding: allowPolyphonicEnding.checked };
    if (isPartial) {
      draft.sentenceInputs = { ...sentenceValues };
      draft.retainedLines = [...retainedLines];
    }
    if (isLocalRewrite) draft.rewriteTargets = [...rewriteTargets];
    if (isLocalRewrite) {
      draft.sourcePoemId = proposal.source_poem_id || null;
      draft.sourceVersion = proposal.source_version || null;
    }
    return draft;
  };
  const saveDraft = () => {
    if (panel.classList.contains("is-submitted")) return;
    if (!prompt.value.trim()) {
      try { localStorage.removeItem(draftKey); } catch (_) { /* storage may be unavailable */ }
      return;
    }
    const draft = readDraft();
    try { localStorage.setItem(draftKey, JSON.stringify(draft)); state.textContent = "已自动保存草稿"; } catch (_) { /* storage may be unavailable */ }
  };
  panel.addEventListener("input", saveDraft);
  panel.addEventListener("change", saveDraft);
  if (rewriteDraftToRestore) importRewriteSource(rewriteDraftToRestore);
  const fixedValidationHint = document.createElement("small");
  fixedValidationHint.setAttribute("aria-live", "polite");
  if (isPartial) selection.append(fixedValidationHint);
  else fixedLinesLabel.append(fixedValidationHint);
  let fixedValidationVersion = 0;
  let fixedValidationTimer;
  let fixedRhymeLocked = false;
  const currentFixedLinesForValidation = () => Object.fromEntries(
    rewriteLines.filter((line) => retainedLines.has(line.number))
      .map((line) => [String(line.number), (sentenceValues[String(line.number)] || "").trim()])
      .filter(([, text]) => text),
  );
  const validateFixedLines = async () => {
    const version = ++fixedValidationVersion;
    if (!isPartial || (isLocalRewrite && !isRevision)) {
      fixedValidationHint.textContent = "";
      return;
    }
    try {
      const lines = currentFixedLinesForValidation();
      if (!Object.keys(lines).length) {
        fixedValidationHint.textContent = "至少保留一句后可预检格律。";
        return { valid: true, lines: [] };
      }
      let validationOptions = {};
      try { validationOptions = taskOptions.value.trim() ? JSON.parse(taskOptions.value) : {}; } catch (_) { validationOptions = {}; }
      delete validationOptions.omit_original_target_from_prompt;
      delete validationOptions.allow_context_constraint_conflicts;
      if (["唐诗", "排律", "汉俳"].includes(meterType.value)) validationOptions.allow_aojiu = allowAoJiu.checked;
      const result = await validateLocalFixedLines({
        meter_type: meterType.value, form_name: selectedFormName(),
        variant_name: meterType.value === "宋词" ? variant.value : null,
        num_lines: meterType.value === "排律" && numLines.value ? Number(numLines.value) : null,
        rhyme_dict_name: rhymeBook.value, rhyme_mode: rhymeMode.value,
        rhyme_parts: rhymeMode.value === "fixed" ? readRhymeParts() : {},
        strict_polyphonic: polyphonic.value === "strict",
        task_options: validationOptions, fixed_lines: lines,
      });
      if (version !== fixedValidationVersion) return;
      const conflicts = (result.lines || []).filter((line) => !line.valid)
        .map((line) => `第${line.number}句：${line.error || "格律冲突"}`);
      const form = result.base_tone === 0 ? "平起" : result.base_tone === 1 ? "仄起" : "起式待定";
      fixedValidationHint.textContent = result.valid
        ? (result.base_tone === undefined ? "固定句格律预检通过。" : `${form}，固定句组合通过。`)
        : `${conflicts.length ? `${conflicts.join("；")}。` : `${result.error || "固定句组合不相容"}。`}提交时可选择继续。`;
      if (result.valid && result.rhyme_parts?.length === 1) {
        selectedRhymeParts = { ...selectedRhymeParts, "1": result.rhyme_parts[0] };
        rhymeMode.value = "fixed";
        rhymeMode.disabled = true;
        fixedRhymeLocked = true;
        rhymeLockHint.hidden = false;
        rhymeLockHint.textContent = `已根据固定韵句锁定韵部：${result.rhyme_parts[0]}`;
        loadRhymeOptions();
      } else {
        if (fixedRhymeLocked) {
          delete selectedRhymeParts["1"];
          rhymeMode.value = "auto";
          fixedRhymeLocked = false;
          loadRhymeOptions();
        }
        rhymeMode.disabled = false;
        rhymeLockHint.hidden = true;
      }
      updateConditionalFields();
      return result;
    } catch (error) {
      if (version === fixedValidationVersion) fixedValidationHint.textContent = `格律预检暂不可用：${error.message}。仍可提交，任务可能因格律约束失败。`;
      return null;
    }
  };
  const scheduleFixedValidation = () => {
    ++fixedValidationVersion;
    clearTimeout(fixedValidationTimer);
    fixedValidationTimer = setTimeout(() => validateFixedLines().catch(() => {}), 350);
  };
  for (const control of [fixedLines, rhymeBook, meterType, formName, numLines, polyphonic, allowAoJiu]) {
    control.addEventListener("input", scheduleFixedValidation);
    control.addEventListener("change", scheduleFixedValidation);
  }
  if (!prepared.submitted_job_id && !isLocalRewrite) scheduleFixedValidation();
  submit.addEventListener("click", async () => {
    const submitVersion = conversationLoadVersion;
    let submitConversationId = panel.dataset.conversationId || currentConversationId;
    const isCurrentSubmission = () => submitVersion === conversationLoadVersion && panel.parentElement === chatThread;
    if (panel.classList.contains("is-submitted")) return;
     const requirement = prompt.value.trim();
    if (!requirement || !selectedFormName()) {
      state.textContent = "编辑创作要求和篇式不能为空。也可以直接在对话中用自然语言让 Agent 重新生成方案。";
      return;
    }
    submit.disabled = true;
    state.textContent = "正在提交本地模型……";
    let createdConversationId = null;
    let taskAccepted = false;
    try {
       state.textContent = "正在提交任务；需要时将自动启动并加载本地模型……";
       let selectedTaskOptions = {};
       try {
         selectedTaskOptions = taskOptions.value.trim() ? JSON.parse(taskOptions.value) : {};
         if (!selectedTaskOptions || Array.isArray(selectedTaskOptions) || typeof selectedTaskOptions !== "object") throw new Error("配置必须是 JSON 对象");
       } catch (error) {
         state.textContent = `其他配置格式错误：${error.message}。示例：{}。也可以用自然语言让 Agent 重新生成方案。`;
         submit.disabled = false;
         return;
       }
       if (meterType.value === "汉俳") selectedTaskOptions.rhyme_scheme = hanpaiRhyme.value;
       selectedTaskOptions = cleanTaskOptions(selectedTaskOptions);
       if (["唐诗", "排律", "汉俳"].includes(meterType.value)) selectedTaskOptions.allow_aojiu = allowAoJiu.checked;
       if (["唐诗", "排律"].includes(meterType.value)) selectedTaskOptions.allow_polyphonic_ending = allowPolyphonicEnding.checked;
       let selectedFixedLines = {};
       try {
         selectedFixedLines = isPartial ? Object.fromEntries(rewriteLines.filter((line) => retainedLines.has(line.number))
           .map((line) => [String(line.number), (sentenceValues[String(line.number)] || "").trim()])) : {};
         if (!selectedFixedLines || Array.isArray(selectedFixedLines) || typeof selectedFixedLines !== "object") throw new Error("指定句子必须是 JSON 对象");
         for (const [line, text] of Object.entries(selectedFixedLines)) if (!/^\d+$/.test(line) || !String(text).trim()) throw new Error(`第${line}句已勾选保留，请填写正文或取消勾选`);
       } catch (error) {
         state.textContent = error.message;
         submit.disabled = false;
         return;
       }
       if (isPartial && !isLocalRewrite && Object.keys(selectedFixedLines).length === 0) {
         state.textContent = "部分生成必须至少指定一句需要原样保留的句子。";
         submit.disabled = false;
         return;
       }
       if (isLocalRewrite) {
         try {
           validateRewriteTargetNumbers([...rewriteTargets].sort((a, b) => a - b), rewriteLines.length);
         } catch (error) {
           state.textContent = error.message;
           submit.disabled = false;
           return;
         }
       }
       clearTimeout(fixedValidationTimer);
       const fixedValidation = await validateFixedLines();
       if (fixedValidation && !fixedValidation.valid) {
         const conflicts = (fixedValidation.lines || []).filter((line) => !line.valid).map((line) => `第${line.number}句：${line.error}`).join("\n");
         const reason = conflicts || fixedValidation.error || "固定句组合与格律不相容";
         const proceed = await confirmAction(
           `固定句存在格律冲突：${reason.replaceAll("\n", "；")}。继续提交会跳过这些上下文格律冲突约束，生成结果仍可能不符合词牌或篇式。`,
           { title: "发现格律冲突", confirmLabel: "仍然提交" },
         );
         if (!proceed) {
           state.textContent = "已取消提交。您可以修改固定句后重新预检，或再次提交并选择继续。";
           submit.disabled = false;
           return;
         }
         selectedTaskOptions.allow_context_constraint_conflicts = true;
       }
       if (meterType.value === "宋词" && !variant.value) {
         await populateSongciForms();
         if (!variant.value) throw new Error("词牌变体尚未加载，请稍后重试");
       }
       const candidateCount = Number(count.value);
       if (!Number.isInteger(candidateCount) || candidateCount < 1 || candidateCount > candidateLimit()) {
         throw new Error(`候选数量必须在 1 到 ${candidateLimit()} 之间`);
       }
       const directPayload = {
         requirement,
         theme: "",
         candidate_count: candidateCount,
         meter_type: meterType.value,
         form_name: selectedFormName(),
         variant_name: meterType.value === "宋词" ? variant.value : null,
         rhyme_dict_name: rhymeBook.value,
         rhyme_mode: rhymeMode.value,
         rhyme_parts: rhymeMode.value === "fixed" ? readRhymeParts() : {},
         strict_polyphonic: polyphonic.value === "strict",
         num_lines: meterType.value === "排律" && numLines.value ? Number(numLines.value) : null,
         task_options: selectedTaskOptions,
       };
       if (meterType.value === "宋词" && !isPartial && !isLocalRewrite) {
         directPayload.task_type = songciTaskType.value;
       }
       if (isPartial && !isLocalRewrite) {
         directPayload.fixed_lines = selectedFixedLines;
         directPayload.sentence_inputs = Object.fromEntries(rewriteLines.map((line) => [String(line.number), (sentenceValues[String(line.number)] || "").trim()]));
         if (!rewriteLines.length || Object.keys(selectedFixedLines).length >= rewriteLines.length) {
           throw new Error("诗词补完至少需要保留一句固定句，并留出一句空缺供生成");
         }
       }
       if (isLocalRewrite) {
         if (!proposal.source_poem_id || rewriteSourceInput.value.trim() !== proposal.source_poem_id) throw new Error("请先导入作品 ID");
         directPayload.original_text = proposal.original_text;
         directPayload.source_poem_id = proposal.source_poem_id;
         directPayload.source_version = proposal.source_version;
         directPayload.target_line_numbers = validateRewriteTargetNumbers(
           [...rewriteTargets].sort((a, b) => a - b), rewriteLines.length,
         );
       } else if (isRevision) {
         directPayload.target_line_numbers = rewriteLines.filter((line) => !retainedLines.has(line.number)).map((line) => line.number);
         if (prepared.direct) {
           directPayload.original_text = proposal.original_text;
           directPayload.source_poem_id = proposal.source_poem_id || null;
           directPayload.source_version = proposal.source_version || null;
         }
       }
       if (!isCurrentSubmission()) return;
       if (prepared.direct && !submitConversationId) {
         const conversation = await apiFetch("/v1/conversations", {
           method: "POST",
           headers: requestHeaders({ "Content-Type": "application/json" }),
           body: JSON.stringify({ title: isLocalRewrite ? "局部重写" : isPartial ? "部分生成" : "整首创作" }),
         });
         createdConversationId = conversation.id;
         submitConversationId = conversation.id;
         if (!isCurrentSubmission()) return;
         currentConversationId = conversation.id;
         chatSessionId = conversation.id;
         panel.dataset.conversationId = conversation.id;
       }
       const endpoint = prepared.direct
        ? `${prepared.kind === "rewrite" ? "/v1/poetry/jobs/rewrite" : isPartial ? "/v1/poetry/jobs/partial-generate" : "/v1/poetry/jobs/generate"}?conversation_id=${encodeURIComponent(submitConversationId)}`
         : `/v1/agent/proposals/${encodeURIComponent(prepared.proposal_id)}/submit`;
      // Agent 提案的原文和来源 ID 已保存在服务端提案中；提交 schema 只接收
      // 可编辑参数，不能把这些服务端来源字段作为额外请求字段发送。
      const proposalPayload = prepared.direct ? directPayload : Object.fromEntries(
        Object.entries(directPayload).filter(([key]) => !["original_text", "source_poem_id", "source_version"].includes(key)),
      );
       const payload = await apiFetch(endpoint, {
        method: "POST",
        headers: requestHeaders({ "Content-Type": "application/json", ...(prepared.direct ? { "Idempotency-Key": `${prepared.proposal_id}-resubmit-${Date.now()}` } : {}) }),
        body: JSON.stringify(prepared.direct ? proposalPayload : { conversation_id: submitConversationId, ...proposalPayload }),
      });
      taskAccepted = true;
      if (!isCurrentSubmission()) return;
      if (prepared.direct && createdConversationId) {
        setConversationLocation(createdConversationId);
        if (currentUser) localStorage.setItem(`shiju_conversation_${currentUser.id}`, createdConversationId);
        setChatTitle(prepared.kind === "rewrite" ? "局部重写" : isPartial ? "部分生成" : "整首创作");
        loadConversations().catch(() => {});
        const titlePayload = [
          isLocalRewrite ? "局部重写诗词" : isPartial ? "部分生成诗词" : "生成整首诗词",
          `${directPayload.meter_type}${directPayload.form_name ? ` ${directPayload.form_name}` : ""}`,
          requirement,
          isPartial && Object.keys(selectedFixedLines).length ? `指定句：${JSON.stringify(selectedFixedLines)}` : "",
        ].filter(Boolean).join("；");
        apiFetch(`/v1/conversations/${encodeURIComponent(createdConversationId)}/auto-title`, {
          method: "POST",
          headers: requestHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ message: titlePayload }),
        }).then((renamed) => {
          if (currentConversationId === createdConversationId) setChatTitle(renamed.title);
          loadConversations().catch(() => {});
        }).catch(() => {});
      }
      panel.querySelectorAll("textarea, input, select, .proposal-editor__import-source").forEach((control) => { control.disabled = true; });
      panel.classList.add("is-submitted");
      panel.open = false;
      panel.dataset.jobId = payload.job_id;
      state.textContent = "已提交，开始生成。";
      try { localStorage.removeItem(draftKey); } catch (_) { /* ignore */ }
      submit.textContent = "已提交";
      edit.hidden = false;
      startJobProgress(payload.job_id, payload, panel, submitConversationId);
    } catch (error) {
      if (createdConversationId && !taskAccepted) {
        if (currentConversationId === createdConversationId) {
          currentConversationId = null;
          chatSessionId = null;
          setConversationLocation(null);
        }
        if (currentUser && localStorage.getItem(`shiju_conversation_${currentUser.id}`) === createdConversationId) localStorage.removeItem(`shiju_conversation_${currentUser.id}`);
        const failedConversationId = createdConversationId;
        apiFetch(`/v1/conversations/${encodeURIComponent(failedConversationId)}`, { method: "DELETE" }).catch(() => {});
      }
      submit.disabled = false;
      state.textContent = `提交失败：${error.message}`;
    }
  });
  actions.append(state, edit, submit);
  body.prepend(meta, promptLabel);
  body.append(config, actions);
  panel.append(summary, body);
  if (prepared.submitted_job_id) {
    panel.classList.add("is-submitted");
    panel.querySelectorAll("textarea, input, select, .proposal-editor__import-source").forEach((control) => { control.disabled = true; });
    state.textContent = "已提交，生成进度如下。";
    submit.textContent = "已提交";
    edit.hidden = false;
  }
  insertChatTimelineNode(panel, prepared.created_at || anchor?.dataset.createdAt, anchor);
  chatThreadScroll.scrollTop = chatThreadScroll.scrollHeight;
  return panel;
}

function setChatBusy(busy) {
  chatBusy = busy;
  chatForm.setAttribute("aria-busy", String(busy));
  chatInput.disabled = busy;
  chatSubmit.disabled = false;
  if (chatCancel) chatCancel.hidden = true;
  chatSubmit.title = busy ? "暂停输出" : "发送";
  chatSubmit.setAttribute("aria-label", busy ? "暂停输出" : "发送");
  chatSubmit.innerHTML = busy
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1"></rect></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M6.5 10.5 12 5l5.5 5.5"></path></svg>';
}

function responseError(payload, status) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item) => {
      const field = (item.loc || []).filter((part) => part !== "body").join(".");
      return `${field ? `${field}：` : ""}${item.msg || "参数无效"}`;
    }).join("；") || `请求失败（${status}）`;
  }
  return `请求失败（${status}）`;
}

function clearJobProgressPolls() {
  activeJobPolls.forEach((control) => control.stop());
  activeJobPolls.clear();
}

const candidateStatusLabels = {
  queued: "等待生成",
  running: "正在生成",
  retrying: "重新尝试",
  succeeded: "生成完成",
  failed: "生成失败",
};

function appendCandidateMeta(list, label, value) {
  if (value === undefined || value === null || value === "") return;
  const group = document.createElement("div");
  const term = document.createElement("dt"); term.textContent = label;
  const description = document.createElement("dd"); description.textContent = value;
  group.append(term, description); list.append(group);
}

function formatCandidateTimestamp(value) {
  const timestamp = Number(value);
  if (!Number.isFinite(timestamp)) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).format(new Date(timestamp * 1000));
}

function candidateDetail(candidate, wasOpen = false, jobKind = "") {
  const status = candidate.status || "queued";
  const item = document.createElement("details");
  item.className = "job-progress__candidate";
  item.dataset.status = status;
  item.dataset.candidateOrdinal = candidate.ordinal;
  item.open = wasOpen;

  const summary = document.createElement("summary");
  const name = document.createElement("span");
  name.className = "job-progress__candidate-name";
  name.textContent = `候选 ${candidate.ordinal}`;
  const state = document.createElement("span");
  state.className = "job-progress__candidate-state";
  state.textContent = candidateStatusLabels[status] || status;
  const attempt = document.createElement("span");
  attempt.className = "job-progress__candidate-attempt";
  attempt.textContent = `第 ${candidate.attempt || 1} 次尝试`;
  summary.append(name, state, attempt);

  const body = document.createElement("div");
  body.className = "job-progress__candidate-body";
  const meta = document.createElement("dl");
  meta.className = "job-progress__candidate-meta";
  appendCandidateMeta(meta, "生成状态", candidateStatusLabels[status] || status);
  appendCandidateMeta(meta, "尝试次数", candidate.attempt || 1);
  appendCandidateMeta(meta, "题目", candidate.title);
  if (candidate.started_at) appendCandidateMeta(meta, "开始时间", formatCandidateTimestamp(candidate.started_at));
  if (candidate.finished_at) appendCandidateMeta(meta, "完成时间", formatCandidateTimestamp(candidate.finished_at));
  body.append(meta);

  const output = candidate.status === "succeeded"
    ? (candidate.raw_text || candidate.raw_output || candidate.partial_text)
    : (candidate.partial_text || candidate.raw_text || candidate.raw_output);
  const outputSection = document.createElement("section");
  const outputHeading = document.createElement("h4"); outputHeading.textContent = "模型原始输出";
  const outputText = document.createElement("pre"); outputText.textContent = output || "等待模型输出。";
  outputSection.append(outputHeading, outputText); body.append(outputSection);
  if (candidate.error) {
    const error = document.createElement("p");
    error.className = "job-progress__candidate-error";
    error.textContent = `失败原因：${candidate.error}`;
    body.append(error);
  }

  item.append(summary, body);
  return item;
}

async function ensureCandidateEvaluation(job, candidate, requestMeta, onComplete) {
  if (candidate.evaluation?.version >= 8 || candidate.status !== "succeeded" || !candidate.content) return null;
  const key = `${job.job_id}:${candidate.ordinal}`;
  if (completedEvaluations.has(key)) {
    candidate.evaluation = completedEvaluations.get(key);
    return null;
  }
  if (activeEvaluations.has(key)) return activeEvaluations.get(key);
  if (failedEvaluations.has(key)) return null;
  const operation = (async () => {
    try {
      console.info("[历史作品评分] 调用本地评分器", { job_id: job.job_id, ordinal: candidate.ordinal, meter_type: requestMeta.meter_type, form_name: requestMeta.form_name, contentLength: String(candidate.content || "").length });
      const evaluation = await evaluateGeneratedPoem(candidate.content, requestMeta);
      const saved = await apiFetch(`/v1/poetry/jobs/${encodeURIComponent(job.job_id)}/candidates/${candidate.ordinal}/evaluation`, {
        method: "PUT",
        headers: requestHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ evaluation }),
      });
      candidate.evaluation = saved.evaluation;
      completedEvaluations.set(key, saved.evaluation);
      await loadPoems();
      onComplete();
    } catch (error) {
      failedEvaluations.add(key);
      console.error("[历史作品评分] 候选评分失败", JSON.stringify({ job_id: job.job_id, ordinal: candidate.ordinal, meter_type: requestMeta.meter_type, form_name: requestMeta.form_name, message: error?.message || String(error), stack: error?.stack || "" }));
      onComplete();
    } finally {
      activeEvaluations.delete(key);
    }
  })();
  activeEvaluations.set(key, operation);
  return operation;
}

function renderPoemScore(scorePanel, inspect, poem, evaluation) {
  poem.evaluation = evaluation;
  scorePanel.classList.remove("poem-score--pending");
  const top = scorePanel.querySelector(".poem-score__top");
  inspect.textContent = "格律评分";
  inspect.disabled = false;
  let book = scorePanel.querySelector(".poem-score__book");
  if (!book) {
    book = document.createElement("span");
    book.className = "poem-score__book";
    top.append(book);
  }
  book.textContent = evaluation.rhyme_book_name || evaluation.rhyme_book_id || "韵书未标注";
  const grid = document.createElement("dl");
  [["结构", evaluation.structure_score], ["平仄", evaluation.tonal_score], ["押韵", evaluation.rhyme_score]].forEach(([label, score]) => {
    const group = document.createElement("div");
    const term = document.createElement("dt"); term.textContent = label;
    const description = document.createElement("dd"); description.textContent = scoreText(score);
    group.append(term, description); grid.append(group);
  });
  scorePanel.querySelector("dl")?.remove();
  scorePanel.append(grid);
  const paper = scorePanel.closest(".poem-handscroll__paper");
  const verses = paper?.querySelector(".poem-handscroll__verses");
  if (verses) {
    const songCi = isSongCi(poem);
    const sourceStanzas = songCi ? songCiStanzas(poem.content) : poemStanzas(poem.content);
    const ends = evaluation.result?.meter?.stanzaEnds;
    const stanzas = songCi && ends?.length ? groupSongCiStanzas(sourceStanzas, ends) : sourceStanzas;
    renderMarkedPoemVerses(verses, stanzas, evaluation.result);
  }
  inspect.onclick = () => openProsodyInspector(poem.evaluation, poem.title);
}

async function evaluateHistoricalPoem(poem, inspect, scorePanel) {
  const key = `${poem.job_id}:${poem.candidate_ordinal}`;
  if (activeEvaluations.has(key)) return activeEvaluations.get(key);
  inspect.disabled = true;
  inspect.textContent = "评分中";
  failedEvaluations.delete(key);
  try {
    const job = await apiFetch(`/v1/poetry/jobs/${encodeURIComponent(poem.job_id)}/state`);
    const ordinal = Number(poem.candidate_ordinal);
    const stored = (job.candidates || []).find((candidate) => Number(candidate.ordinal) === ordinal)
      || job.candidates?.[ordinal]
      || job.candidates?.[ordinal - 1]
      || {};
    const candidate = { ...stored, ordinal, status: "succeeded", content: stored.content || poem.content };
    if (!candidate.content) throw new Error("找不到这首诗的正文，无法评分");
    if (candidate.evaluation) {
      renderPoemScore(scorePanel, inspect, poem, candidate.evaluation);
      return;
    }
    const operation = ensureCandidateEvaluation(job, candidate, { ...(job.request || {}), ...poem }, () => {
      if (candidate.evaluation) renderPoemScore(scorePanel, inspect, poem, candidate.evaluation);
      else {
        inspect.disabled = false;
        inspect.textContent = "暂无评分，点击重试";
      }
    });
    await operation;
    if (!operation && completedEvaluations.has(key)) renderPoemScore(scorePanel, inspect, poem, completedEvaluations.get(key));
  } catch (error) {
    inspect.disabled = false;
    inspect.textContent = "暂无评分，点击重试";
    announce(`评分失败：${error.message}`);
  }
}

function renderAvailableScrolls(container, job, requestMeta, { autoEvaluate = true } = {}) {
  const candidates = (job.candidates || []).filter((candidate) => candidate.status === "succeeded" && candidate.content);
  const works = candidates.length
    ? candidates
    : job.status === "succeeded" && job.result?.candidates?.length
      ? job.result.candidates.map((candidate, index) => ({ ...candidate, ordinal: index + 1, status: "succeeded" }))
      : job.status === "succeeded" && job.result?.full_text
        ? [{ content: job.result.full_text, ordinal: 1, status: "succeeded" }]
        : [];
  if (!works.length) return;

  let list = container.querySelector(".job-progress__scrolls");
  if (!list) {
    const heading = document.createElement("div");
    heading.className = "job-progress__results-heading";
    const title = document.createElement("h3"); title.textContent = "诗稿卷轴";
    heading.append(title); container.append(heading);
    list = document.createElement("div");
    list.className = "job-progress__scrolls";
    container.append(list);
    const identity = document.createElement("p");
    identity.className = "job-progress__poem-id";
    identity.setAttribute("aria-live", "polite");
    container.append(identity);
    list.addEventListener("poem-identity-resolved", () => updateScrollNavigation(container));
    const navigation = document.createElement("nav");
    navigation.className = "job-progress__scroll-nav";
    navigation.setAttribute("aria-label", "切换诗稿卷轴");
    const previous = document.createElement("button"); previous.type = "button"; previous.className = "quiet-button"; previous.textContent = "上一首";
    const position = document.createElement("span"); position.className = "job-progress__scroll-position";
    const count = document.createElement("span"); count.className = "job-progress__scroll-count"; count.setAttribute("aria-live", "polite");
    const ordinalLabel = document.createElement("label"); ordinalLabel.textContent = " · 候选 ";
    const ordinalInput = document.createElement("input"); ordinalInput.type = "number"; ordinalInput.min = "1"; ordinalInput.step = "1";
    ordinalInput.className = "job-progress__scroll-ordinal"; ordinalInput.setAttribute("aria-label", "跳转到候选编号");
    const jump = () => {
      const ordinal = Number(ordinalInput.value);
      const target = [...list.children].find((scroll) => Number(scroll.dataset.candidateOrdinal) === ordinal);
      ordinalInput.setCustomValidity(target ? "" : "请输入已生成的候选编号");
      if (!target) { ordinalInput.reportValidity(); return; }
      container.dataset.selectedScroll = target.dataset.candidateOrdinal;
      updateScrollNavigation(container);
    };
    ordinalInput.addEventListener("input", () => ordinalInput.setCustomValidity(""));
    ordinalInput.addEventListener("change", jump);
    ordinalInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); jump(); }
    });
    ordinalLabel.append(ordinalInput); position.append(count, ordinalLabel);
    const next = document.createElement("button"); next.type = "button"; next.className = "quiet-button"; next.textContent = "下一首";
    const change = (step) => {
      const scrolls = [...list.children];
      const current = scrolls.findIndex((scroll) => scroll.dataset.candidateOrdinal === container.dataset.selectedScroll);
      const selected = scrolls[Math.max(0, Math.min(scrolls.length - 1, current + step))];
      if (selected) { container.dataset.selectedScroll = selected.dataset.candidateOrdinal; updateScrollNavigation(container); }
    };
    previous.addEventListener("click", () => change(-1));
    next.addEventListener("click", () => change(1));
    navigation.append(previous, position, next); container.append(navigation);
  }

  works.forEach((work, index) => {
    const ordinal = Number(work.ordinal || index + 1);
    const value = {
      ...requestMeta,
      ...work,
      job_id: job.job_id,
      candidate_ordinal: ordinal,
      created_at: work.created_at ?? job.created_at ?? requestMeta.created_at,
      content: work.content || work.text || work.full_text || work.display_text || "",
    };
    const key = `${job.job_id}:${ordinal}`;
    value.evaluation = (value.evaluation?.version >= 8 ? value.evaluation : null) || completedEvaluations.get(key);
    const evaluationState = value.evaluation?.evaluated_at || (activeEvaluations.has(key) ? "pending" : failedEvaluations.has(key) ? "failed" : "none");
    const existing = list.querySelector(`[data-candidate-ordinal="${ordinal}"]`);
    if (!existing || existing.dataset.evaluationState !== String(evaluationState)) {
      const scroll = createPoemHandscroll(value, {
        resultLabel: `候选 ${ordinal}`,
        favoritable: true,
        showTime: true,
        evaluationPending: !value.evaluation && !failedEvaluations.has(key),
      });
      scroll.dataset.candidateOrdinal = String(ordinal);
      scroll.dataset.evaluationState = String(evaluationState);
      if (existing) existing.replaceWith(scroll);
      else list.append(scroll);
    }
    if (autoEvaluate || (work.evaluation && !(work.evaluation.version >= 8))) ensureCandidateEvaluation(job, work, requestMeta, () => renderAvailableScrolls(container, job, requestMeta, { autoEvaluate }));
  });
  updateScrollNavigation(container);
}

function updateScrollNavigation(container) {
  const scrolls = [...container.querySelectorAll(".job-progress__scrolls > .poem-handscroll")];
  if (!scrolls.length) return;
  let selected = scrolls.findIndex((scroll) => scroll.dataset.candidateOrdinal === container.dataset.selectedScroll);
  if (selected < 0) selected = 0;
  container.dataset.selectedScroll = scrolls[selected].dataset.candidateOrdinal;
  scrolls.forEach((scroll, index) => { scroll.hidden = index !== selected; });
  const identity = container.querySelector(".job-progress__poem-id");
  const selectedScroll = scrolls[selected];
  if (identity) identity.textContent = `作品 ID：${selectedScroll.dataset.poemId || (selectedScroll.dataset.poemIdUnavailable ? "暂不可用" : "正在读取……")}`;
  const navigation = container.querySelector(".job-progress__scroll-nav");
  navigation.hidden = scrolls.length < 2;
  const [previous, next] = navigation.querySelectorAll("button");
  previous.disabled = selected === 0; next.disabled = selected === scrolls.length - 1;
  navigation.querySelector(".job-progress__scroll-count").textContent = `${selected + 1} / ${scrolls.length}`;
  const ordinalInput = navigation.querySelector(".job-progress__scroll-ordinal");
  if (document.activeElement !== ordinalInput) {
    ordinalInput.value = scrolls[selected].dataset.candidateOrdinal;
    ordinalInput.setCustomValidity("");
  }
  requestAnimationFrame(() => updatePoemLayout(scrolls[selected]));
}

function startJobProgress(jobId, initialJob = null, anchor = null, conversationId = currentConversationId) {
  if (!jobId || !currentUser?.id) return;
  if (currentConversationId !== conversationId || (anchor && anchor.parentElement !== chatThread)) return;
  const progressVersion = conversationLoadVersion;
  const existing = chatThread.querySelector(`.job-progress[data-job-id="${CSS.escape(jobId)}"]`);
  if (existing) return;
  if (activeJobPolls.has(jobId)) return;
  let terminalAtStart = initialJob?.status ? !["queued", "running"].includes(initialJob.status) : null;
  const details = document.createElement("details");
  details.className = "job-progress"; details.open = terminalAtStart !== true;
  details.dataset.jobId = jobId;
  details.dataset.conversationId = conversationId || "";
  details.dataset.createdAt = String(anchor?.dataset.createdAt || initialJob?.created_at || Date.now() / 1000);
  details.innerHTML = "<summary><span>诗词生成进度</span><span class=\"job-progress__count\">0 / 0</span></summary><p class=\"job-progress__status\">任务已提交，等待 Worker……</p><div class=\"job-progress__candidates\"></div><div class=\"job-progress__results\" aria-live=\"polite\"></div>";
  insertChatTimelineNode(details, initialJob?.created_at, anchor);
  chatThreadScroll.scrollTop = chatThreadScroll.scrollHeight;
  const statusNode = details.querySelector(".job-progress__status");
  const resultsNode = details.querySelector(".job-progress__results");
  const countNode = details.querySelector(".job-progress__count");
  const candidatesNode = details.querySelector(".job-progress__candidates");
  let timer = null;
  let stopped = false;
  let inFlight = false;
  let settled = false;
  const archivedOrdinals = new Set();
  const seenStartedCandidates = new Set();
  let requestMeta = initialJob?.request || {};
  const render = async (job) => {
    if (stopped || progressVersion !== conversationLoadVersion || currentConversationId !== conversationId || details.parentElement !== chatThread) return true;
    if (terminalAtStart === null && job.status) {
      terminalAtStart = !["queued", "running"].includes(job.status);
      if (terminalAtStart) details.open = false;
    }
    if (job.request) requestMeta = job.request;
    const labels = {
      queued: job.generation_phase === "loading" ? "模型加载中……" : job.waiting_for_worker ? "正在自动启动或加载本地模型，任务已排队……" : "已进入生成队列……",
      running: job.generation_phase === "loading" ? "模型加载中……" : "模型生成中……",
      succeeded: "生成完成",
      failed: "生成失败",
    };
    statusNode.textContent = labels[job.status] || job.status;
    details.querySelector("summary > span").textContent = `诗词生成 · ${labels[job.status] || job.status}`;
    countNode.textContent = `${job.completed ?? job.result?.candidates?.length ?? 0} / ${job.total ?? job.request?.candidate_count ?? 0}`;
    if (Array.isArray(job.candidates)) {
      const openCandidates = new Set([...candidatesNode.querySelectorAll("details[open]")].map((item) => item.dataset.candidateOrdinal));
      job.candidates.forEach((candidate) => {
        const ordinal = String(candidate.ordinal);
        if (["running", "retrying"].includes(candidate.status) && !seenStartedCandidates.has(ordinal)) {
          openCandidates.add(ordinal);
          seenStartedCandidates.add(ordinal);
        }
      });
      candidatesNode.replaceChildren(...job.candidates.map((candidate) => candidateDetail(candidate, openCandidates.has(String(candidate.ordinal)), job.kind)));
      const completedOrdinals = job.candidates.filter((candidate) => candidate.status === "succeeded").map((candidate) => candidate.ordinal);
      const hasNewCandidate = completedOrdinals.some((ordinal) => !archivedOrdinals.has(ordinal));
      completedOrdinals.forEach((ordinal) => archivedOrdinals.add(ordinal));
      if (hasNewCandidate && currentUser) loadPoems();
    }
    renderAvailableScrolls(resultsNode, job, requestMeta, { autoEvaluate: !terminalAtStart });
    if (job.status === "succeeded") {
      activeJobPolls.delete(jobId);
      if (!settled) {
        settled = true;
        if (!terminalAtStart) await loadPoems();
      }
      return true;
    }
    if (job.status === "failed") {
      const error = document.createElement("p");
      error.className = "job-progress__failure";
      error.textContent = job.error?.message || "任务失败";
      resultsNode.replaceChildren(error);
      activeJobPolls.delete(jobId);
      settled = true;
      return true;
    }
    return false;
  };
  const poll = async (knownJob = null) => {
    if (stopped || document.visibilityState === "hidden" || inFlight) return;
    inFlight = true;
    try {
      const job = knownJob || await apiFetch(`/v1/poetry/jobs/${encodeURIComponent(jobId)}/state`);
      if (await render(job)) return;
      if (!stopped) timer = window.setTimeout(() => poll(), 1800);
    } catch (error) {
      if (error.status === 429) {
        const retryAfter = Math.max(1, error.retryAfterSeconds || 5);
        statusNode.textContent = `状态查询较频繁，将在 ${retryAfter} 秒后自动继续。`;
        if (!stopped) timer = window.setTimeout(() => poll(), retryAfter * 1000);
        return;
      }
      if ([401, 403, 404].includes(error.status)) {
        statusNode.textContent = "当前账号无法继续查询此任务。";
        const control = activeJobPolls.get(jobId);
        activeJobPolls.delete(jobId);
        if (control) control.stop();
        stopped = true;
        document.removeEventListener("visibilitychange", onVisibility);
        return;
      }
      statusNode.textContent = `进度查询失败，稍后重试：${error.message}`;
      if (!stopped) timer = window.setTimeout(() => poll(), 3000);
    } finally {
      inFlight = false;
    }
  };
  const onVisibility = () => {
    if (document.visibilityState === "visible" && !stopped) {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => poll(), 0);
    }
  };
  document.addEventListener("visibilitychange", onVisibility);
  activeJobPolls.set(jobId, {
    stop() { stopped = true; if (timer) window.clearTimeout(timer); document.removeEventListener("visibilitychange", onVisibility); },
  });
  poll(initialJob);
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (chatBusy) return;
  if (!requireLocalProfile()) return;
  const attachmentFile = typeof chatAttachment !== "undefined" ? chatAttachment?.files?.[0] : null;
  const message = chatInput.value.trim() || (attachmentFile ? "请理解这张图片。" : "");
  if (!message) {
    chatInput.focus();
    return;
  }
  hideChatWelcome();
  await loadModelConfig();
  if (!chatModel?.value) { announce("请先在模型设置中配置服务端点。"); window.location.hash = "settings"; return; }
  const editedMessageId = editingMessageId;
  const images = [];
  if (attachmentFile) {
    const attachmentViewVersion = conversationLoadVersion;
    setChatBusy(true);
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("图片读取失败"));
        reader.readAsDataURL(attachmentFile);
      });
      if (attachmentViewVersion !== conversationLoadVersion) { setChatBusy(false); return; }
      images.push({ name: attachmentFile.name, data_url: dataUrl });
    } catch (error) { setChatBusy(false); announce(error.message); return; }
  }
  const submitViewVersion = conversationLoadVersion;
  const resumingTurnId = resumeTurnId;
  resumeTurnId = null;
  let editedVersions = null;
  if (editedMessageId) {
    setChatBusy(true);
    const editedArticle = chatThread.querySelector(`[data-message-id="${CSS.escape(editedMessageId)}"]`);
    try { editedVersions = JSON.parse(editedArticle?.dataset.messageVersions || "[]"); } catch { editedVersions = []; }
    const editedText = editedArticle?.dataset.messageText || "";
    if (!editedVersions.length) editedVersions = [{ content: editedText, version_number: 1 }];
    try {
      await apiFetch(`/v1/conversations/${encodeURIComponent(currentConversationId)}/messages/${encodeURIComponent(editedMessageId)}/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: message }),
      });
    } catch (error) {
      if (submitViewVersion !== conversationLoadVersion) return;
      setChatBusy(false);
      announce(`编辑失败：${error.message}`);
      return;
    }
    if (submitViewVersion !== conversationLoadVersion) return;
    const target = chatThread.querySelector(`[data-message-id="${CSS.escape(editedMessageId)}"]`);
    if (target) {
      let node = target;
      while (node) {
        const next = node.nextElementSibling;
        node.remove();
        node = next;
      }
    }
    editingMessageId = null;
  }
  hideChatWelcome();
  const nextVersions = editedMessageId
    ? [...(editedVersions || []), { content: message, version_number: (editedVersions?.length || 0) + 1 }]
    : undefined;
  const userMessageArticle = resumingTurnId ? null : appendMessage(message, "user", "", { ...(nextVersions ? { versions: nextVersions } : {}), images });
  if (attachmentFile && chatAttachment.files?.[0] === attachmentFile) { chatAttachment.value = ""; renderChatAttachment(null); }
  chatInput.value = "";
  chatInput.style.height = "";
  const waitingMessage = resumingTurnId
    ? chatThread.querySelector(`[data-turn-id="${CSS.escape(resumingTurnId)}"]`)
    : appendMessage("正在斟酌……", "assistant", "pending");
  if (!waitingMessage) return;
  activeAssistantMessage = waitingMessage;
  waitingMessage.dataset.executionPhase = "running";
  activeChatStopMode = null;
  const requestConversationId = currentConversationId;
  const requestVersion = conversationLoadVersion;
  const isCurrentRequest = () => requestVersion === conversationLoadVersion && waitingMessage.parentElement === chatThread;
  let reply = "";
  let previewReply = "";
  let terminalReceived = false;
  const requestController = new AbortController();
  activeChatRequest = requestController;
  setChatBusy(true);
  agentStatus.textContent = "思考中";

  try {
    const chatPayload = { message, model: chatModel.value, session_id: chatSessionId, conversation_id: currentConversationId, parent_message_id: editedMessageId || null };
    if (images.length) chatPayload.images = images;
    if (resumingTurnId) chatPayload.turn_id = resumingTurnId;
    const response = await fetch("/v1/agent/chat/stream", {
      method: "POST",
      headers: requestHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(chatPayload),
      signal: requestController.signal,
    });
    if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(responseError(payload, response.status)); }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ""; let responseConversationId = requestConversationId; const jobs = [];
    const consume = async () => {
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true }); const blocks = buffer.split("\n\n"); buffer = blocks.pop() || "";
        for (const block of blocks) {
          const event = block.match(/^event:\s*(.+)$/m)?.[1]; const dataLine = block.match(/^data:\s*(.*)$/m)?.[1]; if (!dataLine) continue;
          let data = {}; try { data = JSON.parse(dataLine); } catch { continue; }
          if (!isCurrentRequest()) { await reader.cancel(); return; }
          if (event === "turn.started" || event === "turn.queued") {
            activeTurnId = data.turn_id || activeTurnId;
            waitingMessage.dataset.turnId = activeTurnId || "";
            waitingMessage.dataset.branchId = data.branch_id || window.currentActiveBranchId || "";
            waitingMessage.dataset.conversationId = data.conversation_id || requestConversationId || "";
            responseConversationId = data.conversation_id || responseConversationId;
            if (data.user_message_id && userMessageArticle) {
              userMessageArticle.dataset.messageId = data.user_message_id;
              if (images.length) {
                chatImagePreviews.set(data.user_message_id, images);
                if (chatImagePreviews.size > 8) chatImagePreviews.delete(chatImagePreviews.keys().next().value);
              }
            }
            if (data.assistant_message_id) {
              waitingMessage.dataset.messageId = data.assistant_message_id;
              restoreToolActivity(waitingMessage);
            }
            if (data.conversation_id && currentConversationId === requestConversationId) {
              currentConversationId = data.conversation_id;
              setConversationLocation(currentConversationId);
              if (currentUser) localStorage.setItem(`shiju_conversation_${currentUser.id}`, currentConversationId);
            }
          }
          if (event === "assistant.preview.delta") { previewReply += data.text || ""; updateActivityPreview(waitingMessage, previewReply); }
          if (event === "assistant.preview.reset") { previewReply = ""; updateActivityPreview(waitingMessage, ""); }
          if (event === "assistant.delta") {
            reply += previewReply; previewReply = ""; reply += data.text || "";
            updateActivityPreview(waitingMessage, reply);
          }
          if (event === "tool.activity") updateToolActivity(waitingMessage, data.id, data.label, data.state, data.updated_at);
          if (event === "turn.retrying") {
            reply = ""; previewReply = "";
            waitingMessage._toolActivityItems ||= new Map();
            waitingMessage._toolActivityItems.delete("live-preview");
            for (const item of waitingMessage._toolActivityItems.values()) {
              if (item.state === "running") item.state = "interrupted";
            }
            waitingMessage._toolActivityItems.set("retry-status", { id: "retry-status", kind: "narration",
              text: "正在恢复连接并继续处理。", state: "completed" });
            renderToolActivityItem(waitingMessage);
          }
          if (event === "assistant.activity" && data.id && data.text) {
            waitingMessage._streamingPreview?.stop();
            waitingMessage._toolActivityItems ||= new Map();
            waitingMessage._toolActivityItems.delete("live-preview");
            waitingMessage._toolActivityItems.set(data.id, { id: data.id, kind: "narration", text: data.text,
              state: "completed", started_at: data.updated_at, updated_at: data.updated_at });
            renderToolActivityItem(waitingMessage);
          }
          if (["turn.completed", "turn.failed", "turn.cancelled", "turn.paused", "turn.interrupted"].includes(event)) {
            terminalReceived = true;
            if (event === "turn.completed") {
              reply = data.reply || reply || previewReply;
              previewReply = "";
              waitingMessage._toolActivityItems?.delete("live-preview");
            }
            finishToolActivity(waitingMessage);
            if (event === "turn.completed") replaceMessage(waitingMessage, reply);
          }
          if (event === "tool.started") {

            agentStatus.textContent = "正在判断意图";
            agentStatus.classList.add("is-working");
          }
          if (event === "tool.completed") {
            const completed = data.output?.ok === false ? "failed" : "completed";

            agentStatus.textContent = completed === "failed"
              ? "操作未成功，正在继续处理"
              : "工具结果已返回";
            agentStatus.classList.remove("is-working");
            if ((data.name === "prepare_generation" || data.name === "prepare_partial_generation" || data.name === "prepare_rewrite") && data.output?.ok) {
               appendProposalEditor(data.output.result, waitingMessage);
            }
          }
          if (event === "job.submitted") jobs.push({ ...data, _anchor: waitingMessage });
          if (event === "turn.completed") responseConversationId = data.conversation_id || responseConversationId;
          if (event === "turn.failed" || event === "error") {
            const message = data.message || data.error || "请求处理失败";
            const reference = data.error_code
              ? `（错误码：${data.error_code}${data.error_id ? `，追踪号：${data.error_id}` : ""}）`
              : "";
            throw new Error(`${message}${reference}`);
          }
          if (terminalReceived) return;
        }
      }
    };
    try {
      await consume();
    } finally {
      reader.cancel().catch(() => {});
    }
    if (isCurrentRequest() && !terminalReceived) throw new Error("连接已中断，正在恢复执行状态");
    const stillViewingRequest = isCurrentRequest();
    if (stillViewingRequest) {
      currentConversationId = responseConversationId;
      if (currentConversationId) setConversationLocation(currentConversationId);
      if (currentConversationId && currentUser) localStorage.setItem(`shiju_conversation_${currentUser.id}`, currentConversationId);
      replaceMessage(waitingMessage, reply || previewReply || "暂时无法回复。", reply || previewReply ? "" : "error");
      agentStatus.textContent = "已连接"; agentStatus.classList.remove("is-working");
      jobs.forEach((job) => startJobProgress(job.job_id, job, job._anchor));
      if (responseConversationId) await openConversation(responseConversationId);
    }
    await loadConversations();
  } catch (error) {
    if (!isCurrentRequest()) return;
    finishToolActivity(waitingMessage);
    if (error.name === "AbortError") {
      if (activeChatStopMode === "pause") {
        replaceMessage(waitingMessage, reply || "已暂停输出。", reply ? "" : "error");
        appendAssistantContinueButton(waitingMessage);
        agentStatus.textContent = "已暂停";
      } else {
        replaceMessage(waitingMessage, "发送已取消。", "error");
        agentStatus.textContent = "已取消";
      }
      agentStatus.classList.remove("is-working");
      if (currentConversationId) scheduleConversationRefresh(currentConversationId);
      return;
    }
    replaceMessage(waitingMessage, `暂时无法回复：${error.message}`, "error");
    agentStatus.textContent = "连接异常"; agentStatus.classList.remove("is-working");
    if (currentConversationId && !terminalReceived) scheduleConversationRefresh(currentConversationId);
  } finally {
    try {
      finishToolActivity(waitingMessage);
    } finally {
      if (activeChatRequest === requestController) {
        activeChatRequest = null;
        activeTurnId = null;
        if (activeAssistantMessage === waitingMessage) activeAssistantMessage = null;
        setChatBusy(false);
        chatInput.focus();
      }
    }
  }
});

function pauseActiveChat() {
  if (!activeChatRequest) return;
  activeChatStopMode = "pause";
  if (activeTurnId) {
    fetch(`/v1/agent/turns/${encodeURIComponent(activeTurnId)}/pause`, { method: "POST", headers: requestHeaders() }).catch(() => {});
  }
  activeChatRequest.abort();
  agentStatus.textContent = "正在暂停";
  agentStatus.classList.remove("is-working");
}

chatSubmit?.addEventListener("click", (event) => {
  if (!chatBusy) return;
  event.preventDefault();
  pauseActiveChat();
});

chatCancel?.addEventListener("click", pauseActiveChat);

chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = `${Math.min(chatInput.scrollHeight, 140)}px`;
});

chatThread.addEventListener("click", (event) => {
  const continueButton = event.target.closest("[data-continue-message]");
  if (continueButton) {
    if (chatBusy) return;
    resumeTurnId = continueButton.closest("[data-turn-id]")?.dataset.turnId || null;
    chatInput.value = "继续完成刚才的回答。";
    chatInput.dispatchEvent(new Event("input"));
    chatForm.requestSubmit();
    return;
  }
  const copy = event.target.closest("[data-copy-message]");
  if (copy) {
    const article = copy.closest("[data-message-id]");
    const versions = JSON.parse(article?.dataset.messageVersions || "[]");
    const index = Number(article?.dataset.versionIndex || 0);
    const text = versions[index]?.content || article?.dataset.messageText || "";
    navigator.clipboard?.writeText(text).then(() => announce("消息已复制")).catch(() => announce("复制失败，请手动选择文本"));
    return;
  }
  const versionStep = event.target.closest("[data-version-step]");
  if (versionStep) {
    if (chatBusy) {
      announce("正在生成回复，完成后才能切换分支。");
      return;
    }
    const article = versionStep.closest("[data-message-id]");
    if (!article) return;
    const versions = JSON.parse(article.dataset.messageVersions || "[]");
    const current = Number(article.dataset.versionIndex || 0);
    const next = Math.max(0, Math.min(versions.length - 1, current + Number(versionStep.dataset.versionStep || 0)));
    const siblings = JSON.parse(article.dataset.messageSiblings || "[]");
    const sibling = siblings[next];
    if (sibling?.id && currentConversationId) {
      const conversationId = currentConversationId;
      const switchVersion = ++conversationLoadVersion;
      setChatBusy(true);
      clearJobProgressPolls();
      clearConversationRefresh();
      apiFetch(`/v1/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(sibling.id)}/activate-branch`, { method: "POST" })
        .catch((error) => announce(`切换分支失败：${error.message}`))
        .then(() => { if (switchVersion === conversationLoadVersion && currentConversationId === conversationId) return openConversation(conversationId); })
        .catch((error) => { if (switchVersion + 1 === conversationLoadVersion) setChatBusy(false); announce(error.message); });
      return;
    }
    if (siblings.length) {
      announce("该分支暂时无法打开，请刷新对话后重试。");
      return;
    }
    article.dataset.versionIndex = String(next);
    updateMessageVersionControls(article);
    return;
  }
  const edit = event.target.closest("[data-edit-message]");
  if (edit) {
    const article = edit.closest("[data-message-id]");
    const content = article?.querySelector(".message-content");
    const paragraph = content?.querySelector("p");
    if (!article || !content || !paragraph || article.dataset.messageStatus === "pending" || chatBusy) return;
    editingMessageId = article.dataset.messageId || null;
    const editor = document.createElement("textarea");
    editor.className = "chat-message-inline-editor";
    editor.rows = Math.max(2, Math.min(6, paragraph.textContent.split("\\n").length));
    editor.value = paragraph.textContent || "";
    const actions = document.createElement("div");
    actions.className = "chat-message-inline-actions";
    const cancel = document.createElement("button");
    cancel.type = "button"; cancel.className = "chat-message-inline-cancel"; cancel.textContent = "取消";
    const save = document.createElement("button");
    save.type = "button"; save.className = "chat-message-inline-save"; save.textContent = "保存并重新生成";
    actions.append(cancel, save);
    paragraph.hidden = true; edit.closest(".chat-message-actions").hidden = true;
    content.append(editor, actions);
    cancel.addEventListener("click", () => {
      editor.remove(); actions.remove(); paragraph.hidden = false; edit.closest(".chat-message-actions").hidden = false;
      editingMessageId = null;
    });
    save.addEventListener("click", () => {
      const value = editor.value.trim();
      if (!value) { editor.focus(); return; }
      chatInput.value = value;
      chatInput.dispatchEvent(new Event("input"));
      chatForm.requestSubmit();
    });
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
    announce("已进入消息编辑状态");
    return;
  }
  const button = event.target.closest("[data-prompt]");
  if (!button) return;
  chatInput.value = button.dataset.prompt;
  chatInput.focus();
});

document.querySelector("#conversation-search-toggle")?.addEventListener("click", () => {
  const open = conversationSearch.hidden;
  conversationSearch.hidden = !open;
  if (open) conversationSearchInput.focus();
  else { conversationSearchInput.value = ""; filterConversations(); }
});
conversationSearchInput?.addEventListener("input", () => {
  filterConversations();
});
document.querySelector("#chat-sidebar-toggle")?.addEventListener("click", () => {
  setChatSidebarCollapsed(true);
  chatSidebarReopen?.focus();
});
document.querySelector("#chat-sidebar-reopen")?.addEventListener("click", () => {
  setChatSidebarCollapsed(false);
  chatSidebarToggle?.focus();
});
document.addEventListener("pointerdown", (event) => {
  if (window.matchMedia("(max-width: 760px)").matches
    && chatLayout
    && !chatLayout.classList.contains("is-sidebar-collapsed")
    && !event.target.closest(".chat-sidebar")) {
    setChatSidebarCollapsed(true);
  }
});

document.querySelector("#new-chat").addEventListener("click", () => {
  if (!requireLocalProfile()) return;
  activeChatRequest?.abort();
  clearJobProgressPolls();
  clearConversationRefresh();
  conversationLoadVersion += 1;
  activeChatRequest = null;
  chatSessionId = null;
  currentConversationId = null;
  currentConversationReadOnly = false;
  document.querySelector("#chat-form").hidden = false;
  setConversationLocation(null);
  editingMessageId = null;
  setChatTitle();
  closeProsodyInspector();
  localStorage.removeItem(`shiju_conversation_${currentUser.id}`);
  setChatBusy(false);
  chatThread.innerHTML = initialThreadMarkup;
  chatInput.value = "";
  chatInput.style.height = "";
  agentStatus.textContent = "待连接";
  chatInput.focus();
  announce("已新建对话。");
});

function beginConversationRename(row, conversationId) {
  if (!row || row.classList.contains("is-renaming")) return;
  const titleButton = row.querySelector("button[data-conversation-id]");
  const currentTitle = titleButton?.textContent?.trim() || "新建对话";
  row.classList.add("is-renaming");
  titleButton.hidden = true;
  row.querySelector("[data-rename-conversation]")?.setAttribute("hidden", "true");
  row.querySelector("[data-archive-conversation]")?.setAttribute("hidden", "true");
  const input = document.createElement("input");
  input.className = "conversation-rename-input";
  input.value = currentTitle;
  input.maxLength = conversationTitleMaxLength;
  input.setAttribute("aria-label", "新的对话名称");
  const save = document.createElement("button");
  save.type = "button"; save.className = "conversation-rename-save"; save.dataset.saveConversation = conversationId; save.textContent = "保存";
  const cancel = document.createElement("button");
  cancel.type = "button"; cancel.className = "conversation-rename-cancel"; cancel.dataset.cancelConversation = conversationId; cancel.textContent = "取消";
  row.prepend(input); row.append(save, cancel);
  input.focus(); input.select();
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); save.click(); }
    if (event.key === "Escape") { event.preventDefault(); cancel.click(); }
  });
}

function findBranchIdForMessage(messageId) {
  if (!messageId || !Array.isArray(window.currentConversationBranches)) return null;
  const branch = window.currentConversationBranches.find((item) => item.root_message_id === messageId && item.id !== window.currentActiveBranchId)
    || window.currentConversationBranches.find((item) => item.root_message_id === messageId);
  return branch?.id || null;
}

function findAlternateBranchId() {
  if (!Array.isArray(window.currentConversationBranches)) return null;
  return window.currentConversationBranches.find((item) => item.id !== window.currentActiveBranchId)?.id || null;
}

function isBranchRootMessage(messageId) {
  return Boolean(messageId && Array.isArray(window.currentConversationBranches)
    && window.currentConversationBranches.some((item) => item.root_message_id === messageId));
}

function cancelConversationRename(row) {
  if (!row) return;
  const input = row.querySelector(".conversation-rename-input");
  const titleButton = row.querySelector("button[data-conversation-id]");
  input?.remove();
  row.querySelector("[data-save-conversation]")?.remove();
  row.querySelector("[data-cancel-conversation]")?.remove();
  titleButton.hidden = false;
  row.querySelector("[data-rename-conversation]")?.removeAttribute("hidden");
  row.querySelector("[data-archive-conversation]")?.removeAttribute("hidden");
  row.classList.remove("is-renaming");
}

async function saveConversationRename(row, conversationId) {
  const input = row?.querySelector(".conversation-rename-input");
  const title = input?.value.trim();
  if (!title) { announce("请输入对话名称。"); input?.focus(); return; }
  const save = row.querySelector("[data-save-conversation]");
  save.disabled = true;
  try {
    const updated = await apiFetch(`/v1/conversations/${encodeURIComponent(conversationId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    const titleButton = row.querySelector("button[data-conversation-id]");
    titleButton.textContent = updated.title || title;
    titleButton.setAttribute("aria-label", `打开对话：${updated.title || title}`);
    row.querySelector("[data-rename-conversation]")?.setAttribute("aria-label", `修改对话名称：${updated.title || title}`);
    if (conversationId === currentConversationId) setChatTitle(updated.title || title);
    cancelConversationRename(row);
    announce("对话名称已更新。");
  } catch (error) {
    save.disabled = false;
    announce(`修改名称失败：${error.message}`);
  }
}

document.querySelector("#conversation-list").addEventListener("click", async (event) => {
  const rename = event.target.closest("[data-rename-conversation]");
  if (rename) { beginConversationRename(rename.closest(".conversation-row"), rename.dataset.renameConversation); return; }
  const saveRename = event.target.closest("[data-save-conversation]");
  if (saveRename) { await saveConversationRename(saveRename.closest(".conversation-row"), saveRename.dataset.saveConversation); return; }
  const cancelRename = event.target.closest("[data-cancel-conversation]");
  if (cancelRename) { cancelConversationRename(cancelRename.closest(".conversation-row")); return; }
  const archive = event.target.closest("[data-archive-conversation]");
  if (archive) { pendingArchiveId = archive.dataset.archiveConversation; document.querySelector("#archive-dialog").showModal(); return; }
  const button = event.target.closest("button[data-conversation-id]");
  if (!button) return;
  try { await openConversation(button.dataset.conversationId); }
  catch (error) { announce(`无法打开对话：${error.message}`); }
});

document.querySelector("#profile-form").addEventListener("submit", async (event) => { event.preventDefault(); if (!requireLocalProfile()) return; const status = document.querySelector("#profile-status"); status.textContent = "保存中……"; try { currentUser = await apiFetch("/v1/profile/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ display_name: document.querySelector("#profile-display-name").value.trim(), bio: document.querySelector("#profile-bio").value.trim() }) }); updateProfileUi(); status.textContent = "已保存"; } catch (error) { status.textContent = error.message; } });

showRoute(routeFromHash(), { scroll: false });
loadCurrentUser();

let localRuntimeConfig = {};
let runtimeModels = [];
function enableDiagnosticCopy(target) {
  if (!target || target.nextElementSibling?.dataset.diagnosticCopy) return;
  const button = document.createElement('button');
  button.type = 'button'; button.dataset.diagnosticCopy = '1';
  button.textContent = '复制错误与日志位置';
  button.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(target.textContent); button.textContent = '已复制'; }
    catch { button.textContent = '请选中文字复制'; }
  });
  target.after(button);
}
let runtimeRequestActive = false;
function renderWorkerStatus(data) {
  const labels = { released: '权重已释放；提交新任务时自动重新加载', stopped: '生成器未启动；保存模型目录后，提交任务会自动启动', loading: '正在加载模型；可提交任务排队，加载完成后自动生成', ready: '本地生成器已就绪，可以提交诗词生成', failed: '模型加载或运行失败，请查看下方错误并检查依赖、模型和显存' };
  const pendingVerification = (data.worker.checks || []).some(c => c.state === 'pending');
  const text = data.worker.ready ? (pendingVerification ? '模型已加载；配对未验证，实际生成成功后自动记住' : labels.ready) : data.worker.state === 'loading' ? (data.worker.message || labels.loading) : data.worker.external_worker ? '已连接独立运行的诗词生成器' : labels[data.worker.state];
  document.querySelector('#local-worker-status').textContent = text;
  document.querySelector('#local-model-check-results').replaceChildren(...(data.worker.checks || []).map(c => { const li = document.createElement('li'); li.textContent = `${c.name}：${({passed:'通过',running:'进行中',failed:'失败',pending:'待实际生成验证'})[c.state] || '未测试'} · ${c.detail}`; return li; }));
  const diagnosticText = data.worker.diagnostics?.log_path ? `日志：${data.worker.diagnostics.log_path}\n诊断编号：${data.worker.diagnostics.diagnostic_id}` : '';
  document.querySelector('#local-worker-error').textContent = [data.worker.state === 'failed' ? data.worker.message : '', data.worker.error, data.worker.state === 'failed' ? diagnosticText : ''].filter(Boolean).join('\n\n');
  if (data.worker.plan) document.querySelector('#local-model-plan').textContent = `${data.worker.plan.label}。${data.worker.plan.note}`;
  document.querySelector('#local-worker-error').hidden = !data.worker.error;
  if (data.worker.error) enableDiagnosticCopy(document.querySelector('#local-worker-error'));
  document.querySelector('#local-readiness').textContent = `${data.agent_ready ? '对话模型：已配置' : '对话模型：未配置（可选，不影响手动打开创作面板）'}；诗词生成：${text}`;
  document.querySelector('#local-worker-start').disabled = ['ready', 'loading', 'released'].includes(data.worker.state);
  document.querySelector('#local-worker-stop').disabled = !['ready', 'loading', 'released'].includes(data.worker.state);
}
async function loadLocalRuntime(endpoints) {
  const data = await apiFetch('/v1/local-runtime');
  localRuntimeConfig = data.config;
  runtimeModels = [...new Set(endpoints.map((item) => item.model))];
  if (!runtimeModels.includes(localRuntimeConfig.default_model)) localRuntimeConfig.default_model = '';
  document.querySelector('#local-model-path').value = data.config.model_path;
  const verified = await apiFetch('/v1/local-runtime/verified-models');
  const choice = document.querySelector('#local-model-choice');
  choice.replaceChildren(new Option('选择已通过当前环境验收的模型', ''));
  verified.models.forEach(m => choice.add(new Option(`${m.name} · 验收通过 · ${m.path}`, m.path)));
  choice.value = data.config.model_path;
  document.querySelector('#local-model-auto').checked = data.config.auto_start;
  document.querySelector('#local-model-idle').value = String(data.config.idle_unload_seconds ?? 300);
  document.querySelector('#application-python-path').textContent = data.environment.application_python;
  document.querySelector('#inference-python-path').textContent = data.environment.local_enabled ? data.environment.inference_python : `未启用；启动器目前记住的是 ${data.environment.inference_python}。请在启动器勾选本地生成，并选择所需推理环境后重新启动。`;
  checkLocalEnvironment().catch(() => {});
  const remembered = localStorage.getItem('shiju_selected_chat_model');
  const preferred = remembered || data.config.default_model;
  if (preferred && runtimeModels.includes(preferred)) chatModel.value = preferred;
  renderWorkerStatus(data);
}
chatModel?.addEventListener('change', () => localStorage.setItem('shiju_selected_chat_model', chatModel.value));
async function saveRuntimeConfig(section) {
  const config = { ...localRuntimeConfig };
  config.model_path = document.querySelector('#local-model-path').value.trim();
  config.quantization = 'auto';
  config.auto_start = document.querySelector('#local-model-auto').checked;
  config.idle_unload_seconds = Number(document.querySelector('#local-model-idle').value);
  const data = await apiFetch('/v1/local-runtime', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(config) });
  localRuntimeConfig = data.config;
  renderWorkerStatus(data);
  return data;
}
async function checkLocalEnvironment() {
  const button = document.querySelector('#local-environment-check');
  button.disabled = true;
  const status = document.querySelector('#local-environment-status');
  status.textContent = '正在使用所选推理环境检查依赖与 CUDA……';
  try {
    const data = await apiFetch('/v1/local-runtime/check', {method: 'POST'});
    const environment = data.environment;
    status.textContent = environment.ok ? `环境检查通过：${environment.gpu?.name || 'CPU'}。模型仍需通过实际加载、有效输出及约束生成。` : (environment.errors || []).join('；');
    if (environment.diagnostics?.log_path) status.textContent += `\n日志：${environment.diagnostics.log_path}\n诊断编号：${environment.diagnostics.diagnostic_id}`;
    if (!environment.ok) enableDiagnosticCopy(status);
    document.querySelector('#local-model-plan').textContent = data.error || (data.plan ? `${data.plan.label}。${data.plan.note}` : '选择模型后自动确定运行方式，完整验收通过后才会就绪。');
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
}
document.querySelector('#local-environment-check')?.addEventListener('click', () => checkLocalEnvironment());
document.querySelector('#local-generator-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try { await saveRuntimeConfig('generator'); document.querySelector('#local-worker-action-status').textContent = '配置已保存。'; }
  catch (error) { document.querySelector('#local-worker-action-status').textContent = error.message; }
});
document.querySelector('#local-model-scan')?.addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  const status = document.querySelector('#local-model-scan-status');
  status.textContent = '正在扫描完整的本地模型……';
  try {
    const directory = document.querySelector('#local-model-scan-root').value.trim();
    const data = await apiFetch('/v1/local-runtime/models?directory=' + encodeURIComponent(directory));
    const choice = document.querySelector('#local-model-choice');
    choice.replaceChildren(new Option('请选择模型', ''));
    data.models.forEach((model) => { const option = new Option(`${model.name} · ${model.supported ? model.reason : '不可用：' + model.reason} · ${model.path}`, model.path); option.disabled = !model.supported; choice.add(option); });
    const rejected = data.models.filter((model) => !model.supported);
    const details = document.querySelector('#local-model-unsupported');
    details.hidden = !rejected.length;
    details.open = rejected.length > 0 && !data.models.some((model) => model.supported);
    details.querySelector('ul').replaceChildren(...rejected.map((model) => { const item = document.createElement('li'); item.textContent = `${model.name}：${model.reason}`; return item; }));
    const supported = data.models.filter((model) => model.supported).length;
    status.textContent = `共发现 ${data.models.length} 个模型目录：${supported} 个可尝试加载，${rejected.length} 个暂不可用。${data.environment?.ok ? '完整验收通过后才会就绪。' : (data.environment?.errors || []).join('；')} 扫描位置：${(data.scanned_roots || []).join('；')}`;
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});
document.querySelector('#local-model-choice')?.addEventListener('change', (event) => {
  if (event.target.value) document.querySelector('#local-model-path').value = event.target.value;
});
for (const action of ['start', 'stop']) {
  document.querySelector(`#local-worker-${action}`)?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      document.querySelector('#local-worker-action-status').textContent = '';
      if (action === 'start') await saveRuntimeConfig('generator');
      await apiFetch(`/v1/local-runtime/${action}`, { method: 'POST' });
      renderWorkerStatus(await apiFetch('/v1/local-runtime'));
    } catch (error) { document.querySelector('#local-worker-action-status').textContent = error.message; }
    finally { button.disabled = false; }
  });
}
setInterval(async () => {
  if (!currentUser || window.location.hash !== '#settings' || runtimeRequestActive) return;
  runtimeRequestActive = true;
  try { renderWorkerStatus(await apiFetch('/v1/local-runtime')); } catch { /* Keep the last observed state. */ }
  finally { runtimeRequestActive = false; }
}, 3000);
