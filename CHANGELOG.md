# Changelog

Newest first. Dates are when the work was done.

## v1.0 (2026-10)

- Shop this protocol: the cheapest one- or two-order plan from the price lists, editable shipping, BAC water from the brands you rank, text file and email sharing.
- Price-list ingest: engine, Telegram watcher, forum topics, skip words, inbox; image and spreadsheet price lists; photos group by their message dates.
- Orders tab with parcel-style tracking; reconstitution supplies and BAC priorities; IU vials and a teaching mode in the calculator.
- Dashboard compliance bars with a time-window dropdown; a larger body diagram.
- Food tracking, workout calories and TDEE, body photos, encrypted backup and restore.
- Vial labels print on check-in, with a "put these dates on your label" dialog after reconstituting.
- Inventory: use-first order, Local seller flag, runs-out predictions and alerts, a Spending page, average time to arrive on each vendor.
- Protocol alerts: a bell beside Print on each protocol and a Check for alerts button in the builder (dose against the library range, titration jumps, peptide against peptide, medicine cautions that grow with the dose, sedating peptides in daytime slots); medicines can carry a dose.
- Protocols: nine times of day, a titration ramp helper, a printable view, Vitamins and Prescriptions cards, the titration step on the Dashboard.
- Calendar: log from the calendar, a private iCal subscription, ntfy reminders (opt-in), an installable app.
- Library: private notes and saved articles, reorderable goal stacks, calculator links on dosing tiers, sheets join differently named cards.
- Labs: a line per marker, results like <5 and >100; Journal: any past day, trend charts; Workouts: end or delete a plan, export the log, weight-aware TDEE.
- The shopping plan counts BAC bottles by the 28-day rule.
- Your own side effects in the journal, free-form workouts, live USDA food search (opt-in), and scheduled encrypted backups (opt-in).
- Metric units: choose US or metric in Settings (weight, tape measurements, height, water, speed and loads); your data is stored once and converted when shown or typed.
- Medicines list in Settings, with red-and-black caution tape on peptides commonly flagged with them; reference lines on every chart; examples from the peptide community in the protocol builder.
- A Links page: save sites by type (Vendor, Supplies, Community, Research, Blog/Vlog, Calculator, Workouts, Nutrition, Other), sorted by name, with Edit and Delete; a link saved without a description gets a short one read from its site (`AMIDE_LINK_DESCRIPTIONS=0` turns that off).
- Minimum password length raised to 8 (set `AMIDE_PASSWORD_MIN_LENGTH` to change it).
- GitHub Actions: tests on every push, Docker image published on version tags.

## Earlier releases

**v1.0 — Active Vials.** Reconstituting a Lyophilized inventory item (from the Inventory page or the Calculator) creates a tracked Active Vial — concentration, total content, discard-by date, and doses per vial, shown as a card with the real vial icon. Expired vials flag themselves for a one-time-per-24-hours discard prompt; discarding never deletes the record, just retires it. A default discard window (in days) is configurable in Settings.

**v0.9 — Sharing.** Per-person sharing, off by default: grant specific users a read-only view of your Inventory, your Protocols (personal data), or both, from the new Sharing section in Settings. Shared inventory merges into the viewer's own list, tagged by owner; shared protocols show in a separate "Shared with me" tab, never mixed into your own view or Calendar. Vendors are now a shared list across all users (like the peptide library) — what you bought from one stays private unless you share your inventory.

**v0.8 — Settings.** A Settings page (from the username menu) for changing your username/password, turning two-factor authentication on or off, setting an email (for future sharing features) and timezone, and picking a colorway — Light, Dark, or one of seven named palettes, including a pure-black-and-white High Contrast option. Admins get a Manage Users panel: add accounts, reset passwords, remove someone's two-factor authentication, or delete an account (which also removes everything they own, after two confirmations).

**v0.7 — Inventory Foundations.** The inventory form adapts to the medium: amount + unit (mg/mcg/IU), volume for Liquid, units-per-package for Autoinjector/Pill, plus expiration date and storage location, each required only where it makes sense. Vendors are now their own table (type a name to reuse or create it, like the peptide picker). The inventory list has search, medium filters, and sortable columns. Backup & restore (JSON + CSV export, additive-only import) is on the account menu.

**v0.6 — Reconstitution Calculator.** Live vial + BAC water + dose → concentration, draw volume and U-100 syringe units, with a syringe-fill visual, presets, and a reverse solver (pick the units you want, it works out the water). Pre-fills from Inventory (lyophilized items) or a Protocol's dose. *Not yet built:* turning a mixed vial into a tracked "Active Vial" that depletes inventory and has a discard-by date — that's still open, see the roadmap.

**v0.5 — Calendar.** Month, week and day views of every dose your active and scheduled protocols call for (titration steps included). Click any line, block or card to see exactly what's due.

**v0.4 — Accounts & legal notice.** Every visit after 10 minutes away starts with the legal notice, then New User / Login. Each user's inventory and protocols are private (the library is shared). Optional two-factor authentication with an authenticator app. See *Accounts* below.

**v0.3 — Peptide Library.** A searchable Library of your peptide cards (details as text plus the original card image), with your own dose range, frequency, aliases, notes and goal stacks per peptide. The protocol builder searches the whole library as you type. See *Importing your peptide cards* below.

**v0.2 — Protocols.** Build protocols from goals (suggested peptide stacks), set your own dose and schedule per peptide, optional titration steps, and see active protocols as cards; saved protocols can be paused, ended, repeated, or deleted.

**v0.1 — Inventory.** Add, edit, and delete inventory items (name, count, vial size, medium, lot/batch #, cost, vendor, order/shipped/arrival dates, COA photo/PDF with lab-measured vial size and purity, notes). See [docs/ROADMAP.md](docs/ROADMAP.md) for what comes next.
