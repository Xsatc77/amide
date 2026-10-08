"""The shopping plan as plain text, for a text file or an email: the same data the dialog shows, spelled out."""

from datetime import date


def _money(n: float) -> str:
    return f"${n:,.2f}"


def _us_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{m}/{d}/{y}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _pack(line: dict) -> str:
    kind, size = line.get("pack_type"), line["pack_size"]
    if kind:
        return kind if size == 1 else f"{kind} of {size}"
    return "single" if size == 1 else f"pack of {size}"


def _order(source: dict, number: int, of: int) -> list[str]:
    where = "US warehouse" if source["warehouse"] == "us" else "China warehouse"
    out = [f"Order {number} of {of}" if of > 1 else "Order", f"Vendor: {source['vendor']}", f"{where}, price list dated {_us_date(source['list_date'])}", ""]
    for l in source["lines"]:
        left = "none left over" if not l["leftover_vials"] else f"{_plural(l['leftover_vials'], 'vial')} left over"
        out += [f"  {l['peptide']} {l['size_label']}",
                f"    Buy: {l['packs']} x {_pack(l)} ({_plural(l['vials_needed'], 'vial')} needed)",
                f"    {_money(l['per_vial'])} per vial. Cost: {_money(l['cost'])}. {left[0].upper() + left[1:]}.", ""]
    out += [f"  Items: {_money(source['items_total'])}", f"  Shipping: {_money(source['shipping'])}", f"  Order total: {_money(source['total'])}"]
    return out


def shop_text(data: dict, today: date) -> str:
    """data is the dict shop_for_protocol returns."""
    if data["status"] == "no_end_date":
        return "This protocol has no end date, so there are no course totals to shop from. Give it an end date first.\n"
    ship = data["shipping"]
    out = [f"Shopping plan: {data['protocol']['name']}", f"Made {today.strftime('%m/%d/%Y')}",
           f"Shipping fees assumed: China {_money(ship['china'])}, US {_money(ship['us'])}", ""]
    plan = data.get("plan")
    if plan is None:
        out.append("Nothing on this protocol could be found on the current price lists." if data["unshoppable"] else "There is nothing to buy for this protocol.")
    else:
        out += [plan["reason"] + ".", ""]
        for i, source in enumerate(plan["sources"], 1):
            out += _order(source, i, len(plan["sources"])) + [""]
        out += [f"Grand total: {_money(data['grand_total'] if data.get('grand_total') is not None else plan['total'])}"]
        if plan["missing"]:
            out += ["", f"Not covered by these vendors: {', '.join(plan['missing'])}"]
    if data.get("needs_volume"):
        out += ["", "Needs the vial size before it can be priced:"] + [f"  {a['name']}: {a['strength']}, sold as {a['pack_size']} {a['pack_type']}" for a in data["needs_volume"]]
    if data["unshoppable"]:
        out += ["", "Not available on the current price lists:"] + [f"  {u['name']}: {u['reason']}" for u in data["unshoppable"]]
    if data.get("bac"):
        bac = data["bac"]
        buy = bac.get("buy")
        if buy:
            where = "US warehouse" if buy["warehouse"] == "us" else "China warehouse"
            how = "its own order" if buy["mode"] == "separate" else "added to the order from this vendor"
            out += ["", f"BAC water (about {bac['ml']} mL for the course)", f"  Vendor: {buy['vendor']} ({where}), {how}",
                    f"  {buy['product']} {buy['size_label']}", f"    Buy: {buy['packs']} x {buy['pack_label']} ({_plural(buy['units'], 'bottle')})",
                    f"    Cost: {_money(buy['cost'])}" + (f". Shipping: {_money(buy['shipping'])}" if buy["shipping"] else "") + f". Total: {_money(buy['extra'])}"]
        else:
            out += ["", f"No BAC water brand you rank is on the current price lists. This course uses about {bac['ml']} mL: {_plural(bac['bottles'], 'bottle')} of 30 mL. Buy it separately."]
    out += ["", "Prices come from each vendor's newest price list, with a 5% buffer on the total dose. Confirm with the vendor before ordering."]
    return "\n".join(out) + "\n"
