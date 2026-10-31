// Kopi Kiosk phone page: each stall's current menu, plus an order total that never leaves the phone.
// Menus are polled so a break and a fix appear live: a changed price flashes, a wrong price
// stays marked, and one notice names the page's state (LIVE, CHANGED, RESTORED; see incident.js).
// Reading order: start and the poll loop, then what changes the page's state, then rendering.
import { formatPrice } from "/site/price.js";
import { RESTORED_MS, START, State, nextState, noticeFor, orderTotals, wrongPrices } from "/site/incident.js";

const POLL_MS = 5000;
const FLASH_MS = 2400;

let config = { images: { hero: "", stalls: {}, items: {} }, promo: null, expected_prices: {} };
let menus = {};                 // stall id -> last menu fetched successfully
let lastMenusJson = "";
let incident = START;
let offline = false;
let restoredTimer = 0;          // ends RESTORED; one at a time
const order = new Map();        // item id -> quantity, local only
const lastPrice = new Map();    // item id -> price at the previous fetch
const flashUntil = new Map();   // item id -> time its changed-price flash ends
let shownNotice = "";           // the notice on screen, so an unchanged one is left alone
let shownTone = "";             // its tone, so the entrance plays once per state

start();

// --- Start and the poll loop -------------------------------------------------------------------

async function start() {
  try {
    config = { ...config, ...(await fetchJson("/site/config.json")) };
  } catch (error) {
    console.warn(error);
    setOffline(true);
    // No retry limit, on purpose: one small request every 5 s, and a phone left open should
    // connect whenever the kiosk comes up.
    setTimeout(start, POLL_MS);
    return;
  }
  document.title = config.title;
  document.getElementById("title").textContent = config.title;
  if (config.images.hero) {
    const hero = document.getElementById("hero");
    hero.src = config.images.hero;
    hero.hidden = false;
  }
  if (config.guide_url) {
    const link = document.getElementById("guide");
    link.href = config.guide_url;
    link.hidden = false;
  }
  await poll();
  setTimeout(() => document.getElementById("menus").classList.remove("first"), 1000); // entrance once
}

// One poll at a time: the next starts 5 s after this one settles, so slow Wi-Fi can never land an
// older answer after a newer one. A poll that throws is a page bug: it is reported, and polling
// carries on, so the phone recovers as soon as the menus do.
async function poll() {
  try {
    await refresh();
  } catch (error) {
    console.error(error);
  }
  setTimeout(poll, POLL_MS);
}

// Only the fetch is guarded: a network failure is OFFLINE, but a bug in the page must surface as
// an error, never pass itself off as "reconnecting".
async function refresh() {
  const paths = config.stalls.map((id) => `/menus/stall-${id}.json`);
  let fetched;
  try {
    fetched = await Promise.all(paths.map(fetchJson));
  } catch (error) {
    console.warn(error);
    setOffline(true); // keeps the last good menu on screen; never goes blank
    return;
  }
  setOffline(false);
  const json = JSON.stringify(fetched);
  if (json === lastMenusJson) return; // nothing changed; keep the DOM (and any tap) as it is
  markChangedPrices(fetched);
  menus = Object.fromEntries(fetched.map((menu) => [menu.stall, menu]));
  updateIncident();
  render();
  lastMenusJson = json; // only once shown: a poll that throws midway is processed again next time
}

// A 4 s limit, so a hung request becomes OFFLINE instead of holding up every poll after it.
// AbortSignal.timeout is missing on iOS 15: there the request has no limit, and one poll at a
// time still keeps answers in order.
async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store", signal: AbortSignal.timeout?.(4000) });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

// --- What changes the page's state -------------------------------------------------------------

// Changes only the pill and the notice, and only on a change: the rows (and any tap in progress)
// stay as they are through a Wi-Fi blip.
function setOffline(isOffline) {
  if (offline === isOffline) return;
  offline = isOffline;
  const pill = document.getElementById("live");
  pill.classList.toggle("down", offline);
  pill.textContent = offline ? "Reconnecting" : "Live menu";
  renderNotice();
}

// Move the incident on from the menus on screen. Entering RESTORED (re)starts its one end timer.
function updateIncident() {
  const previous = incident.state;
  incident = nextState(incident, wrongPrices(Object.values(menus), config.expected_prices), Date.now());
  if (incident.state === State.RESTORED && previous !== State.RESTORED) {
    clearTimeout(restoredTimer);
    restoredTimer = setTimeout(() => {
      updateIncident();
      render();
    }, RESTORED_MS + 50);
  }
}

// Remember which prices moved since the last fetch, so render() can flash them.
function markChangedPrices(fetched) {
  const now = Date.now();
  for (const menu of fetched) {
    for (const item of menu.items) {
      const previous = lastPrice.get(item.id);
      if (previous !== undefined && previous !== item.price) flashUntil.set(item.id, now + FLASH_MS);
      lastPrice.set(item.id, item.price);
    }
  }
  setTimeout(render, FLASH_MS + 50); // clear the flash class once it has played
}

function changeQuantity(itemId, delta) {
  order.set(itemId, Math.max(0, (order.get(itemId) ?? 0) + delta));
  if (delta > 0) navigator.vibrate?.(8); // a tap you can feel, where the phone supports it
  render();
}

// --- Rendering ---------------------------------------------------------------------------------

function render() {
  const shownMenus = Object.values(menus);
  const wrongById = new Map(wrongPrices(shownMenus, config.expected_prices).map((w) => [w.id, w]));
  document.getElementById("menus").replaceChildren(...shownMenus.map((menu) => renderStall(menu, wrongById)));
  renderTotal(orderTotals(shownMenus, order, config.expected_prices), shownMenus[0]?.currency);
  renderNotice();
}

function renderStall(menu, wrongById) {
  const section = el("section", undefined, "stall");
  const bannerSrc = config.images.stalls[menu.stall];
  if (bannerSrc) {
    const banner = el("div", undefined, "banner");
    banner.append(img(bannerSrc), el("h2", menu.title));
    section.append(banner);
  } else {
    section.append(el("h2", menu.title));
  }
  const list = el("ul");
  for (const item of menu.items) list.append(renderItem(item, menu.currency, wrongById.get(item.id)));
  section.append(list);
  return section;
}

// A row is [photo | text]; the control comes first in the text so it floats top-right and the
// name and price flow around it (see .item in index.html).
function renderItem(item, currency, wrong) {
  const row = el("li", undefined, "item");
  if ((flashUntil.get(item.id) ?? 0) > Date.now()) row.classList.add("changed");
  if (wrong) row.classList.add("wrong");
  if (incident.state === State.RESTORED && incident.items.includes(item.id)) row.classList.add("restored");

  const thumbSrc = config.images.items[item.id];
  row.append(thumbSrc ? img(thumbSrc, "thumb") : el("span", undefined, "thumb"));

  const priceLine = el("div", undefined, "price-line");
  priceLine.append(el("span", formatPrice(item.price, currency), "price"));
  const promo = promoFor(item);
  if (promo) priceLine.append(el("span", promo.label, "badge"));
  // Struck beside the price: what it should be (a wrong price), or what it was (the promo).
  const reference = wrong ? wrong.expected : promo ? promo.base_price : null;
  if (reference !== null) priceLine.append(el("span", formatPrice(reference, currency), "was"));

  const slot = el("div", undefined, "slot");
  slot.append(renderStepper(item));
  const text = el("div", undefined, "text");
  text.append(slot, el("div", item.name, "name"), priceLine);
  row.append(text);
  return row;
}

// The promo badge shows only while the promo price is actually live, so phones show
// whether the valid change survived the rollback.
function promoFor(item) {
  const promo = config.promo;
  return promo && promo.item === item.id && item.price === promo.price ? promo : null;
}

// A lone "+" until the item is ordered, then "− n +". Both sit in the same fixed-width slot.
function renderStepper(item) {
  const quantity = order.get(item.id) ?? 0;
  const stepper = el("div", undefined, quantity ? "stepper" : "stepper solo");
  const plus = el("button", "+", "add");
  plus.setAttribute("aria-label", `Add one ${item.name}`);
  plus.onclick = () => changeQuantity(item.id, 1);
  if (quantity) {
    const minus = el("button", "−");
    minus.setAttribute("aria-label", `Remove one ${item.name}`);
    minus.onclick = () => changeQuantity(item.id, -1);
    stepper.append(minus, el("span", String(quantity), "qty"));
  }
  stepper.append(plus);
  return stepper;
}

// The total at today's prices; while it differs from the expected total, the expected one is
// struck above it, so the bug lands in the viewer's own order.
function renderTotal(totals, currency) {
  const totalNode = document.getElementById("total");
  const text = currency ? formatPrice(totals.total, currency) : "";
  if (totalNode.textContent && totalNode.textContent !== text) {
    totalNode.classList.remove("bump");
    void totalNode.offsetWidth; // restart the animation
    totalNode.classList.add("bump");
  }
  totalNode.textContent = text;
  const isWrong = totals.total !== totals.expectedTotal;
  const wasNode = document.getElementById("total-was");
  wasNode.textContent = isWrong && currency ? formatPrice(totals.expectedTotal, currency) : "";
  wasNode.hidden = !isWrong;
  totalNode.classList.toggle("alarm", isWrong);
  totalNode.classList.toggle("ok", !isWrong && totals.count > 0 && incident.state === State.RESTORED);
  document.querySelector(".struk").classList.toggle("alarm", isWrong);
  const count = totals.count;
  document.getElementById("count").textContent = count ? `${count} item${count === 1 ? "" : "s"}` : "No items yet";
}

// Touches the DOM only when the notice changes: role="status" announces every change to screen
// readers, and the entrance animation plays only when the tone (the state) changes.
function renderNotice() {
  const shownMenus = Object.values(menus);
  const notice = noticeFor({
    incident, offline, hasMenu: shownMenus.length > 0, totals: orderTotals(shownMenus, order, config.expected_prices),
    currency: shownMenus[0]?.currency, promo: config.promo, menus: shownMenus,
  });
  const key = JSON.stringify(notice);
  if (key === shownNotice) return;
  shownNotice = key;
  const node = document.getElementById("notice");
  node.hidden = !notice;
  if (!notice) {
    shownTone = "";
    return;
  }
  const detail = el("span", undefined, "sub");
  detail.append(...notice.detail.map((part) => (typeof part === "string" ? document.createTextNode(part) : el("b", part.amount))));
  node.replaceChildren(el("span", notice.headline, "headline"), detail);
  node.className = `notice ${notice.tone}`;
  if (notice.tone !== shownTone) {
    shownTone = notice.tone;
    node.style.animation = "none";
    void node.offsetWidth; // restart the entrance
    node.style.animation = "";
  }
}

// Menu text comes from the bucket, so it is placed with textContent, never parsed as HTML.
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function img(src, className, lazy = true) {
  const node = el("img", undefined, className);
  node.src = src;
  node.alt = "";
  if (lazy) node.loading = "lazy";
  return node;
}
