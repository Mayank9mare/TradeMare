# WhatsApp alerts: setup

TradeMare sends alerts (new signals, fills, stops, kill switch, missed runs, IPOs, Zerodha login
reminders) through Meta's official **WhatsApp Cloud API**. You receive them on your own WhatsApp.
About 20 minutes, once.

## 1. Create the Meta app and get a sending number
1. Go to **developers.facebook.com**. If the top right shows **Get Started** instead of **My Apps**,
   your Facebook account is not a developer yet: click it, agree, verify a mobile number by SMS,
   confirm your email and pick "Developer".
2. **My Apps → Create App**. Name it (e.g. `TradeMare Alerts`), then on **Use cases** tick
   **Connect with customers through WhatsApp**, pick (or create) a business portfolio, and
   **Create app**. An unverified portfolio is fine for the test number.
3. **Use cases → Customize** on the WhatsApp use case, accept the WhatsApp terms, then open
   **Step 1. Try it out**:
   - Copy the **Phone number ID** (a long number, not the phone number itself). The **Messaging
     account ID** next to it is needed only to create the template through the API (step 2).
   - Under **Recipient → Manage phone number list**, add **your own WhatsApp number** and enter the
     code WhatsApp sends you. The test number can message up to 5 verified numbers.

## 2. Create the alert template
Meta only lets a business start a conversation with an approved template.
1. Open **WhatsApp Manager → Message templates → Create template**.
2. Category **Utility**, name **`trademare_alert`**, language **English (`en`)**.
3. Body (exactly; the variable may not be at the very start or end):
   ```
   TradeMare update: {{1}}. Open your dashboard for details.
   ```
   Sample for `{{1}}`: `IN · 2026-10-29 · LONG HSCL x14 @ 536.25`
4. Submit. Approval usually takes minutes, sometimes up to 24-48 hours (longer for new apps and
   unverified portfolios).

Or create it through the API with your token (step 3) and the Messaging account ID:
```
curl -X POST "https://graph.facebook.com/v21.0/<MESSAGING_ACCOUNT_ID>/message_templates" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"trademare_alert","category":"UTILITY","language":"en","components":[{"type":"BODY",
       "text":"TradeMare update: {{1}}. Open your dashboard for details.",
       "example":{"body_text":[["IN · 2026-10-29 · LONG HSCL x14 @ 536.25"]]}}]}'
```
Until it is approved, alerts fail with `(#132001) Template name does not exist in the translation`.

If you can't get a template approved, set `mode: text` (step 4). Plain text messages only reach
you within 24 hours of your last message to the TradeMare number, so send it "hi" each morning.

## 3. Get a permanent access token
The token from **Generate token** on the Try-it-out page expires within a day (fine for a first
test). Make a lasting one:
1. **business.facebook.com → Settings → Users → System users → Add** (role: Admin).
2. **Assign assets**: your app (*Full control*) **and** your WhatsApp account (*Full control*);
   without the WhatsApp account the token cannot send.
3. **Generate token** for your app, expiry **Never**, permissions `whatsapp_business_messaging`
   and `whatsapp_business_management`.
4. Put it in TradeMare's `.env` (never commit it):
   ```
   WHATSAPP_TOKEN=EAAG...
   ```

## 4. Tell TradeMare where to send
In `config/settings.local.yaml` (your personal settings, not committed):
```yaml
notifications:
  whatsapp:
    enabled: true
    to: ["919812345678"]          # your number with country code, no + or spaces
    phone_number_id: "123456789012345"
    # template: trademare_alert   # default
    # mode: text                  # only if you skip the template (24-hour window)
```

## 5. Test
```
uv run trademare notify test
```
You should get "TradeMare update: test message: your alerts are working. Open your dashboard for
details." Then restart `trademare serve` so the scheduled runs pick up the settings.

## Cost and limits
- India utility template messages cost about **₹0.12 + GST each** (October 2026 rates; check
  Meta's current rate card). A few alerts a day is well under ₹50 a month. Meta may ask you to add
  a payment method to your WhatsApp account.
- Long alerts are split into several messages (each template variable holds about 1,000
  characters) and line breaks become " · ", because Meta does not allow line breaks in variables.
- The test number only messages verified numbers. That is fine for alerts to yourself; register
  your own business number later if you want a permanent sender.
- Replying to alerts (approve/reject by WhatsApp) is not built yet: it needs a public webhook,
  which comes with the always-on server in the automation plan.
