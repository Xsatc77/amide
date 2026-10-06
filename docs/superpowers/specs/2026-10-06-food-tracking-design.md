# Food Tracking (Part A: core) — Design

Status: design approved in conversation 2026-10-06; written spec pending owner review.

## Problem

Amide knows a person's calorie target and macro split (the Macros tab) and what a workout burns (the Energy tab), but
there is nowhere to log what was eaten, so a diet cannot be followed or checked. The owner wants food logging tied to
the chosen diet type, a calorie limit with the potential deficit, a macro pie chart, and macro fulfilment for the day.

## Scope: this is part A of three

- **A. Core (this spec):** foods, the starter list, daily logging, targets, deficit, diet-type picker, pie chart, fulfilment.
- **B. Live online search (later, own spec):** a USDA FoodData Central lookup that imports results into My foods
  (needs a free API key kept in Settings, and internet).
- **C. Meal plans and shot-day guidance (later, own spec):** generated and browsable meal plans built from simple,
  easy foods (explicitly not "fancy" foods), a library of ready-made diet plans in the manner of a workouts repository,
  and eating guidance for GLP-1 / Retatrutide shot days written in original words (the owner's three reference pages
  are inspiration and sources to link, never text to copy). C depends on A's foods and logs.

## Decisions made with the owner

| Question | Decision |
|---|---|
| Where it lives | **Replaces the Macros tab** on Weight & Measurements; the tab becomes **Food** (`/measurements?tab=food`; the old `?tab=macros` address redirects there). |
| Nutrients per food | Calories, protein, carbs, fat and fiber. |
| Food sources | My foods plus a bundled common-foods starter list (this spec); live online search in part B. |
| Calorie accounting | Limit = the goal-adjusted target. Potential deficit = TDEE + logged workout burn - eaten. |

## Out of scope for A

Online search; meal plans and diet-plan library; shot-day guidance; barcodes; recipes and multi-food meals as one
item; micronutrients; photo logging; copying a day to another day; sharing food data with anyone.

## One calorie number

The Macros tab used an older TDEE (Mifflin-St Jeor times an activity multiplier) while the Energy tab uses the module that
reproduces tdeecalculator.org (`app/measurements/tdee.py`, `tdee.report`). The Food tab uses the **Energy tab's TDEE**
everywhere, so the two tabs agree. The goal offset and the safe floor still come from `target_calories(tdee, goal, sex)`.
Consequence the owner should know: the calorie number shown where Macros used to be changes slightly (it now equals the
Energy tab's TDEE plus the goal offset).

## Data model

- **`foods`** (migration 0038): `id`, `owner_id` (FK users, ON DELETE CASCADE, nullable: NULL means a starter food),
  `source` (`starter` | `mine`), `name` (<=120), `serving` (text, e.g. "1 cup cooked", <=60), `serving_g` (float,
  nullable), `calories` (float >= 0), `protein_g`, `carb_g`, `fat_g`, `fiber_g` (floats >= 0), `created_at`.
  Unique on `(owner_id, name, serving)` for a person's own foods. Starter rows are unique on `(name, serving)` and are
  loaded or refreshed at startup from `app/food/starter_foods.json` (insert missing, update changed, never delete a
  starter food that has been logged); a person can never edit or delete a starter food, only copy it into My foods.
- **`food_logs`**: `id`, `owner_id` (FK users, CASCADE), `eaten_on` (date, indexed with owner), `meal`
  (`breakfast|lunch|dinner|snack`), `food_id` (FK foods, ON DELETE SET NULL), `name` and `serving` (snapshot text),
  `servings` (float > 0, <= 50), and **snapshot** `calories`, `protein_g`, `carb_g`, `fat_g`, `fiber_g` (the food's values
  times servings, stored at log time), `created_at`. Editing or deleting a food never changes past logs.
- No new user columns: the diet type and goal are the existing `users.diet_preset`, `users.macro_goal` and the custom
  percentages.

## Targets (`app/food/targets.py`, pure functions)

- Inputs: the signed-in user's profile and latest weight. Output for a day: `calories` limit, `protein_g`, `carb_g`,
  `fat_g` (from the diet preset or the custom percentages), `fiber_g` = 14 g per 1,000 kcal (a common guideline, labeled
  as such), `tdee`, `workout_burn` (that day's net estimated workout kcal, the same number the Energy tab charts),
  and the missing-data statuses the Macros tab already has (missing profile, missing weight, missing custom macros).
- **Day totals:** sums of the day's log snapshots.
- **Potential deficit** for a day = `tdee + workout_burn - eaten`. Shown with an estimate of pounds per week if every day
  were like this one (`deficit * 7 / 3500`), labeled "estimate". A negative number is shown as a surplus.
- Macro percentages shown on the pie chart are the target split of calories (protein and carbs 4 kcal/g, fat 9 kcal/g).

## The Food tab (`/measurements?tab=food&date=YYYY-MM-DD`, default today)

1. **Header controls:** a date picker (previous / next day arrows), a **Diet type** select (Balanced, High protein, Low
   carb, Keto, Custom) and a **Goal** select (the seven `MacroGoal` choices). Changing either saves to the profile and
   reloads. Custom shows three percentage fields that must total 100 (same validation as Settings).
2. **Calorie panel:** limit, eaten, remaining (or over), TDEE, workout burn, potential deficit with the pounds-per-week
   estimate, and the existing "adjusted to a safe floor" notice when it applies.
3. **Pie chart (SVG, server-rendered with the app's `charts.py`):** the target split protein / carbs / fat for the chosen
   diet type, with grams and percent in the legend.
4. **Macro fulfilment:** one bar each for calories, protein, carbs, fat and fiber showing eaten against target, the
   percent, and a color: under (neutral), within 90-110 percent (good), over (warning). Fiber and protein use "at least"
   wording; calories and fat use "limit" wording.
5. **Log:** four meal sections (Breakfast, Lunch, Dinner, Snacks) each listing entries (name, servings, calories and
   macros) with subtotals, **Edit servings** and **Delete** per entry, and an **Add food** button per meal.
6. **Add food dialog:** a search box over the starter list and the person's own foods (search is a request to
   `/food/search?q=`), choose a result, enter servings (default 1), add. A **Create food** tab saves a new My food
   (name, serving, the five numbers) and logs it. A **Quick add** option logs a one-off entry without saving a food.
   Numbers are validated: finite, 0 or more, calories at most 5,000 and each macro at most 500 per serving, servings
   greater than 0 and at most 50.
7. **My foods** list on the same tab (collapsed section): edit and delete the person's own foods; a starter food offers
   **Copy to My foods**.

## Routes (all require sign-in; every query is scoped to the signed-in owner)

`GET /measurements?tab=food`, `POST /food/settings` (diet type, goal, custom percentages), `GET /food/search`,
`POST /food/log` (add), `POST /food/log/{id}/edit` (servings and meal), `POST /food/log/{id}/delete`,
`POST /food/foods` (create), `POST /food/foods/{id}/edit`, `POST /food/foods/{id}/delete`, `POST /food/foods/{id}/copy`.
Another person's log entry or food is 404. A starter food is read-only (edit and delete are 404/refused).

## Starter foods

About 300 common foods chosen for being simple and easy (eggs, chicken, tuna, rice, oats, bread, potatoes, beans,
yogurt, milk, cheese, common fruits and vegetables, peanut butter, lean beef, frozen and canned basics), each with one
sensible household serving and the five numbers. Source: USDA FoodData Central (SR Legacy / Foundation Foods), which is
public domain. `tools/build_starter_foods.py` builds `app/food/starter_foods.json` from the USDA data; the JSON is shipped
in the repository (public-domain nutrition numbers, no vendor or personal data).

## Backup, export, share

- New person-level backup section **Food** with tables `foods` (own rows only: `owner_id = :uid`, so starter foods are
  not backed up) and `food_logs`. Not shareable (`share_drop`), never in a Share file.
- A log whose `food_id` points at a starter food finds it again by name and serving when loaded elsewhere; if it is
  absent the id becomes empty and the log keeps its snapshot, so history is intact.
- Deleting an account removes its foods and logs (cascade, and explicit deletes in the admin delete path like other data).

## Testing

Pure-function tests for targets (each diet preset and custom, safe floor, fiber, deficit including surplus, missing
data statuses); snapshot behavior (editing or deleting a food leaves logs unchanged); validation limits; owner scoping
(another user's food and log are 404; starter foods cannot be edited or deleted); search matches starter and own foods
only; the tab renders each state; diet and goal changes persist and the pie chart follows the diet type; backup round trip
with starter-food relinking; account deletion cleanup; the migration and the starter-food loader (idempotent, preserves
logged starter foods). The vendor-name scan stays clean.

## Review Focus (input classes the tests above do not exercise on their own)

1. A day with no profile or no weight: the tab explains what is missing and still lets the person log food.
2. A food edited or deleted after it was logged: past days and totals do not move.
3. Floating-point totals: values are rounded for display only; fulfilment percent never divides by zero.
4. A very long food name, a serving of 0.1 or 50, and calories at the limits.
5. Two browsers logging at once; and the date near midnight in the person's timezone (the day uses the person's local date).
6. The old `?tab=macros` address and any link to it keep working.
