# Amide: the complete guide

Amide is a self-hosted tracker for peptides and everything around them: what you own, what you ordered, what you take, how your body responds. It runs on your own computer and your data never leaves it unless you turn on one of a few optional connections (listed at the end).

> **Amide is a record-keeping tool. Nothing in it is medical advice.** The examples, cautions and calculators are prompts to ask a prescriber or pharmacist, not instructions.

**About the pictures.** Every screenshot here was taken from a throwaway demo account with invented data: a made-up person named "Demo", an invented vendor called "Acme Labs", and made-up weigh-ins, orders and prices. Nothing in them is a real person, vendor or price.

**Contents**

1. [Getting started](#1-getting-started)
2. [The Dashboard](#2-the-dashboard)
3. [Inventory and orders](#3-inventory-and-orders)
4. [Vendors and price lists](#4-vendors-and-price-lists)
5. [Protocols](#5-protocols)
6. [Today and the Calendar](#6-today-and-the-calendar)
7. [The Calculator](#7-the-calculator)
8. [Body: weight, journal, labs and photos](#8-body-weight-journal-labs-and-photos)
9. [Nutrition](#9-nutrition)
10. [Workouts and the Fitness Test](#10-workouts-and-the-fitness-test)
11. [The Library](#11-the-library)
12. [Settings](#12-settings)
13. [Backup, restore and your privacy](#13-backup-restore-and-your-privacy)
14. [Quick answers](#14-quick-answers)

---

## 1. Getting started

### Open Amide and accept the notice

Open Amide in a browser (by default `http://localhost:1707`). The first page is a legal notice. Tick **I understand the safety statement above** and press **Okay**. You only do this once per browser sign-in.

![The legal notice](guide/img/01-legal-notice.png)

### Create your account or sign in

The first account you create is the **administrator**: it can add other people, reset their passwords and remove them. Everyone else gets their own private account. Passwords must be at least 8 characters (your administrator can change that rule).

![The sign-in page](guide/img/02-sign-in.png)

### First steps, in this order

1. **Settings** (your name, top right). Set your **time zone**, fill in the **body profile** (sex, birth date, height, activity level) so the Food and Energy pages can work, and turn on **two-factor sign-in** if you like.
2. Add what you own in **Inventory** ([section 3](#3-inventory-and-orders)).
3. Build a protocol in **Protocols** ([section 5](#5-protocols)).
4. Each day, open **Today** and press **Log** as you take each dose.

### The top menu

Dashboard · Today · Protocols · Calendar · Body · Workouts · Nutrition · Inventory · Vendors · Library · Calculator. A greyed **Links** is reserved for a future update (marked Coming soon). Your name, top right, opens Settings, Backup and sign out.

### Install it like an app

On a phone or computer, use the browser's menu (**Install** or **Add to Home Screen**). It needs `localhost` or HTTPS. See [DEPLOYING.md](DEPLOYING.md).

---

## 2. The Dashboard

The Dashboard pulls the day together on one page.

![The Dashboard](guide/img/03-dashboard.png)

| Card | What it shows |
|---|---|
| **Today's Schedule** | Doses due today, in time-of-day order, with a **Due** badge. |
| **Workouts This Week** | A Monday-to-Sunday strip (done, missed, upcoming, rest), a progress bar and what is next. |
| **Body** | Your latest tape measurements on a body diagram. Green means moving the way you want (smaller waist and hips, bigger limbs). Click a dot for the previous value. |
| **Alerts** | Expiring items, low stock, late shipments, vials that will run out soon, and new price lists. |
| **Compliance** | Bars for Protocol, water (H2O), Diet and Workout over the window you pick from the drop-down. |
| **Water Goal** | Your daily goal (from your weight), what you have logged, and **+ Log water**. |
| **Cost Snapshot** | What your active protocols cost. |
| **Journal** | A quick note box. |
| **Weight** | A chart with value lines. |
| **Items In Shipment** | Orders on the way, with tracking and a **Check in** button for when they arrive. |

If someone shares their data with you (Settings, Sharing) you can view their Dashboard from the menu that appears.

---

## 3. Inventory and orders

### The inventory list

![The inventory list](guide/img/18-inventory.png)

- **Add item** records a peptide, BAC water or a supply (syringes, alcohol pads and so on). Tick **Local seller** for something you pick up in person: it will not raise a late-shipment alert.
- The peptide list is in **use-first order**: what expires soonest, then what arrived earliest. Empty shelves go last. Switch to alphabetical with the sort box.
- **Runs out** tells you when an item will be gone at your current doses, and the Dashboard warns you ahead of time.
- **Delete** removes an item and its order lines. Amide will not delete an item that is still on an order that has not arrived: check that order in first.

### An item's page

![An inventory item](guide/img/19-inventory-item.png)

An item's page shows its orders (quantity, received, dates, tracking, lot, cost, lab report), sales, and the buttons for **Check in**, **Print labels** and **Reconstitute**.

### Orders and tracking

Record a purchase with **New order**: who it is from, cost, tax, shipping, lot number, expiration date and a lab report (COA). Add a tracking number and Amide shows the order like a parcel on the **Orders and tracking** page.

![Orders and tracking](guide/img/20-orders.png)

### When a package arrives: Check in

Press **Check in** on the order. Enter the arrival date and how many vials actually arrived (with a note if some were short). Amide then opens your vial labels in the browser's print window (name, size, lot, boxes for the reconstitution and expiry dates). Settings, **Vial labels** changes the label size or turns this off.

### Reconstitute

**Reconstitute** turns a sealed vial into an **Active Vial** with a concentration and a discard-by date. It also uses up the BAC water and supplies it needs, and a dialog lists the dates to write on the label.

### Spending

**Spending** shows cost per vial, per mg and per dose for each peptide, and spend by month.

![Spending](guide/img/21-spending.png)

---

## 4. Vendors and price lists

**Vendors** keeps contacts, payment methods and price lists for each seller you use.

![Vendors](guide/img/22-vendors.png)

![A vendor page](guide/img/23-vendor-detail.png)

- Upload a **price list** (PDF, photo or spreadsheet) on a vendor and Amide reads its prices and matches each line to a library card.
- **Price History** charts how a product's price moved.
- The page shows each vendor's average time to deliver, calculated from your own orders.
- An optional watcher program can read price lists posted in chat groups: see [../watcher/README.md](../watcher/README.md).
- A new price list raises an alert on the Dashboard, and a line it could not match to a library card is flagged so you can add the card.

---

## 5. Protocols

A protocol is a plan: which peptides, what dose, how often, at what time of day, by which route, for how long.

![The Protocols page](guide/img/05-protocols.png)

### Build one

Pick one or more goals and press **Build protocol**, or open one of the **Examples From The Peptide Community** at the bottom of the page. The examples exist to show how the builder works; each opens as an example only, and nothing is saved until you press save.

![The protocol builder](guide/img/06-protocol-builder.png)

For each peptide you set:

- **Dose and unit** (mg, mcg or IU) and **frequency** (daily, every other day, every N days, specific days, weekly, as needed). Use the same peptide twice for two doses a day.
- **Time of day**: Fasting, Waking, AM, Pre-workout, Post-workout, PM, Before bed, Bedtime or Any.
- **Route** and, optionally, which **inventory item** you will take it from.
- **Titration**: tick it to ramp a dose up in steps by week. "Fill the steps from a ramp" builds them from a start dose, an increase, weeks per step and a target.
- **Cycle off** weeks that pause an item.

### Caution tape

If you list your medicines in Settings ([section 12](#12-settings)), a peptide that is commonly flagged with one of them gets **red and black caution tape** in the library, on your protocol cards and in the builder, with the reason listed. It is a prompt to ask your prescriber or pharmacist. No tape does not mean no interaction.

### Shop this protocol

The cart icon on a protocol card opens a shopping plan: the cheapest way to buy the whole course from your vendors' newest price lists, in one or two orders.

![The shopping plan](guide/img/07-protocol-shop.png)

- Shipping fees are editable and can be saved as your defaults.
- BAC water is added only from the brands you rank in Inventory. Oil-based products (sold by strength such as 250 mg/mL) need no BAC water; for those Amide asks how many mL are in a vial and how many vials come in a box each time.
- **Share this plan** saves a text file or opens an email.
- Other icons on the card print the protocol and total what the course needs.

### Pause, end, repeat

**Pause** and **Resume** stop doses from being due. **End** finishes it today and keeps it under **Saved protocols**, where you can **Repeat** it as a new one.

---

## 6. Today and the Calendar

### Today

**Today** lists what is due, in day order. Press **Log** (and pick the injection site on the body diagram) or **Skip**. Missed doses can be caught up from the protocol page.

![Today](guide/img/04-today.png)

### Calendar

The **Calendar** shows month, week and day. Click a dose for details; one that is due today and not yet logged has **Pick site and log dose**. Days with a workout done or a Fitness Test completed are marked.

![The Calendar](guide/img/08-calendar.png)

- **Subscribe to your calendar** (Settings) gives a private address for Apple, Google or Outlook calendars, with an alert at each dose's time of day.
- **Dose reminders** (Settings) send push messages through ntfy. They are off by default.

---

## 7. The Calculator

The **Calculator** works out concentration, how much to draw and where that falls on the syringe, from the vial, the water you add and the dose. It handles IU vials (such as HGH), shows its working, and draws the syringe so you can see the fill line.

![The Calculator](guide/img/28-calculator.png)

Pick a BAC water item and an inventory item, or practise with any library peptide. A library dosing tier with a plain dose has **Open in calculator**.

---

## 8. Body: weight, journal, labs and photos

The **Body** page has tabs: **Measurements**, **Food** (this is the Nutrition menu item), **Journal**, **Labs**.

### Measurements

![Body measurements](guide/img/09-body.png)

Log weight, heart rate, blood pressure and tape measurements. Every chart has value lines on its axis so you can read it at a glance. The **Overview** chart is driven by a drop-down of every measurement plus BMI and body fat %.

![Logging an entry](guide/img/10-body-entry.png)

Type each measurement on the body diagram where it belongs. Switch between US and metric units in Settings, Display.

**Body photos** are blurred until you reveal them and can require a second sign-in step.

### Journal

Daily mood, energy and sleep, side effects (including ones you add to your own list) and notes. Add or edit any past day. Trend charts show the pattern.

![The journal](guide/img/11-journal.png)

### Labs

A line for every marker, with a reference band when you enter one. Results can be 0, negative, <5 or >100. Attach the lab report if you have it.

![Labs](guide/img/12-labs.png)

---

## 9. Nutrition

**Nutrition** has your diet type, calorie and macro targets from your body profile, and a food log by meal. Add your own foods; if your administrator set a USDA key you can also search the USDA database live.

![Nutrition](guide/img/13-nutrition.png)

---

## 10. Workouts and the Fitness Test

### Plans

Build a plan by hand, pick one with **Find a Workout**, or import one from a PDF. Each workout day has its weekday boxes right inside it. One plan is active at a time; **End plan** or **Delete** when you are done. **Log a free workout** records one that is not in a plan, and **Export log** saves a spreadsheet.

![Workout plans](guide/img/14-workouts.png)

Logging a workout estimates calories from the exercise, your weight and the sets and reps. If the exercise name is not an exact match, Amide uses the closest exercise in its database (you can confirm or change the match).

### Energy

Your daily energy use (TDEE) follows your weight, with the workout burn stacked on top and a chart against your target.

![Energy](guide/img/15-workout-energy.png)

### Progress

Top load and volume per exercise, weekly volume by body area, and where your burn comes from.

![Progress](guide/img/16-workout-progress.png)

### Fitness Test

A tab inside Workouts. Log max push-ups, sit-ups and a plank hold, and watch each trend. Amide suggests a retest after 28 days.

![Fitness Test](guide/img/17-fitness-test.png)

---

## 11. The Library

The **Library** holds reference cards for peptides.

![The library](guide/img/24-library.png)

Open a card to see dosing tiers, cycles, price ranges and what is on your price lists.

![A library card](guide/img/25-library-card.png)

- **My notes and saved articles** are private to you.
- **Goal stacks** lets you reorder each goal's suggested peptides.
- File vitamins and prescriptions under **Vitamins and Supplements** or **Prescriptions**.
- With a medicine listed in Settings, a flagged peptide shows caution tape and a note:

![A caution on a library card](guide/img/26-library-caution.png)

![Goal stacks](guide/img/27-goal-stacks.png)

---

## 12. Settings

![Settings](guide/img/29-settings.png)

| Section | What you can do |
|---|---|
| **User** | Change your username and password; two-factor sign-in and a separate code for body photos; time zone; email; the default discard window (28 days after reconstituting); dashboard alert defaults; the body profile; shopping defaults; vial label size; the calendar feed; dose reminders. |
| **Medicines** | List the medicines you take so flagged peptides get caution tape. |
| **Display** | Colorway (including light and dark) and **Units**: US (lb, in, oz, mph) or metric (kg, cm, mL, km/h). Your data is stored once and converted when shown. |
| **Sharing** | Give chosen people a read-only view of a category of your data. |
| **Integrations** | Health apps are marked Coming Soon. |
| **Admin** (administrator only) | Add people, reset a password, remove two-factor, delete a user. |

---

## 13. Backup, restore and your privacy

### Backup and restore

Open **Backup** from your name menu. It has three tabs: **Back up**, **Export / Share** and **Restore / Import**. A backup is an encrypted file you can restore on another computer: choose **My data** or, as the administrator, the **whole installation**, tick the sections to include, and set a passphrase of at least 8 characters. There is no way to recover a lost passphrase, so keep it somewhere safe. **Export / Share** makes a smaller file to send someone part of your data. You can also schedule encrypted backups (set a passphrase in the server settings).

![Backup](guide/img/30-backup.png)

### Where your data lives

Everything is stored in one folder (`AMIDE_DATA_DIR`), so backing up that one folder backs up all of it. Nothing leaves your computer on its own. The only optional outside connections are:

- **ntfy** push reminders (off by default).
- **USDA** live food search (off unless a key is set).
- **Scheduled backups** (to a folder you choose).
- **Price-list watcher** (a separate program you run).
- The **calendar feed**, a private address you choose to share.

The Privacy Policy page in the footer lists these.

---

## 14. Quick answers

**I mistyped a weight.** Body, Measurements: the list of recent entries lets you add a corrected one for the same day.

**A peptide is missing from the library.** Type its name in the protocol builder and it is created for you ("New to library"). You can add a card later.

**The shopping plan says a product is not available.** The newest price list from each vendor is used. A line must be matched to a library card and priced in a size Amide can read. Oils sold by strength ask for the vial size.

**I deleted something by accident.** Restore from a backup (section 13). Deleting an item removes its order lines.

**I want metric.** Settings, Display, Units.

**Where is Fitness Test?** Workouts, fourth tab.

Amide, copyright 2026, for research and informational purposes only.
