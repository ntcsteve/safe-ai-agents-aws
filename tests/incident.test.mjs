// The phone page's incident states: LIVE, CHANGED, RESTORED, and the OFFLINE notice.
// "Correct" is kiosk/menu.py expected_prices (seed plus the approved promo), so the phone and
// ./kopi check agree on prices. Expected values are literals from scenario/jakarta-kopi.json.
import assert from "node:assert/strict";
import { test } from "node:test";
import { RESTORED_MS, START, State, nextState, noticeFor, orderTotals, wrongPrices } from "../site/incident.js";

const IDR = { code: "IDR", symbol: "Rp ", thousands: ".", decimal: ",", decimals: 0 };
const EXPECTED = {
  kopi: { "kopi-susu": 25000, "kopi-hitam": 18000, "es-kopi-gula-aren": 28000, "teh-tarik": 20000 },
  seduh: { v60: 35000, "long-black": 22000, "kopi-flight": 36000 },
};
const PROMO = { item: "kopi-flight", price: 36000, base_price: 45000, label: "20% off" };

function menus({ kopi = {}, seduh = {} } = {}) {
  const item = (id, name, price, over) => ({ id, name, price: over[id] ?? price });
  return [
    { stall: "kopi", title: "Kopi Corner", currency: IDR, items: [
      item("kopi-susu", "Kopi Susu", 25000, kopi), item("kopi-hitam", "Kopi Hitam", 18000, kopi),
      item("es-kopi-gula-aren", "Es Kopi Gula Aren", 28000, kopi), item("teh-tarik", "Teh Tarik", 20000, kopi)] },
    { stall: "seduh", title: "Kopi Seduh", currency: IDR, items: [
      item("v60", "V60 Pour Over", 35000, seduh), item("long-black", "Long Black", 22000, seduh),
      item("kopi-flight", "Kopi Flight", 36000, seduh)] },
  ];
}
const HEALTHY = menus();
const MNT_142 = menus({ kopi: { "kopi-susu": 2500000, "kopi-hitam": 1800000, "es-kopi-gula-aren": 2800000 } });

// --- Which prices are wrong ---

test("healthy menus: nothing wrong", () => assert.deepEqual(wrongPrices(HEALTHY, EXPECTED), []));

test("MNT-142: the three faulted items, each with its expected price", () => {
  assert.deepEqual(
    wrongPrices(MNT_142, EXPECTED).map((w) => [w.id, w.price, w.expected]),
    [["kopi-susu", 2500000, 25000], ["kopi-hitam", 1800000, 18000], ["es-kopi-gula-aren", 2800000, 28000]],
  );
});

test("promo price is correct; the promo item at its base price is wrong (PROMO-7 not applied)", () => {
  assert.deepEqual(wrongPrices(menus({ seduh: { "kopi-flight": 45000 } }), EXPECTED).map((w) => [w.id, w.expected]),
    [["kopi-flight", 36000]]);
});

// --- State transitions ---

test("a break moves LIVE to CHANGED, naming the stall and items", () => {
  const next = nextState(START, wrongPrices(MNT_142, EXPECTED), 1000);
  assert.equal(next.state, State.CHANGED);
  assert.equal(next.restoredAt, 0);
  assert.deepEqual(next.stalls, ["Kopi Corner"]);
  assert.deepEqual(next.items, ["kopi-susu", "kopi-hitam", "es-kopi-gula-aren"]);
});

test("a phone that opens mid-break goes straight to CHANGED", () => {
  assert.equal(nextState(START, wrongPrices(MNT_142, EXPECTED), 0).state, State.CHANGED);
});

test("a phone that opens after the fix stays LIVE (it never saw the break)", () => {
  assert.equal(nextState(START, [], 0).state, State.LIVE);
});

test("CHANGED then all correct is RESTORED, keeping what was restored", () => {
  const changed = nextState(START, wrongPrices(MNT_142, EXPECTED), 1000);
  const restored = nextState(changed, [], 5000);
  assert.equal(restored.state, State.RESTORED);
  assert.equal(restored.restoredAt, 5000);
  assert.deepEqual(restored.stalls, ["Kopi Corner"]);
  assert.deepEqual(restored.items, ["kopi-susu", "kopi-hitam", "es-kopi-gula-aren"]);
});

test("LIVE with nothing wrong stays LIVE, as the shared START state", () => {
  assert.equal(nextState(START, [], 5000), START);
});

test("RESTORED holds for 8 s, then settles to LIVE", () => {
  const restored = nextState(nextState(START, wrongPrices(MNT_142, EXPECTED), 0), [], 10000);
  assert.equal(RESTORED_MS, 8000);
  assert.equal(nextState(restored, [], 10000 + 7999).state, State.RESTORED);
  assert.equal(nextState(restored, [], 10000 + 8000).state, State.LIVE);
});

test("a new break during RESTORED is CHANGED again", () => {
  const restored = nextState(nextState(START, wrongPrices(MNT_142, EXPECTED), 0), [], 1000);
  assert.equal(nextState(restored, wrongPrices(MNT_142, EXPECTED), 2000).state, State.CHANGED);
});

// --- Order totals ---

test("order totals: what the phone charges now, and what it should", () => {
  const order = new Map([["kopi-susu", 1], ["kopi-flight", 1]]);
  assert.deepEqual(orderTotals(MNT_142, order, EXPECTED), { total: 2536000, expectedTotal: 61000, count: 2 });
  assert.deepEqual(orderTotals(HEALTHY, new Map(), EXPECTED), { total: 0, expectedTotal: 0, count: 0 });
});

// --- What the notice says ---

const mnt142 = () => nextState(START, wrongPrices(MNT_142, EXPECTED), 0);

test("LIVE: no notice", () => {
  assert.equal(noticeFor({ incident: START, offline: false, hasMenu: true, totals: { total: 0, count: 0 }, currency: IDR, promo: PROMO, menus: HEALTHY }), null);
});

test("CHANGED with a broken item in the order: the new total, and hold that order", () => {
  const notice = noticeFor({ incident: mnt142(), offline: false, hasMenu: true, totals: { total: 2536000, expectedTotal: 61000, count: 2 }, currency: IDR, promo: PROMO, menus: MNT_142 });
  assert.deepEqual(notice, { tone: "changed", headline: "3 prices changed at Kopi Corner",
    detail: ["Your total is now ", { amount: "Rp 2.536.000" }, ". Hold that order."] });
});

test("CHANGED with an order the break does not touch: say so, not 'hold that order'", () => {
  const notice = noticeFor({ incident: mnt142(), offline: false, hasMenu: true, totals: { total: 36000, expectedTotal: 36000, count: 1 }, currency: IDR, promo: PROMO, menus: MNT_142 });
  assert.deepEqual(notice.detail, ["Your order isn't affected."]);
});

test("CHANGED with no order yet: check before ordering (never 'your total is Rp 0')", () => {
  const notice = noticeFor({ incident: mnt142(), offline: false, hasMenu: true, totals: { total: 0, count: 0 }, currency: IDR, promo: PROMO, menus: MNT_142 });
  assert.deepEqual(notice.detail, ["Check the prices before you order."]);
});

test("CHANGED, one price: singular", () => {
  const one = nextState(START, wrongPrices(menus({ kopi: { "teh-tarik": 2000000 } }), EXPECTED), 0);
  const notice = noticeFor({ incident: one, offline: false, hasMenu: true, totals: { total: 0, count: 0 }, currency: IDR, promo: PROMO, menus: HEALTHY });
  assert.equal(notice.headline, "1 price changed at Kopi Corner");
});

test("CHANGED on both stalls: both named", () => {
  const both = nextState(START, wrongPrices(menus({ kopi: { "teh-tarik": 2000000 }, seduh: { v60: 3500000 } }), EXPECTED), 0);
  const notice = noticeFor({ incident: both, offline: false, hasMenu: true, totals: { total: 0, count: 0 }, currency: IDR, promo: PROMO, menus: HEALTHY });
  assert.equal(notice.headline, "2 prices changed at Kopi Corner and Kopi Seduh");
});

test("RESTORED: the stall is back, and the promo survived", () => {
  const restored = nextState(mnt142(), [], 1000);
  const notice = noticeFor({ incident: restored, offline: false, hasMenu: true, totals: { total: 61000, count: 2 }, currency: IDR, promo: PROMO, menus: HEALTHY });
  assert.deepEqual(notice, { tone: "restored", headline: "Kopi Corner prices restored", detail: ["Kopi Flight is still 20% off."] });
});

test("OFFLINE with a menu on screen: say it may be stale", () => {
  const notice = noticeFor({ incident: mnt142(), offline: true, hasMenu: true, totals: { total: 0, count: 0 }, currency: IDR, promo: PROMO, menus: MNT_142 });
  assert.deepEqual(notice, { tone: "offline", headline: "Showing the last menu", detail: ["Prices may be out of date. Reconnecting…"] });
});

test("OFFLINE before any menu loaded: say it is connecting", () => {
  const notice = noticeFor({ incident: START, offline: true, hasMenu: false, totals: { total: 0, count: 0 }, currency: IDR, promo: PROMO, menus: [] });
  assert.deepEqual(notice, { tone: "offline", headline: "Connecting to the kiosk", detail: ["Retrying every 5 s."] });
});
