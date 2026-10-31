// The menu page: every menu, read again every few seconds, so a bad change and its fix appear by themselves.
// A price that differs from the expected one is marked, with the expected price struck beside it.
import { formatPrice } from "/site/price.js";

const POLL_MS = 5000;
const statusNode = document.getElementById("status");
const menusNode = document.getElementById("menus");
let config;

start();

async function start() {
  try {
    config = await fetchJson("/site/config.json");
  } catch (error) {
    console.warn(error);
    showStatus("Reconnecting", "offline");
    setTimeout(start, POLL_MS); // no limit, on purpose: one small request, until the stack is up
    return;
  }
  document.title = config.title;
  document.getElementById("title").textContent = config.title;
  poll();
}

// One poll at a time: the next starts after this one settles, so answers can never arrive out of order.
async function poll() {
  try {
    const menus = await Promise.all(config.stalls.map((id) => fetchJson(`/menus/stall-${id}.json`)));
    render(menus);
  } catch (error) {
    console.warn(error);
    showStatus("Reconnecting", "offline"); // keeps the last menu on screen; never goes blank
  }
  setTimeout(poll, POLL_MS);
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function render(menus) {
  const expected = (menu, item) => config.expected_prices[menu.stall]?.[item.id];
  const wrong = menus.flatMap((menu) => menu.items.filter((item) => item.price !== expected(menu, item)));
  showStatus(
    wrong.length ? `${wrong.length} price${wrong.length === 1 ? " is" : "s are"} wrong` : "All prices correct",
    wrong.length ? "wrong" : "ok",
  );
  menusNode.replaceChildren(...menus.map((menu) => renderMenu(menu, expected)));
}

function renderMenu(menu, expected) {
  const section = el("section");
  const list = el("ul");
  for (const item of menu.items) {
    const want = expected(menu, item);
    const row = el("li", undefined, item.price === want ? "" : "wrong");
    row.append(el("span", item.name));
    const prices = el("span");
    if (item.price !== want && want !== undefined) prices.append(el("s", formatPrice(want, menu.currency)), " ");
    prices.append(el("b", formatPrice(item.price, menu.currency)));
    if (config.promo.item === item.id && config.promo.price === item.price) prices.append(" ", el("em", config.promo.label));
    row.append(prices);
    list.append(row);
  }
  section.append(el("h2", menu.title), list);
  return section;
}

function showStatus(text, tone) {
  statusNode.textContent = text;
  statusNode.className = tone;
}

// Menu text comes from the bucket, so it is placed with textContent, never parsed as HTML.
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
