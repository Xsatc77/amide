# Amide user guide

A tour of what Amide does and where to find it. Everything stays on the computer that runs Amide.

A longer illustrated walkthrough, with screenshots of every page, is in [GUIDE.md](GUIDE.md).

> Amide is a record-keeping tool. Nothing in it is medical advice. Read the legal notice on the sign-in page.

## First steps

1. Open Amide (by default `http://localhost:1707`), accept the legal notice and choose **New User**. The first account is the administrator.
2. **Settings** (your initial, top right): turn on two-factor sign-in, set your time zone (Automatic follows your device; every "today" in Amide uses it), and fill in the **body profile** (sex, birth date, height, activity level). The Food and Energy pages use it. **Display** switches everything between US (lb, in, oz, mph) and metric (kg, cm, mL, km/h). **Medicines** lists what you take so peptides commonly flagged with them get red-and-black caution tape.
3. Add what you own in **Inventory**, then build a protocol in **Protocols**.

## Inventory and orders

- **Add item** for a peptide, BAC water or a supply (syringes, alcohol pads, pen parts). Tick **Local seller** for something you pick up in person: it gets no late-shipment alert.
- **New order** records what you bought, from whom, with cost, lot number, expiration date and a lab report (COA). Add tracking details and Amide shows the order in **Orders and tracking** like a parcel.
- When a package arrives press **Check in**. Vial labels open in your browser's print window (name, size, lot, blank boxes for the reconstitution and expiry dates). Settings, Vial labels changes the label size or turns this off, and its **Custom sheet** option takes the measurements from your own label maker's template (any sheet, rectangle or round) with a test sheet to check the alignment.
- Low-stock alerts on the Dashboard can be snoozed for 7 or 10 days, and each one has an **Alert at** box for that item's own level.
- The peptides list has **one row per peptide**, whatever the vial size: the total on hand, the sizes, and the earliest expiry. Click the name (or **View stock**) for every lot across sizes in one table, soonest expiry first. A peptide with only one line opens that line directly. The list is in **use-first order**: expires soonest, then oldest. **Runs out** estimates when an item will be gone at your current doses. **Spending** shows cost per vial, per mg and per dose, and spend by month.
- **Remove an item from an order** that has not shipped yet (Orders page, **Remove from order**) when the vendor cannot send it: shipping is shared by the items left and the tax drops by that item's share.
- **Active Vials** show what is left in each opened vial, pen, pill bottle or bottle of drops; a vial's amount is an estimate from your logged doses, and **update** corrects it from the real vial.
- **Reconstitute** turns a sealed vial into an **Active Vial** with a concentration and a discard-by date. It also uses up the BAC water and supplies it needs, and a dialog lists the dates to write on the label.

## Protocols, doses and the calendar

- **Protocols**: pick one or more goals, then choose peptides, dose, schedule, time of day, route and an optional inventory item. Frequencies: daily, every other day, every N days, specific days, weekly, as needed.
- **Split a dose** (in the protocol builder, on each item): enter a total, a period in days or weeks and a number of injections. Amide divides the dose and sets the days (or one item per time of day for several injections in a day). You choose the numbers; it only does the arithmetic.
- **Titration** ramps a dose up in weekly steps. "Fill the steps from a ramp" builds the steps from a start dose, an increase, weeks per step and a target. **Cycle off** weeks pause an item. Use the same peptide twice for two doses a day.
- **Times of day**: Fasting, Waking, AM, Pre-workout, Post-workout, PM, Before bed, Bedtime, Any.
- The icons on a protocol card print it, show its **alerts** (the bell), total what the course needs, and **shop** it (see below).
- **Alerts** look at the doses and schedule you entered and list anything worth asking a prescriber or pharmacist about: a dose above the library's range or a titration that more than doubles, two peptides of one class together, medicine cautions that matter more at higher doses (list the medicines, and optionally their doses, in Settings, Medicines), and a sedating peptide in a daytime slot. The builder has a **Check for alerts** button. Alerts are informational only, never block saving, and no alert does not mean no interaction.
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
- **Price correction** (administrator: Settings, Admin): every product on the current price lists beside what it is matched to; fix a wrong match in a dropdown, mark a product the library lacks as new, and **Save corrections**. Corrections are remembered for later lists. A size such as "10mg Dual Chamber Cartridge" is recognised and priced apart from plain vials.

## Body and health

- **Body**: weight and tape measurements (any of your own entries, journal days and water entries can be deleted; each asks first) (with a body diagram), water goal (from your latest weight; the Dashboard card keeps tracking without one and has Reset today, and the Nutrition tab lists your last 30 days of water entries), **Food** (diet type, calories and macros, a food log; paste a free USDA key in Settings, USDA food search, to add a live USDA food search and **Scan a barcode** (camera over https, or type the digits)), **Journal** (mood, energy, sleep, side effects including ones you add to your own list; add or edit any past day; trend charts), **Labs** (a line for every marker; results can be 0, negative, <5 or >100; any past date) and **Body photos** (blurred until you reveal them).
- **Weight chart labels**: each day's weight is labeled with what you lost (green) or gained (amber) since the day before, on the Dashboard and the Measurements tab. Several weigh-ins on one day count once.
- **Scale readings**: type in the numbers a body-composition scale reports (Log a measurement), or **Import from a scale (Renpho CSV)** to load a whole export; importing a file twice is safe. Each number can be charted in the Overview Metric list and the latest reading is shown with its change.
- **Water against your goal** (Food tab): the last 14 days, each with that day's goal, its share and whether it was compliant. **90% or more** of the day's goal counts as compliant; today is not counted until it ends. The Dashboard's Water Goal fill is blue below half, yellow from half and green from 90%.
- **Macro rings**: a donut of the protein, carbs, fat and fiber you logged (with calories in the middle) on the Food tab, on each Journal day, and in the Overview Metric list as **Nutrition**. A pill's **Fiber in one dose** adds to the day's fiber.
- **Journal photos**: **Add a photo** at the top of the Journal tab or on a day's row (several at once, optional caption). Private, stripped of location and camera data, and included in backups. Any logged workout on a journal day has an **Edit** button.
- **Body-photo reminders**: two weeks after a body photo, and at every protocol's start and end, a **BODY PHOTO** line appears on the Dashboard and a camera on the Calendar (and a 9:00 AM ntfy message if you turned push on).
- **Workouts** and **Fitness Test**: plans from a PDF or built by hand, one active at a time (End plan or Delete when you are done), calorie estimates, an Energy page (TDEE that follows your weight) and a Progress page. **Log a free workout** records one that is not in a plan. **Export log** saves a spreadsheet.

## Library

The **Library** holds reference cards for peptides, and a new install already has 107 of the commonly used ones (price estimates are not included). Open one to see dosing tiers, cycles, stacking notes and what to monitor. Cards follow one template and say **Not established** where a value could not be verified; a search also matches a card's other names (Follistatin, FS344 and FST-344 find the same card). The administrator can delete an entry a person added (not a shipped card, and not one a protocol or dose log still uses). **My notes and saved articles** are private to you; **Library, My notes** lists them all. **Library, Goal stacks** reorders each goal's suggested peptides. File vitamins and prescriptions under **Vitamins and Supplements** or **Prescriptions**.

## Learn

The account menu has **Learn**: a tutorial for newcomers to gray-market peptides (risks, scams and customs, vetting vendors, reading a lab report, reconstituting, what to do if you react) with a glossary. Hover or tap an underlined word for its definition. It uses made-up numbers and is not medical advice; the page shows when it was last updated and what still needs expert review.

## Links

**Links** keeps the sites you use, grouped by type and sorted by name. **+ Add** takes a site name, URL, type and an optional description; leave the description blank and Amide reads a short one from the site (once, with a plain request; your administrator can turn this off). Every link has **Edit** and **Delete**.

## Your data

- **Dashboard** pulls the day together: schedule, alerts, compliance bars over a window you choose, water, cost and shipments.
- **Settings, Backup and restore** makes an encrypted backup you can restore on another computer. **Sharing** gives chosen people a read-only view.
- Install Amide on a phone or computer from the browser's menu (**Add to Home Screen** or **Install**). It needs HTTPS or `localhost`: see [DEPLOYING.md](DEPLOYING.md).
