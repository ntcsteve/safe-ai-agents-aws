// The page's formatter must print exactly what kiosk/menu.py format_price prints.
// Same cases as tests/test_menu.py FormatPrice, so the two can never drift silently.
import assert from "node:assert/strict";
import { test } from "node:test";
import { formatPrice } from "../site/price.js";

const IDR = { code: "IDR", symbol: "Rp ", thousands: ".", decimal: ",", decimals: 0 };
const MYR = { code: "MYR", symbol: "RM ", thousands: ",", decimal: ".", decimals: 2 };

test("idr", () => assert.equal(formatPrice(25000, IDR), "Rp 25.000"));
test("idr after fault", () => assert.equal(formatPrice(2500000, IDR), "Rp 2.500.000"));
test("two-decimal currency", () => assert.equal(formatPrice(123450, MYR), "RM 1,234.50"));
test("small amount keeps leading zero fraction", () => assert.equal(formatPrice(5, MYR), "RM 0.05"));
