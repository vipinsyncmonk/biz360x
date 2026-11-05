# Designs

These are the 26 Biz360X screens, exported from the design canvas (`screens/canvas.json` lists their positions and titles). Each `.dc.html` file is one artboard. It uses the canvas runtime (`support.js`), so it won't render on its own, but the markup, copy, sample data and interaction state in each file's `renderVals()` are the reference for building the real frontend.

**Look:** paper ground `#F4F3EE`, ink `#14161A`, dark sidebar `#111318`, cobalt accent `#2846E0` for actions, and amber `#9A4508` / `#FDF0E1` only for "needs action". Type is Bricolage Grotesque for display, Geist for body and Geist Mono for numbers.

| Row | Screens |
|---|---|
| Marketing & access | Main (landing), Login, Onboarding |
| Workspace | Dashboard, Compliance, Startup, MSME, Copilot |
| Module map & mobile | Modules, MobileToday, MobileCompliance, MobileCopilot, MobileMore |
| Run & govern | Finance, Payroll, GST, Legal, Documents |
| Admin | Group (CA-firm view), Settings |
| Sales & billing | Billing, InvoiceEditor, CustomerPay, Quotes, CreditNote |
| Data model | DataModel |

The live canvas on claude.ai is the source of truth. Re-export after design changes.
