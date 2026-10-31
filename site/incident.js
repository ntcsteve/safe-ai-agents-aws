// What the phone says about the menu: LIVE (all prices correct), CHANGED (some are wrong),
// RESTORED (they were wrong and are correct again; shown for 8 s), plus the OFFLINE notice.
// Pure: no DOM and no fetch, so tests/incident.test.mjs covers every state.
// "Correct" is the scenario's expected prices (seed plus the approved promo), the same rule as
// kiosk/menu.py expected_prices, so the phones and ./kopi check agree on prices. Deviation:
// check_menus also flags a missing or extra item; the page judges prices only, because every
// change in this demo (MNT-142, PROMO-7, a rollback) changes prices, never which items exist.
import { formatPrice } from "./price.js"; // relative, so the browser and node --test both resolve it

export const RESTORED_MS = 8000;

export const State = Object.freeze({ LIVE: "LIVE", CHANGED: "CHANGED", RESTORED: "RESTORED" });

// A page that has seen nothing wrong yet. incident: { state, restoredAt, stalls, items }.
export const START = Object.freeze({ state: State.LIVE, restoredAt: 0, stalls: [], items: [] });

// Items whose price differs from the expected price, in menu order. An item the scenario does
// not know has no expected price, so the page cannot judge it and leaves it out.
export function wrongPrices(menus, expectedPrices) {
  const wrong = [];
  for (const menu of menus) {
    for (const item of menu.items) {
      const expected = expectedPrices[menu.stall]?.[item.id];
      if (expected !== undefined && item.price !== expected) {
        wrong.push({ stall: menu.title, id: item.id, name: item.name, price: item.price, expected });
      }
    }
  }
  return wrong;
}

// The state after a fetch. stalls and items name what was wrong at the last CHANGED, so RESTORED
// can say what came back.
export function nextState(incident, wrong, now) {
  if (wrong.length) {
    return { state: State.CHANGED, restoredAt: 0, stalls: [...new Set(wrong.map((w) => w.stall))], items: wrong.map((w) => w.id) };
  }
  if (incident.state === State.CHANGED) return { ...incident, state: State.RESTORED, restoredAt: now };
  if (incident.state === State.RESTORED && now - incident.restoredAt < RESTORED_MS) return incident;
  return START;
}

// What the order costs at today's prices, and what it should cost at the expected prices.
export function orderTotals(menus, order, expectedPrices) {
  let total = 0;
  let expectedTotal = 0;
  let count = 0;
  for (const menu of menus) {
    for (const item of menu.items) {
      const quantity = order.get(item.id) ?? 0;
      count += quantity;
      total += quantity * item.price;
      expectedTotal += quantity * (expectedPrices[menu.stall]?.[item.id] ?? item.price);
    }
  }
  return { total, expectedTotal, count };
}

// The notice, or null when there is nothing to say (LIVE and connected). detail is text parts;
// { amount } marks a price for emphasis. Offline wins: the menu on screen may be stale.
export function noticeFor({ incident, offline, hasMenu, totals, currency, promo, menus }) {
  if (offline) {
    return hasMenu
      ? { tone: "offline", headline: "Showing the last menu", detail: ["Prices may be out of date. Reconnecting…"] }
      : { tone: "offline", headline: "Connecting to the kiosk", detail: ["Retrying every 5 s."] };
  }
  const stalls = incident.stalls.join(" and ");
  switch (incident.state) {
    case State.CHANGED: {
      const count = incident.items.length;
      return {
        tone: "changed",
        headline: `${count} price${count === 1 ? "" : "s"} changed at ${stalls}`,
        detail: changedDetail(totals, currency),
      };
    }
    case State.RESTORED: {
      // RESTORED means every price is as expected, the promo included: say the valid change survived.
      const promoName = menus.flatMap((m) => m.items).find((i) => i.id === promo?.item)?.name;
      return { tone: "restored", headline: `${stalls} prices restored`, detail: promoName ? [`${promoName} is still ${promo.label}.`] : [] };
    }
    case State.LIVE:
      return null;
    default:
      throw new Error(`unknown incident state ${incident.state}`);
  }
}

// What the break means for this phone's own order. "Your total is Rp 0" would be nonsense, and
// "hold that order" would be false for an order the break does not touch.
function changedDetail(totals, currency) {
  if (!totals.count) return ["Check the prices before you order."];
  if (totals.total === totals.expectedTotal) return ["Your order isn't affected."];
  return ["Your total is now ", { amount: formatPrice(totals.total, currency) }, ". Hold that order."];
}
