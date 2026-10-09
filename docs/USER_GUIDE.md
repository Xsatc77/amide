# Amide user guide

A tour of what Amide does and where to find it. Everything stays on the computer that runs Amide.

A longer illustrated walkthrough, with screenshots of every page, is in [GUIDE.md](GUIDE.md).

> Amide is a record-keeping tool. Nothing in it is medical advice. Read the legal notice on the sign-in page.

## First steps

1. Open Amide (by default `http://localhost:1707`), accept the legal notice and choose **New User**. The first account is the administrator.
2. **Settings** (your initial, top right): turn on two-factor sign-in, set your time zone, and fill in the **body profile** (sex, birth date, height, activity level). The Food and Energy pages use it. **Display** switches everything between US (lb, in, oz, mph) and metric (kg, cm, mL, km/h). **Medicines** lists what you take so peptides commonly flagged with them get red-and-black caution tape.
3. Add what you own in **Inventory**, then build a protocol in **Protocols**.

## Inventory and orders

- **Add item** for a peptide, BAC water or a supply (syringes, alcohol pads, pen parts). Tick **Local seller** for something you pick up in person: it gets no late-shipment alert.
- **New order** records what you bought, from whom, with cost, lot number, expiration date and a lab report (COA). Add tracking details and Amide shows the order in **Orders and tracking** like a parcel.
- When a package arrives press **Check in**. Vial labels open in your browser's print window (name, size, lot, blank boxes for the reconstitution and expiry dates). Settings, Vial labels changes the label size or turns this off.
- The peptides list is in **use-first order**: expires soonest, then oldest. **Runs out** estimates when an item will be gone at your current doses. **Spending** shows cost per vial, per mg and per dose, and spend by month.
- **Reconstitute** turns a sealed vial into an **Active Vial** with a concentration and a discard-by date. It also uses up the BAC water and supplies it needs, and a dialog lists the dates to write on the label.

## Protocols, doses and the calendar

- **Protocols**: pick one or more goals, then choose peptides, dose, schedule, time of day, route and an optional inventory item. Frequencies: daily, every other day, every N days, specific days, weekly, as needed.
- **Titration** ramps a dose up in weekly steps. "Fill the steps from a ramp" builds the steps from a start dose, an increase, weeks per step and a target. **Cycle off** weeks pause an item. Use the same peptide twice for two doses a day.
- **Times of day**: Fasting, Waking, AM, Pre-workout, Post-workout, PM, Before bed, Bedtime, Any.
- The icons on a protocol card print it, total what the course needs, and **shop** it (see below).
- **Today** lists what is due, in day order. Press **Log** (pick the injection site) or **Skip**. Missed doses can be caught up from the protocol page.
- **Calendar** shows month, week and day. Click a dose for details; one due today and not yet logged has **Pick site and log dose**.
- **Subscribe to your calendar** (Settings): a private address for Apple, Google or Outlook calendars, with an alert at each dose's time of day.
- **Dose reminders** (Settings): ntfy push messages. They are off by default and are the one thing Amide sends to the outside on its own.

## The calculator

**Calculator** works out concentration, how much to draw and where that falls on a syringe, from a vial, the water you add and the dose. It handles IU vials (HGH) and shows its working. Pick a BAC water item and an inventory item, or practise with any library peptide. A library dosing tier with a plain dose has **Open in calculator**.

## Vendors, prices and shopping

- **Vendors** keeps contacts, payment methods and price lists. Upload a price list (PDF, photo or spreadsheet) on a vendor to read its prices; **Price History** charts them.
- **Shop this protocol** (cart icon on a protocol): the cheapest plan to buy the whole course from the vendors' newest lists, with one or two orders. Shipping fees are editable. BAC water is added from the brands you rank in Inventory. **Share this plan** saves a text file or opens an email.
- An optional watcher program can read price lists posted in chat groups: see [watcher/README.md](../watcher/README.md).

## Body and health

- **Body**: weight and tape measurements (with a body diagram), water goal, **Food** (diet type, calories and macros, a food log; paste a free USDA key in Settings, USDA food search, to add a live USDA food search), **Journal** (mood, energy, sleep, side effects including ones you add to your own list; add or edit any past day; trend charts), **Labs** (a line for every marker; results can be 0, negative, <5 or >100; any past date) and **Body photos** (blurred until you reveal them).
- **Workouts** and **Fitness Test**: plans from a PDF or built by hand, one active at a time (End plan or Delete when you are done), calorie estimates, an Energy page (TDEE that follows your weight) and a Progress page. **Log a free workout** records one that is not in a plan. **Export log** saves a spreadsheet.

## Library

The **Library** holds reference cards for peptides. Open one to see dosing tiers, price ranges and cycles. **My notes and saved articles** are private to you; **Library, My notes** lists them all. **Library, Goal stacks** reorders each goal's suggested peptides. File vitamins and prescriptions under **Vitamins and Supplements** or **Prescriptions**.

## Links

**Links** keeps the sites you use, grouped by type and sorted by name. **+ Add** takes a site name, URL, type and an optional description; leave the description blank and Amide reads a short one from the site (once, with a plain request; your administrator can turn this off). Every link has **Edit** and **Delete**.

## Your data

- **Dashboard** pulls the day together: schedule, alerts, compliance bars over a window you choose, water, cost and shipments.
- **Settings, Backup and restore** makes an encrypted backup you can restore on another computer. **Sharing** gives chosen people a read-only view.
- Install Amide on a phone or computer from the browser's menu (**Add to Home Screen** or **Install**). It needs HTTPS or `localhost`: see [DEPLOYING.md](DEPLOYING.md).
