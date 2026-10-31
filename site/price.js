// Mirror of kiosk/menu.py format_price. Amounts are integers in the currency's smallest
// displayed unit (value x 10^decimals); the currency object comes from the scenario.
export function formatPrice(amount, currency) {
  const scale = 10 ** currency.decimals;
  const whole = Math.floor(amount / scale);
  const fraction = amount % scale;
  let text = String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, currency.thousands);
  if (currency.decimals > 0) {
    text += currency.decimal + String(fraction).padStart(currency.decimals, "0");
  }
  return currency.symbol + text;
}
